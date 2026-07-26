# pylint: disable=redefined-outer-name
"""Tests various POP3 sequences against dummy server."""

import asyncio as aio
import hashlib
import typing as t
from pathlib import Path

import pytest
import pytest_asyncio

from pypop.server import get_client_handler
from pypop.session import PopSession
from pypop.types import (
    BUFFER_SIZE,
    LOGIN_DELAY,
    MAX_LINE_LENGTH,
    RES_AUTH_REQUIRED,
    RES_AUTHENTICATED,
    RES_CAPA,
    RES_EMPTY_LINE,
    RES_GOODBYE,
    RES_INTERNAL_ERROR,
    RES_INVALID_CREDS,
    RES_LINE_TOO_LONG,
    RES_LOGIN_DELAY,
    RES_MARK_DELETE,
    RES_NO_SUCH_ITEM,
    RES_NOOP,
    RES_READY,
    RES_RESET,
    RES_SYNTAX_ERR,
    RES_UPDATE_FAILED,
    RES_USER_ACCEPTED,
    PopConfig,
    PopError,
    PopListItem,
    PopReader,
)

TEST_DIR = Path(__file__).parent

TEST_EMAILS = [
    b"""From: bob@email.com\r
To: alice@email.com\r
Subject: Hello\r
\r
Hello,\r
\r
How are you,\r
\r
Best,\r
Bob"""
]
DELETED_ITEMS: t.List[str] = []


class MailReader(PopReader):  # pylint: disable=too-few-public-methods
    """
    Example mail reader, just reads a string like it's a file"
    """

    def __init__(self, mail: bytes):
        self.mail = mail

    async def read(self, n: int = -1) -> bytes:
        chunk = self.mail[:n]
        self.mail = self.mail[n:]
        return chunk


class FragmentReader(PopReader):
    """Reader that returns deliberately irregular chunks."""

    def __init__(self, chunks: t.Sequence[bytes]):
        self.chunks = list(chunks)

    async def read(self, n: int = -1) -> bytes:  # pylint: disable=unused-argument
        return self.chunks.pop(0) if self.chunks else b""


class BufferWriter:
    """Minimal stream writer for session-level tests."""

    def __init__(self):
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        pass


def get_uid(email: bytes) -> str:
    """Test get uid"""

    return hashlib.md5(email).hexdigest()


async def validate_credentials(
    username: str,
    password: str,
    /,  # pylint: disable=unused-argument
) -> bool:
    """Test validate credentials"""

    if username == "admin" and password == "password":
        return True
    return False


async def get_mailbox_list(
    username: str,
    /,  # pylint: disable=unused-argument
) -> t.Sequence[PopListItem]:
    """Test get mailbox list"""

    results = []
    for email in TEST_EMAILS:
        pop_item = PopListItem(
            uid=get_uid(email),
            size=len(email),
        )
        results.append(pop_item)
    return results


async def get_item_reader(
    username: str,
    uid: str,
    /,  # pylint: disable=unused-argument
) -> PopReader:
    """Test get mail item reader"""

    for email in TEST_EMAILS:
        if uid == get_uid(email):
            return MailReader(email)
    raise PopError(b"-ERR No such uid\r\n")


async def delete_items(
    username: str,
    uids: t.Sequence[str],
    /,  # pylint: disable=unused-argument
) -> None:
    """Test delete items"""

    DELETED_ITEMS.extend(uids)


HOST = "127.0.0.1"


async def try_sequence(
    sequence: t.Sequence[t.Tuple[bytes, bytes] | t.Tuple[bytes, bytes, int]],
    port: int,
) -> str | None:
    """
    Try a sequence of commands against a test server
    """

    reader, writer = await aio.open_connection(HOST, port)

    for msg in sequence:
        if len(msg) == 3:
            await aio.sleep(msg[2])
        if msg[0] is not None:
            writer.write(msg[0])
            await writer.drain()
        try:
            if msg[1]:
                data = await aio.wait_for(reader.readexactly(len(msg[1])), timeout=1.0)
            else:
                data = await aio.wait_for(reader.read(BUFFER_SIZE), timeout=1.0)
            if not data == msg[1]:
                return f"Expected {msg[1]!r}, got {data!r}"
        except aio.TimeoutError:
            if msg[1] is None:
                continue
            return f"Expected {msg[1]!r}, got timeout"

    writer.close()
    await writer.wait_closed()
    return None


@pytest_asyncio.fixture
async def start_test_listener():
    """Starts test listener"""

    cfg = PopConfig(
        host=HOST,
        port=0,
        validate_credentials=validate_credentials,
        get_mailbox_list=get_mailbox_list,
        get_item_reader=get_item_reader,
        delete_items=delete_items,
        debug=True,
    )

    server = await aio.start_server(get_client_handler(cfg), cfg.host, cfg.port)
    port = server.sockets[0].getsockname()[1]
    async with server:
        yield port


@pytest.mark.asyncio
async def test_basic(start_test_listener):
    """Tests a basic sequence"""

    stat_count = 0
    stat_size = 0
    list_items = []
    uidl_items = []
    for i, email in enumerate(TEST_EMAILS):
        assert email
        stat_count += 1
        stat_size += len(email)
        list_items.append(f"{i + 1} {len(email)}")
        uidl_items.append(f"{i + 1} {get_uid(email)}")
    sequence = [
        (None, RES_READY),
        (b"CAPA\r\n", RES_CAPA),
        (b"USER admin\r\n", RES_USER_ACCEPTED),
        (b"PASS password\r\n", RES_AUTHENTICATED),
        (b"STAT\r\n", f"+OK {stat_count} {stat_size}\r\n".encode()),
        (b"LIST\r\n", f"+OK\r\n{'\r\n'.join(list_items)}\r\n.\r\n".encode()),
        (b"LIST 1\r\n", f"+OK {list_items[0]}\r\n".encode()),
        (b"UIDL\r\n", f"+OK\r\n{'\r\n'.join(uidl_items)}\r\n.\r\n".encode()),
        (b"UIDL 1\r\n", f"+OK {uidl_items[0]}\r\n".encode()),
        (b"RETR 1\r\n", f"+OK\r\n{TEST_EMAILS[0].decode()}\r\n.\r\n".encode()),
        (b"QUIT\r\n", RES_GOODBYE),
    ]
    result = await try_sequence(sequence, start_test_listener)
    assert result is None


@pytest.mark.asyncio
async def test_top(start_test_listener):
    """Tests various uses of TOP command"""

    email_split = TEST_EMAILS[0].decode().split("\r\n\r\n", 1)
    email_body_split = email_split[1].split("\r\n")
    sequence = [
        (None, RES_READY),
        (b"USER admin\r\n", RES_USER_ACCEPTED),
        (b"PASS password\r\n", RES_AUTHENTICATED),
        (b"TOP 1 0\r\n", f"+OK\r\n{email_split[0]}\r\n\r\n.\r\n".encode()),
        (
            b"TOP 1 1\r\n",
            f"+OK\r\n{email_split[0]}\r\n\r\n{'\r\n'.join(email_body_split[:1])}\r\n.\r\n".encode(),
        ),
        (
            b"TOP 1 3\r\n",
            f"+OK\r\n{email_split[0]}\r\n\r\n{'\r\n'.join(email_body_split[:3])}\r\n.\r\n".encode(),
        ),
        (
            b"TOP 1 100\r\n",
            f"+OK\r\n{email_split[0]}\r\n\r\n{'\r\n'.join(email_body_split[:100])}\r\n.\r\n".encode(),
        ),
        (b"QUIT\r\n", RES_GOODBYE),
    ]
    result = await try_sequence(sequence, start_test_listener)
    assert result is None


@pytest.mark.asyncio
async def test_login(start_test_listener):
    """Tests login and auth"""

    sequence = [
        (None, RES_READY),
        (b"STAT\r\n", RES_AUTH_REQUIRED),
        (b"LIST\r\n", RES_AUTH_REQUIRED),
        (b"LIST 1\r\n", RES_AUTH_REQUIRED),
        (b"UIDL\r\n", RES_AUTH_REQUIRED),
        (b"UIDL 1\r\n", RES_AUTH_REQUIRED),
        (b"RETR 1\r\n", RES_AUTH_REQUIRED),
        (b"TOP 1 0\r\n", RES_AUTH_REQUIRED),
        (b"LIST 1\r\n", RES_AUTH_REQUIRED),
        (b"USER adminz\r\n", RES_USER_ACCEPTED),
        (b"PASS password\r\n", RES_INVALID_CREDS),
        (b"USER admin\r\n", RES_USER_ACCEPTED),
        (b"PASS toofast\r\n", RES_INVALID_CREDS),
        (b"PASS password\r\n", RES_LOGIN_DELAY),
        (b"PASS bad\r\n", RES_INVALID_CREDS, LOGIN_DELAY + 1),
        (b"PASS password\r\n", RES_AUTHENTICATED, LOGIN_DELAY + 1),
        (b"QUIT\r\n", RES_GOODBYE),
    ]
    result = await try_sequence(sequence, start_test_listener)
    assert result is None


@pytest.mark.asyncio
async def test_invalid_commands(start_test_listener):
    """Tests invalid commands"""

    sequence = [
        (None, RES_READY),
        (b"USER admin\r\n", RES_USER_ACCEPTED),
        (b"PASS password\r\n", RES_AUTHENTICATED),
        (b"\r\n", RES_EMPTY_LINE),
        (b"FOO 1 2 3\r\n", RES_SYNTAX_ERR),
        (b"LIST 9999999\r\n", RES_NO_SUCH_ITEM),
        (b"LIST foobar\r\n", RES_SYNTAX_ERR),
        (b"UIDL 9999999\r\n", RES_NO_SUCH_ITEM),
        (b"UIDL foobar\r\n", RES_SYNTAX_ERR),
        (b"TOP 100 100\r\n", RES_NO_SUCH_ITEM),
        (b"TOP foo bar\r\n", RES_SYNTAX_ERR),
        (b"DELE 100\r\n", RES_NO_SUCH_ITEM),
        (b"A" * (MAX_LINE_LENGTH + 1) + b"\r\n", RES_LINE_TOO_LONG),
        (b"QUIT\r\n", RES_GOODBYE),
    ]
    result = await try_sequence(sequence, start_test_listener)
    assert result is None


@pytest.mark.asyncio
async def test_pipelining(start_test_listener):
    """Tests pipelining"""

    sequence = [
        (None, RES_READY),
        (b"USER a", None),
        (b"dm", None),
        (b"in", None),
        (b"\r\n", RES_USER_ACCEPTED),
        (
            b"PASS password\r\nNOOP\r\nNOOP\r\nN",
            RES_AUTHENTICATED + RES_NOOP + RES_NOOP,
        ),
        (b"OOP\r\nQUIT\r\nNOOP\r\n", RES_NOOP + RES_GOODBYE),
    ]
    result = await try_sequence(sequence, start_test_listener)
    assert result is None


@pytest.mark.asyncio
async def test_command_timeout():
    """Closes connections which do not send command data in time."""

    cfg = PopConfig(
        host=HOST,
        port=0,
        command_timeout=1,
        validate_credentials=validate_credentials,
        get_mailbox_list=get_mailbox_list,
        get_item_reader=get_item_reader,
        delete_items=delete_items,
    )
    server = await aio.start_server(get_client_handler(cfg), cfg.host, cfg.port)
    port = server.sockets[0].getsockname()[1]

    async with server:
        sequence = [
            (None, RES_READY),
            (None, b"", 2),
        ]
        result = await try_sequence(sequence, port=port)
        assert result is None


@pytest.mark.asyncio
async def test_command_timeout_resets_after_complete_command():
    """A partial next command gets a fresh deadline after a complete command."""

    cfg = PopConfig(
        host=HOST,
        port=0,
        command_timeout=0.2,
        validate_credentials=validate_credentials,
        get_mailbox_list=get_mailbox_list,
        get_item_reader=get_item_reader,
        delete_items=delete_items,
    )
    server = await aio.start_server(get_client_handler(cfg), cfg.host, cfg.port)
    port = server.sockets[0].getsockname()[1]

    async with server:
        reader, writer = await aio.open_connection(HOST, port)
        assert await reader.read(BUFFER_SIZE) == RES_READY
        await aio.sleep(0.15)
        writer.write(b"USER admin\r\nN")
        await writer.drain()
        assert await reader.read(BUFFER_SIZE) == RES_USER_ACCEPTED
        await aio.sleep(0.15)
        writer.write(b"OOP\r\n")
        await writer.drain()
        assert await reader.read(BUFFER_SIZE) == RES_AUTH_REQUIRED
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_delete(start_test_listener):
    """Tests login and auth"""

    DELETED_ITEMS.clear()
    sequence = [
        (None, RES_READY),
        (b"USER admin\r\n", RES_USER_ACCEPTED),
        (b"PASS password\r\n", RES_AUTHENTICATED),
        (b"DELE 1\r\n", RES_MARK_DELETE),
        (b"STAT\r\n", b"+OK 0 0\r\n"),
        (b"LIST\r\n", b"+OK\r\n\r\n.\r\n"),
        (b"LIST 1\r\n", RES_NO_SUCH_ITEM),
        (b"UIDL\r\n", b"+OK\r\n\r\n.\r\n"),
        (b"UIDL 1\r\n", RES_NO_SUCH_ITEM),
        (b"RETR 1\r\n", RES_NO_SUCH_ITEM),
        (b"TOP 1 1\r\n", RES_NO_SUCH_ITEM),
        (b"DELE 1\r\n", RES_NO_SUCH_ITEM),
        (b"RSET\r\n", RES_RESET),
        (b"STAT\r\n", f"+OK 1 {len(TEST_EMAILS[0])}\r\n".encode()),
        (b"DELE 1\r\n", RES_MARK_DELETE),
        (b"QUIT\r\n", RES_GOODBYE),
    ]
    result = await try_sequence(sequence, start_test_listener)
    assert result is None
    assert DELETED_ITEMS == [get_uid(TEST_EMAILS[0])]


@pytest.mark.asyncio
async def test_retr_and_top_frame_fragmented_messages():
    """RETR and TOP normalize line endings and dot-stuff message lines."""

    chunks = [b"Header: value\r", b"\n\r\n.one\r\n", b".\r", b"\nlast"]

    async def fragmented_reader(
        username: str,
        uid: str,  # pylint: disable=unused-argument
    ) -> PopReader:
        return FragmentReader(chunks)

    cfg = PopConfig(
        host=HOST,
        port=0,
        validate_credentials=validate_credentials,
        get_mailbox_list=get_mailbox_list,
        get_item_reader=fragmented_reader,
        delete_items=delete_items,
    )

    retr_writer = BufferWriter()
    retr_session = PopSession(retr_writer, cfg)
    retr_session.username = "admin"
    retr_session.is_authenticated = True
    retr_session.mailbox_list = [PopListItem(uid="uid", size=1)]
    await retr_session._handle_retr_cmd(1)
    assert bytes(retr_writer.data) == (
        b"+OK\r\nHeader: value\r\n\r\n..one\r\n..\r\nlast\r\n.\r\n"
    )

    top_writer = BufferWriter()
    top_session = PopSession(top_writer, cfg)
    top_session.username = "admin"
    top_session.is_authenticated = True
    top_session.mailbox_list = [PopListItem(uid="uid", size=1)]
    await top_session._handle_top_cmd(1, 2)
    assert bytes(top_writer.data) == (
        b"+OK\r\nHeader: value\r\n\r\n..one\r\n..\r\n.\r\n"
    )


@pytest.mark.asyncio
async def test_login_delay_is_shared_across_connections():
    """Reconnects cannot bypass the per-user login delay."""

    attempts: dict[str, float] = {}
    first_writer = BufferWriter()
    first = PopSession(first_writer, _test_config(), attempts)
    first.username = "admin"
    await first._handle_pass_cmd("bad")
    assert bytes(first_writer.data) == RES_INVALID_CREDS

    second_writer = BufferWriter()
    second = PopSession(second_writer, _test_config(), attempts)
    second.username = "admin"
    await second._handle_pass_cmd("password")
    assert bytes(second_writer.data) == RES_LOGIN_DELAY


@pytest.mark.asyncio
async def test_noop_and_rset_require_authentication():
    """Transaction commands are rejected during authorization."""

    writer = BufferWriter()
    session = PopSession(writer, _test_config())
    await session._handle_line(b"NOOP")
    await session._handle_line(b"RSET")
    assert bytes(writer.data) == RES_AUTH_REQUIRED + RES_AUTH_REQUIRED


@pytest.mark.asyncio
async def test_quit_reports_mailbox_update_failure():
    """QUIT does not report success when deletion fails."""

    async def fail_delete(
        username: str,
        uids: t.Sequence[str],  # pylint: disable=unused-argument
    ) -> None:
        raise RuntimeError("storage unavailable")

    cfg = _test_config(delete_fn=fail_delete)
    writer = BufferWriter()
    session = PopSession(writer, cfg)
    session.username = "admin"
    session.is_authenticated = True
    session.deleted_uids = ["uid"]
    assert not await session._handle_quit_cmd()
    assert bytes(writer.data) == RES_UPDATE_FAILED


@pytest.mark.asyncio
async def test_unexpected_callback_failure_gets_error_response():
    """Unexpected application callback errors produce a POP3 error."""

    async def fail_validation(
        username: str,
        password: str,  # pylint: disable=unused-argument
    ) -> bool:
        raise RuntimeError("authentication backend unavailable")

    cfg = _test_config().model_copy(update={"validate_credentials": fail_validation})
    writer = BufferWriter()
    session = PopSession(writer, cfg)
    session.username = "admin"
    assert await session._handle_line(b"PASS password")
    assert bytes(writer.data) == RES_INTERNAL_ERROR


@pytest.mark.asyncio
async def test_mailbox_failure_does_not_authenticate_session():
    """Authentication is committed only after mailbox loading succeeds."""

    mailbox_error = b"-ERR Mailbox unavailable\r\n"

    async def fail_mailbox(
        username: str,  # pylint: disable=unused-argument
    ) -> t.Sequence[PopListItem]:
        raise PopError(mailbox_error)

    cfg = _test_config().model_copy(update={"get_mailbox_list": fail_mailbox})
    writer = BufferWriter()
    session = PopSession(writer, cfg)
    session.username = "admin"
    assert await session._handle_line(b"PASS password")
    assert bytes(writer.data) == mailbox_error
    assert not session.is_authenticated
    assert session.mailbox_list is None


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [b"RETR 1", b"TOP 1 1"])
async def test_reader_acquisition_failure_has_no_success_prefix(command: bytes):
    """Reader acquisition errors are sent before any positive response."""

    reader_error = b"-ERR Message unavailable\r\n"

    async def fail_reader(
        username: str,
        uid: str,  # pylint: disable=unused-argument
    ) -> PopReader:
        raise PopError(reader_error)

    cfg = _test_config().model_copy(update={"get_item_reader": fail_reader})
    writer = BufferWriter()
    session = PopSession(writer, cfg)
    session.username = "admin"
    session.is_authenticated = True
    session.mailbox_list = [PopListItem(uid="uid", size=1)]
    assert await session._handle_line(command)
    assert bytes(writer.data) == reader_error


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [b"RETR 1", b"TOP 1 1"])
async def test_midstream_reader_failure_has_no_partial_response(command: bytes):
    """Reader errors during staging produce one complete error response."""

    class FailingReader(PopReader):
        def __init__(self):
            self.read_count = 0

        async def read(self, n: int = -1) -> bytes:
            self.read_count += 1
            if self.read_count == 1:
                return b"Header: value\r\n\r\nbody\r\n"
            raise RuntimeError("message backend unavailable")

    async def failing_reader(
        username: str,
        uid: str,  # pylint: disable=unused-argument
    ) -> PopReader:
        return FailingReader()

    cfg = _test_config().model_copy(update={"get_item_reader": failing_reader})
    writer = BufferWriter()
    session = PopSession(writer, cfg)
    session.username = "admin"
    session.is_authenticated = True
    session.mailbox_list = [PopListItem(uid="uid", size=1)]
    assert await session._handle_line(command)
    assert bytes(writer.data) == RES_INTERNAL_ERROR


def _test_config(
    delete_fn: t.Callable[[str, t.Sequence[str]], t.Awaitable[None]] = delete_items,
) -> PopConfig:
    """Build a configuration for session-level tests."""

    return PopConfig(
        host=HOST,
        port=0,
        validate_credentials=validate_credentials,
        get_mailbox_list=get_mailbox_list,
        get_item_reader=get_item_reader,
        delete_items=delete_fn,
    )
