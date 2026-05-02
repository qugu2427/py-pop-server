"""
This is a simple example implementation of
as pop3 server. It not part of the
actual pop3 library.
"""

import asyncio as aio
import typing as t
from pathlib import Path

from pypop.server import listen
from pypop.types import PopConfig, PopListItem, PopReader


class ExampleMailReader(PopReader):  # pylint: disable=too-few-public-methods
    """
    A reader class
    which is used to read mail items from
    an arbitrary source (files, database, etc...).
    """

    def __init__(self, path: Path):
        self.path = path

    async def read(self, n: int = -1) -> bytes:
        with open(self.path, "rb") as f:
            while chunk := f.read(n):
                return chunk
        assert False


async def example_validate_credentials(username: str, password: str, /) -> bool:
    """
    Simply check the username and password.
    No need to handle any other auth state.

    This will be called after the PASS command.
    """

    if username == "user" and password == "password":
        return True
    return False


async def example_get_mailbox_list(username: str, /) -> t.Sequence[PopListItem]:
    """
    Gets an itemized list of mail items.

    This will be called on the STAT, LIST, and UIDL commands.
    """

    path = Path(f"/home/{username}/mail")
    results = []
    for item in path.iterdir():
        pop_item = PopListItem(
            uid=str(item.absolute()),
            size=item.lstat().st_size,
        )
        results.append(pop_item)
    return results


async def example_get_item_reader(
    username: str, uid: str, /  # pylint: disable=unused-argument
) -> PopReader:
    """
    Gets the mail reader.

    This will be called on the RETR and TOP commands.
    """
    return ExampleMailReader(Path(uid))


async def example_delete_items(
    username: str, uids: t.Sequence[str], /  # pylint: disable=unused-argument
) -> None:
    """
    Delete uids marked for deletion.

    This will be called after the QUIT command.
    """
    for uid in uids:
        print(f"Pretending to delete {uid}")


EXAMPLE_HOST = "0.0.0.0"
EXAMPLE_PORT = 1026


def start_example_server():
    """Starts example server"""

    print(f"Starting an example server at {EXAMPLE_HOST}:{EXAMPLE_PORT}..." "")
    cfg = PopConfig(
        host=EXAMPLE_HOST,
        port=EXAMPLE_PORT,
        validate_credentials=example_validate_credentials,
        get_mailbox_list=example_get_mailbox_list,
        get_item_reader=example_get_item_reader,
        delete_items=example_delete_items,
        debug=True,
        debug_fn=print,
    )
    aio.run(listen(cfg))


if __name__ == "__main__":
    start_example_server()
