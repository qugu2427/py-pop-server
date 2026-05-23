# pylint: disable=redefined-outer-name
"""Tests various POP3 sequences against dummy server."""

import asyncio as aio
import hashlib
import typing as t
from pathlib import Path

import pytest
import pytest_asyncio

from pypop.server import listen
from pypop.types import (
    BUFFER_SIZE,
    LOGIN_DELAY,
    RES_AUTH_REQUIRED,
    RES_AUTHENTICATED,
    RES_CAPA,
    RES_EMPTY_LINE,
    RES_GOODBYE,
    RES_INVALID_CREDS,
    RES_LOGIN_DELAY,
    RES_MARK_DELETE,
    RES_NO_SUCH_ITEM,
    RES_NOOP,
    RES_READY,
    RES_RESET,
    RES_SYNTAX_ERR,
    RES_USER_ACCEPTED,
    PopConfig,
    PopError,
    PopListItem,
    PopReader,
)

TEST_DIR = Path(__file__).parent

TEST_EMAILS = [b"""From: bob@email.com\r
To: alice@email.com\r
Subject: Hello\r
\r
Hello,\r
\r
How are you,\r
\r
Best,\r
Bob"""]


class MailReader(PopReader):  # pylint: disable=too-few-public-methods
    """
    Example mail reader, just reads a string like it's a file"
    """

    def __init__(self, mail: bytes):
        self.mail = mail

    async def read(self, n: int = -1) -> bytes:
        chunk = self.mail[:n]
        self.mail = self.mail[:n]
        return chunk


def get_uid(email: bytes) -> str:
    """Test get uid"""

    return hashlib.md5(email).hexdigest()


async def validate_credentials(
    username: str, password: str, /  # pylint: disable=unused-argument
) -> bool:
    """Test validate credentials"""

    if username == "admin" and password == "password":
        return True
    return False


async def get_mailbox_list(
    username: str, /  # pylint: disable=unused-argument
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
    username: str, uid: str, /  # pylint: disable=unused-argument
) -> PopReader:
    """Test get mail item reader"""

    for email in TEST_EMAILS:
        if uid == get_uid(email):
            return MailReader(email)
    raise PopError(b"-ERR No such uid\r\n")


async def delete_items(
    username: str, uids: t.Sequence[str], /  # pylint: disable=unused-argument
) -> None:
    """Test delete items"""

    for uid in uids:
        print(f"Pretending to delete {uid}")


HOST = "0.0.0.0"
PORT = 1026


async def try_sequence(
    sequence: t.Sequence[t.Tuple[bytes, bytes] | t.Tuple[bytes, bytes, int]],
) -> str | None:
    """
    Try a sequence of commands against a test server
    """

    reader, writer = await aio.open_connection(HOST, PORT)

    for msg in sequence:
        if msg[0] is not None:
            writer.write(msg[0])
            await writer.drain()
        if len(msg) == 3:
            await aio.sleep(msg[2])
        try:
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
        port=PORT,
        validate_credentials=validate_credentials,
        get_mailbox_list=get_mailbox_list,
        get_item_reader=get_item_reader,
        delete_items=delete_items,
        debug=True,
    )

    task = aio.create_task(listen(cfg))

    await aio.sleep(5)

    try:
        yield task
    finally:
        task.cancel()
        try:
            await task
        except aio.CancelledError:
            print("Background task cancelled")


@pytest.mark.asyncio
async def test_basic(start_test_listener):
    """Tests a basic sequence"""

    assert not start_test_listener.done()
    stat_count = 0
    stat_size = 0
    list_items = []
    uidl_items = []
    for i, email in enumerate(TEST_EMAILS):
        assert email
        stat_count += 1
        stat_size += len(email)
        list_items.append(f"{i+1} {len(email)}")
        uidl_items.append(f"{i+1} {get_uid(email)}")
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
    result = await try_sequence(sequence)
    assert result is None


@pytest.mark.asyncio
async def test_top(start_test_listener):
    """Tests various uses of TOP command"""

    assert not start_test_listener.done()
    email_split = TEST_EMAILS[0].decode().split("\r\n\r\n", 1)
    email_body_split = email_split[1].split("\r\n")
    sequence = [
        (None, RES_READY),
        (b"USER admin\r\n", RES_USER_ACCEPTED),
        (b"PASS password\r\n", RES_AUTHENTICATED),
        (b"TOP 1 0\r\n", f"{email_split[0]}\r\n\r\n.\r\n".encode()),
        (
            b"TOP 1 1\r\n",
            f"{email_split[0]}\r\n\r\n{'\r\n'.join(email_body_split[:1])}\r\n.\r\n".encode(),
        ),
        (
            b"TOP 1 3\r\n",
            f"{email_split[0]}\r\n\r\n{'\r\n'.join(email_body_split[:3])}\r\n.\r\n".encode(),
        ),
        (
            b"TOP 1 100\r\n",
            f"{email_split[0]}\r\n\r\n{'\r\n'.join(email_body_split[:100])}\r\n.\r\n".encode(),
        ),
        (b"QUIT\r\n", RES_GOODBYE),
    ]
    result = await try_sequence(sequence)
    assert result is None


@pytest.mark.asyncio
async def test_login(start_test_listener):
    """Tests login and auth"""

    assert not start_test_listener.done()
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
        (b"PASS toofast\r\n", RES_LOGIN_DELAY, LOGIN_DELAY + 1),
        (b"PASS bad\r\n", RES_INVALID_CREDS, LOGIN_DELAY + 1),
        (b"PASS password\r\n", RES_AUTHENTICATED),
        (b"QUIT\r\n", RES_GOODBYE),
    ]
    result = await try_sequence(sequence)
    assert result is None


@pytest.mark.asyncio
async def test_invalid_commands(start_test_listener):
    """Tests invalid commands"""

    assert not start_test_listener.done()
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
        (b"QUIT\r\n", RES_GOODBYE),
    ]
    result = await try_sequence(sequence)
    assert result is None


@pytest.mark.asyncio
async def test_pipelining(start_test_listener):
    """Tests pipelining"""

    assert not start_test_listener.done()
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
    result = await try_sequence(sequence)
    assert result is None


@pytest.mark.asyncio
async def test_delete(start_test_listener):
    """Tests login and auth"""

    assert not start_test_listener.done()
    sequence = [
        (None, RES_READY),
        (b"USER admin\r\n", RES_USER_ACCEPTED),
        (b"PASS password\r\n", RES_AUTHENTICATED),
        (b"DELE 1\r\n", RES_MARK_DELETE),
        (b"RSET\r\n", RES_RESET),
        (b"QUIT\r\n", RES_GOODBYE),
    ]
    result = await try_sequence(sequence)
    assert result is None
