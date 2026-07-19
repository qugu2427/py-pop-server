"""Contains externally useful types for pypop."""

import typing as t

import pydantic as pd

BUFFER_SIZE = 1024
COMMAND_TIMEOUT = 300
MAX_LINE_LENGTH = 1024

LOGIN_DELAY = 5

RES_ALREADY_AUTHENTICATED = b"-ERR Already authenticated\r\n"
RES_AUTH_REQUIRED = b"-ERR Authentication required\r\n"
RES_AUTHENTICATED = b"+OK Authenticated\r\n"
RES_CAPA = (
    f"+OK\r\nIMPLEMENTATION pypop\r\n"
    f"LOGIN-DELAY {LOGIN_DELAY*1000}\r\n"
    f"PIPELINING\r\nTOP\r\nUIDL\r\nUSER\r\n.\r\n"
).encode()
RES_EMPTY_LINE = b"+OK Empty line\r\n"
RES_GOODBYE = b"+OK Goodbye\r\n"
RES_INVALID_CREDS = b"-ERR Invalid credentials\r\n"
RES_LOGIN_DELAY = f"-ERR Wait {LOGIN_DELAY} seconds between attempts\r\n".encode()
RES_LINE_TOO_LONG = b"-ERR Line too long\r\n"
RES_MARK_DELETE = b"+OK Marked for deletion\r\n"
RES_NO_SUCH_ITEM = b"-ERR No such item\r\n"
RES_NO_USER = b"-ERR No user provided\r\n"
RES_NOOP = b"+OK No operation\r\n"
RES_READY = b"+OK POP3 Server ready\r\n"
RES_RESET = b"+OK Session reset\r\n"
RES_SYNTAX_ERR = b"-ERR Syntax error\r\n"
RES_UNHANDLED_CMD = b"-ERR Unhandled command\r\n"
RES_USER_ACCEPTED = b"+OK User accepted\r\n"


class PopError(Exception):
    """Can be raised to cause a -ERR pop message."""

    def __init__(self, message: bytes):
        self.message = message


class PopListItem(pd.BaseModel):
    """
    Represents a single piece of mail in a mailbox.

    Attributes:
        uid (str): Unique id for a single item of mail.
        size (PositiveInt): Size, in bytes, of item.
    """

    uid: str
    size: pd.PositiveInt


class PopReader:  # pylint: disable=too-few-public-methods
    """
    Asbract class for a reader which will be used to
    read a mail item in chunks.
    """

    async def read(self, n: int = -1) -> bytes:
        """Read bytes of mail items"""

        raise NotImplementedError("PopReader is abstract")


class PopConfig(pd.BaseModel):
    """
    Represents the configuration for a POP3 server.

    Attributes:
        host: serve on host
        port: serve on port
        command_timeout: seconds to wait for client command data

        validate_credentials: fn to validate credentials
        get_mailbox_list: fn to get mailbox list
        get_item_reader: fn to get reader for mail item
        delete_items: fn to delete mail items

        debug: if true, log all messages
        debug_fn: fn to log debug messages (defaults to print)
    """

    host: str
    port: int
    command_timeout: pd.PositiveFloat = COMMAND_TIMEOUT

    validate_credentials: t.Callable[[str, str], t.Awaitable[bool]]
    get_mailbox_list: t.Callable[[str], t.Awaitable[t.Sequence[PopListItem]]]
    get_item_reader: t.Callable[[str, str], t.Awaitable[PopReader]]
    delete_items: t.Callable[[str, t.Sequence[str]], t.Any]

    debug: bool = False
    debug_fn: t.Callable[[str], t.Any] = print
