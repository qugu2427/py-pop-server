"""Functions to manage POP3 client session"""

import time
import typing as t

from pydantic import PositiveInt, ValidationError

from pypop.command import (
    CapaCmd,
    DeleCmd,
    ListCmd,
    NoopCmd,
    PassCmd,
    PopCmd,
    QuitCmd,
    RetrCmd,
    RsetCmd,
    StatCmd,
    TopCmd,
    UidlCmd,
    UserCmd,
    parse_msg_to_cmd,
)
from pypop.types import (
    BUFFER_SIZE,
    LOGIN_DELAY,
    RES_ALREADY_AUTHENTICATED,
    RES_AUTH_REQUIRED,
    RES_AUTHENTICATED,
    RES_CAPA,
    RES_GOODBYE,
    RES_INVALID_CREDS,
    RES_LOGIN_DELAY,
    RES_MARK_DELETE,
    RES_NO_SUCH_ITEM,
    RES_NO_USER,
    RES_NOOP,
    RES_RESET,
    RES_SYNTAX_ERR,
    RES_UNHANDLED_CMD,
    RES_USER_ACCEPTED,
    PopConfig,
    PopError,
    PopListItem,
)


class PopSession:  # pylint: disable=too-few-public-methods,too-many-instance-attributes
    """Pop client session"""

    def __init__(self, writer, cfg: PopConfig):
        self.writer = writer
        self.cfg = cfg
        self.username: str | None = None
        self.is_authenticated: bool = False
        self.deleted_uids: t.List[str] = []
        self.mailbox_list: t.Sequence[PopListItem] | None = None
        self.last_chunk_part: bytes = b""
        self.last_login_timestamp: int = 0

    async def _load_mailbox_list(self):
        if self.mailbox_list is None:
            self.mailbox_list = await self.cfg.get_mailbox_list(self.username)

    def _assert_authenticated(self):
        if not self.is_authenticated:
            raise PopError(RES_AUTH_REQUIRED)

    def _calc_stat(self) -> t.Tuple[int, int]:
        assert self.mailbox_list is not None
        stat = [0, 0]
        for item in self.mailbox_list:
            stat[0] += 1
            stat[1] += item.size
        return (stat[0], stat[1])

    def _get_item(self, item_id: int) -> PopListItem | None:
        assert self.mailbox_list is not None
        for i, item in enumerate(self.mailbox_list):
            if i + 1 == item_id:
                return item
        return None

    async def _write_string(self, text: str):
        self.writer.write(text.encode())
        await self.writer.drain()

    async def _write_bytes(self, text: bytes):
        self.writer.write(text)
        await self.writer.drain()

    async def _handle_quit_cmd(self) -> bool:
        await self._write_bytes(RES_GOODBYE)
        if len(self.deleted_uids) > 0 and self.username is not None:
            self.cfg.delete_items(self.username, self.deleted_uids)
        return False

    async def _handle_stat_cmd(self) -> None:
        self._assert_authenticated()
        stat = self._calc_stat()
        await self._write_string(f"+OK {stat[0]} {stat[1]}\r\n")

    async def _handle_retr_cmd(self, item_id: PositiveInt) -> None:
        self._assert_authenticated()
        assert self.username is not None
        item = self._get_item(item_id)
        if item is None:
            await self._write_bytes(RES_NO_SUCH_ITEM)
        else:
            keep_reading = True
            await self._write_bytes(b"+OK\r\n")
            reader = await self.cfg.get_item_reader(self.username, item.uid)
            while keep_reading:
                chunk = await reader.read(BUFFER_SIZE)
                if len(chunk) < BUFFER_SIZE:
                    keep_reading = False
                    if not chunk.endswith(b"\r\n"):
                        chunk += b"\r\n"
                    if not chunk.endswith(b".\r\n"):
                        chunk += b".\r\n"
                await self._write_bytes(chunk)

    async def _handle_list_cmd(self, item_id: PositiveInt | None) -> None:
        self._assert_authenticated()
        assert self.mailbox_list is not None
        if item_id is None:
            joined_list = "\r\n".join(
                [f"{i+1} {item.size}" for i, item in enumerate(self.mailbox_list)]
            )
            await self._write_string(f"+OK\r\n{joined_list}\r\n.\r\n")
        else:
            for i, item in enumerate(self.mailbox_list):
                if i + 1 == item_id:
                    await self._write_string(f"+OK {i+1} {item.size}\r\n")
                    return
            await self._write_bytes(RES_NO_SUCH_ITEM)

    async def _handle_uidl_cmd(self, item_id: PositiveInt | None) -> None:
        self._assert_authenticated()
        assert self.mailbox_list is not None
        if item_id is None:
            joined_list = "\r\n".join(
                [f"{i+1} {item.uid}" for i, item in enumerate(self.mailbox_list)]
            )
            await self._write_string(f"+OK\r\n{joined_list}\r\n.\r\n")
        else:
            for i, item in enumerate(self.mailbox_list):
                if i + 1 == item_id:
                    await self._write_string(f"+OK {i+1} {item.uid}\r\n")
                    return
            await self._write_bytes(RES_NO_SUCH_ITEM)

    async def _handle_dele_cmd(self, item_id: PositiveInt) -> None:
        self._assert_authenticated()
        item = self._get_item(item_id)
        if item is None:
            await self._write_bytes(RES_NO_SUCH_ITEM)
        else:
            self.deleted_uids.append(item.uid)
            await self._write_bytes(RES_MARK_DELETE)

    async def _handle_user_cmd(self, user: str) -> None:
        if self.is_authenticated:
            await self._write_bytes(RES_ALREADY_AUTHENTICATED)
        else:
            self.username = user
            await self._write_bytes(RES_USER_ACCEPTED)

    async def _handle_pass_cmd(self, password: str) -> None:
        if self.is_authenticated:
            await self._write_bytes(RES_ALREADY_AUTHENTICATED)
        elif self.username is None:
            await self._write_bytes(RES_NO_USER)
        elif time.time() - self.last_login_timestamp < LOGIN_DELAY:
            print(time.time() - self.last_login_timestamp)
            await self._write_bytes(RES_LOGIN_DELAY)
        elif await self.cfg.validate_credentials(self.username, password):
            self.is_authenticated = True
            await self._load_mailbox_list()
            await self._write_bytes(RES_AUTHENTICATED)
        else:
            self.last_login_timestamp = int(time.time())
            await self._write_bytes(RES_INVALID_CREDS)

    async def _handle_top_cmd(  # pylint: disable=too-many-branches
        self,
        item_id: PositiveInt,
        lines: PositiveInt,
    ) -> None:
        self._assert_authenticated()
        assert self.username is not None
        item = self._get_item(item_id)
        if item is None:
            await self._write_bytes(RES_NO_SUCH_ITEM)
        else:
            keep_reading = True
            reader = await self.cfg.get_item_reader(self.username, item.uid)
            last_chunk_part = b""
            in_body = False
            while keep_reading:
                chunk = await reader.read(BUFFER_SIZE)
                if len(chunk) < BUFFER_SIZE:
                    if not chunk.endswith(b"\r\n"):  # pylint: disable=too-many-branches
                        chunk += b"\r\n"
                    if not chunk.endswith(b".\r\n"):
                        chunk += b".\r\n"
                    keep_reading = False
                chunk_lines = (
                    chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
                )
                chunk_lines[0] = last_chunk_part + chunk_lines[0]
                last_chunk_part = chunk_lines.pop()
                out_chunk = b""
                for line in chunk_lines:
                    if not in_body:
                        if line == b"":
                            in_body = True
                        out_chunk += line + b"\r\n"
                    elif lines > 0:
                        out_chunk += line + b"\r\n"
                        lines -= 1
                    else:
                        if not out_chunk.endswith(b"\r\n"):
                            out_chunk += b"\r\n"
                        if not out_chunk.endswith(b".\r\n"):
                            out_chunk += b".\r\n"
                        keep_reading = False
                        break
                await self._write_bytes(out_chunk)

    async def _handle_cmd(  # pylint: disable=too-many-branches
        self, cmd: PopCmd
    ) -> bool:
        match cmd:
            case QuitCmd():
                return await self._handle_quit_cmd()
            case StatCmd():
                await self._handle_stat_cmd()
            case RetrCmd():
                await self._handle_retr_cmd(cmd.id)
            case ListCmd():
                await self._handle_list_cmd(cmd.id)
            case UidlCmd():
                await self._handle_uidl_cmd(cmd.id)
            case DeleCmd():
                await self._handle_dele_cmd(cmd.id)
            case NoopCmd():
                await self._write_bytes(RES_NOOP)
            case RsetCmd():
                self.deleted_uids = []
                await self._write_bytes(RES_RESET)
            case UserCmd():
                await self._handle_user_cmd(cmd.user)
            case PassCmd():
                await self._handle_pass_cmd(cmd.password)
            case TopCmd():
                await self._handle_top_cmd(cmd.id, cmd.lines)
            case CapaCmd():
                await self._write_bytes(RES_CAPA)
            case _:
                await self._write_bytes(RES_UNHANDLED_CMD)
        return True

    async def _handle_line(self, line: bytes) -> bool:
        try:
            cmd = parse_msg_to_cmd(line)
            return await self._handle_cmd(cmd)
        except PopError as exc:
            await self._write_bytes(exc.message)
            return True
        except ValidationError:
            await self._write_bytes(RES_SYNTAX_ERR)
            return True
        except Exception as exc:
            raise exc

    async def handle_chunk(self, chunk: bytes) -> bool:
        """Handles a chunk (.i.e arbitrary fraction or number of lines)."""

        lines = chunk.split(b"\r\n")
        lines[0] = self.last_chunk_part + lines[0]
        self.last_chunk_part = lines.pop()
        for line in lines:
            if not await self._handle_line(line):
                return False
        return True
