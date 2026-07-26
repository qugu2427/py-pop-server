"""Functions to manage POP3 client session"""

import time
import tempfile
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
    MAX_LINE_LENGTH,
    RES_ALREADY_AUTHENTICATED,
    RES_AUTH_REQUIRED,
    RES_AUTHENTICATED,
    RES_CAPA,
    RES_GOODBYE,
    RES_INTERNAL_ERROR,
    RES_INVALID_CREDS,
    RES_LINE_TOO_LONG,
    RES_LOGIN_DELAY,
    RES_MARK_DELETE,
    RES_NO_SUCH_ITEM,
    RES_NO_USER,
    RES_NOOP,
    RES_RESET,
    RES_SYNTAX_ERR,
    RES_UNHANDLED_CMD,
    RES_UPDATE_FAILED,
    RES_USER_ACCEPTED,
    PopConfig,
    PopError,
    PopListItem,
)


class PopSession:  # pylint: disable=too-few-public-methods,too-many-instance-attributes
    """Pop client session"""

    def __init__(
        self,
        writer,
        cfg: PopConfig,
        login_attempts: dict[str, float] | None = None,
    ):
        self.writer = writer
        self.cfg = cfg
        self.login_attempts = login_attempts if login_attempts is not None else {}
        self.username: str | None = None
        self.is_authenticated: bool = False
        self.deleted_uids: t.List[str] = []
        self.mailbox_list: t.Sequence[PopListItem] | None = None
        self.last_chunk_part: bytes = b""

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
            if item.uid in self.deleted_uids:
                continue
            stat[0] += 1
            stat[1] += item.size
        return (stat[0], stat[1])

    def _get_item(self, item_id: int) -> PopListItem | None:
        assert self.mailbox_list is not None
        for i, item in enumerate(self.mailbox_list):
            if i + 1 == item_id and item.uid not in self.deleted_uids:
                return item
        return None

    async def _write_string(self, text: str):
        self.writer.write(text.encode())
        await self.writer.drain()

    async def _write_bytes(self, text: bytes):
        self.writer.write(text)
        await self.writer.drain()

    async def _iter_message_lines(self, reader) -> t.AsyncIterator[bytes]:
        """Yield message lines while accepting CRLF, CR, or LF input endings."""

        line = bytearray()
        pending_cr = False
        while True:
            chunk = await reader.read(BUFFER_SIZE)
            if not chunk:
                break
            for byte in chunk:
                if pending_cr:
                    yield bytes(line)
                    line.clear()
                    pending_cr = False
                    if byte == 10:
                        continue
                if byte == 13:
                    pending_cr = True
                elif byte == 10:
                    yield bytes(line)
                    line.clear()
                else:
                    line.append(byte)
        if pending_cr or line:
            yield bytes(line)

    @staticmethod
    def _encode_message_line(line: bytes) -> bytes:
        """Encode one dot-transparent POP3 multiline-response line."""

        if line.startswith(b"."):
            line = b"." + line
        return line + b"\r\n"

    async def _write_staged_response(
        self, response: tempfile.SpooledTemporaryFile[bytes]
    ) -> None:
        """Write a complete multiline response after it has been staged."""

        await self._write_bytes(b"+OK\r\n")
        response.seek(0)
        while chunk := response.read(BUFFER_SIZE):
            await self._write_bytes(chunk)

    async def _handle_quit_cmd(self) -> bool:
        if len(self.deleted_uids) > 0 and self.username is not None:
            try:
                await self.cfg.delete_items(self.username, self.deleted_uids)
            except PopError:
                raise
            except Exception:
                await self._write_bytes(RES_UPDATE_FAILED)
                return False
        await self._write_bytes(RES_GOODBYE)
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
            reader = await self.cfg.get_item_reader(self.username, item.uid)
            with tempfile.SpooledTemporaryFile(max_size=BUFFER_SIZE * 1024) as response:
                async for line in self._iter_message_lines(reader):
                    response.write(self._encode_message_line(line))
                response.write(b".\r\n")
                await self._write_staged_response(response)

    async def _handle_list_cmd(self, item_id: PositiveInt | None) -> None:
        self._assert_authenticated()
        assert self.mailbox_list is not None
        if item_id is None:
            joined_list = "\r\n".join(
                [
                    f"{i + 1} {item.size}"
                    for i, item in enumerate(self.mailbox_list)
                    if item.uid not in self.deleted_uids
                ]
            )
            await self._write_string(f"+OK\r\n{joined_list}\r\n.\r\n")
        else:
            for i, item in enumerate(self.mailbox_list):
                if i + 1 == item_id and item.uid not in self.deleted_uids:
                    await self._write_string(f"+OK {i + 1} {item.size}\r\n")
                    return
            await self._write_bytes(RES_NO_SUCH_ITEM)

    async def _handle_uidl_cmd(self, item_id: PositiveInt | None) -> None:
        self._assert_authenticated()
        assert self.mailbox_list is not None
        if item_id is None:
            joined_list = "\r\n".join(
                [
                    f"{i + 1} {item.uid}"
                    for i, item in enumerate(self.mailbox_list)
                    if item.uid not in self.deleted_uids
                ]
            )
            await self._write_string(f"+OK\r\n{joined_list}\r\n.\r\n")
        else:
            for i, item in enumerate(self.mailbox_list):
                if i + 1 == item_id and item.uid not in self.deleted_uids:
                    await self._write_string(f"+OK {i + 1} {item.uid}\r\n")
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
        elif time.monotonic() - self.login_attempts.get(self.username, 0) < LOGIN_DELAY:
            await self._write_bytes(RES_LOGIN_DELAY)
        else:
            now = time.monotonic()
            expired_users = [
                user
                for user, attempted_at in self.login_attempts.items()
                if now - attempted_at >= LOGIN_DELAY
            ]
            for user in expired_users:
                self.login_attempts.pop(user)
            self.login_attempts[self.username] = now
            if await self.cfg.validate_credentials(self.username, password):
                self.login_attempts.pop(self.username, None)
                await self._load_mailbox_list()
                self.is_authenticated = True
                await self._write_bytes(RES_AUTHENTICATED)
            else:
                await self._write_bytes(RES_INVALID_CREDS)

    async def _handle_top_cmd(  # pylint: disable=too-many-branches
        self,
        item_id: PositiveInt,
        lines: int,
    ) -> None:
        self._assert_authenticated()
        assert self.username is not None
        item = self._get_item(item_id)
        if item is None:
            await self._write_bytes(RES_NO_SUCH_ITEM)
        else:
            reader = await self.cfg.get_item_reader(self.username, item.uid)
            in_body = False
            with tempfile.SpooledTemporaryFile(max_size=BUFFER_SIZE * 1024) as response:
                async for line in self._iter_message_lines(reader):
                    if in_body:
                        if lines == 0:
                            break
                        lines -= 1
                    elif line == b"":
                        in_body = True
                    response.write(self._encode_message_line(line))
                response.write(b".\r\n")
                await self._write_staged_response(response)

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
                self._assert_authenticated()
                await self._write_bytes(RES_NOOP)
            case RsetCmd():
                self._assert_authenticated()
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
        if len(line) > MAX_LINE_LENGTH:
            await self._write_bytes(RES_LINE_TOO_LONG)
            return True

        try:
            cmd = parse_msg_to_cmd(line)
            return await self._handle_cmd(cmd)
        except PopError as exc:
            await self._write_bytes(exc.message)
            return True
        except ValidationError:
            await self._write_bytes(RES_SYNTAX_ERR)
            return True
        except Exception:
            await self._write_bytes(RES_INTERNAL_ERROR)
            return True

    async def handle_chunk(self, chunk: bytes) -> bool:
        """Handles a chunk (.i.e arbitrary fraction or number of lines)."""

        lines = chunk.split(b"\r\n")
        lines[0] = self.last_chunk_part + lines[0]
        self.last_chunk_part = lines.pop()
        if len(self.last_chunk_part) > MAX_LINE_LENGTH:
            await self._write_bytes(RES_LINE_TOO_LONG)
            self.last_chunk_part = b""
            return False
        for line in lines:
            if not await self._handle_line(line):
                return False
        return True
