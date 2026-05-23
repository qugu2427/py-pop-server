"""Types and parser for POP3 commands."""

import typing as t

import pydantic as pd

from pypop.types import RES_EMPTY_LINE, RES_SYNTAX_ERR, PopError


class QuitCmd(pd.BaseModel):
    """QUIT"""

    cmd: t.Literal["QUIT"]


class StatCmd(pd.BaseModel):
    """STAT"""

    cmd: t.Literal["STAT"]


class RetrCmd(pd.BaseModel):
    """RETR id"""

    cmd: t.Literal["RETR"]
    id: pd.PositiveInt


class ListCmd(pd.BaseModel):
    """LIST [id]"""

    cmd: t.Literal["LIST"]
    id: pd.PositiveInt | None


class UidlCmd(pd.BaseModel):
    """UIDL [id]"""

    cmd: t.Literal["UIDL"]
    id: pd.PositiveInt | None


class DeleCmd(pd.BaseModel):
    """DELE [id]"""

    cmd: t.Literal["DELE"]
    id: pd.PositiveInt


class NoopCmd(pd.BaseModel):
    """NOOP"""

    cmd: t.Literal["NOOP"]


class RsetCmd(pd.BaseModel):
    """RSET"""

    cmd: t.Literal["RSET"]


class UserCmd(pd.BaseModel):
    """USER username"""

    cmd: t.Literal["USER"]
    user: str


class PassCmd(pd.BaseModel):
    """PASS password"""

    cmd: t.Literal["PASS"]
    password: str


class TopCmd(pd.BaseModel):
    """TOP id lines"""

    cmd: t.Literal["TOP"]
    id: pd.PositiveInt
    lines: pd.NonNegativeInt


class CapaCmd(pd.BaseModel):
    """CAPA"""

    cmd: t.Literal["CAPA"]


PopCmd = t.Annotated[
    t.Union[
        CapaCmd,
        DeleCmd,
        ListCmd,
        NoopCmd,
        PassCmd,
        QuitCmd,
        RetrCmd,
        RsetCmd,
        StatCmd,
        TopCmd,
        UidlCmd,
        UserCmd,
    ],
    pd.Field(discriminator="cmd"),
]


def parse_msg_to_cmd(  # pylint: disable=too-many-return-statements,too-many-branches
    msg: bytes,
) -> PopCmd:
    """Tries to parse a message (i.e line) to a Pop command type."""

    if msg == b"":
        raise PopError(RES_EMPTY_LINE)

    msg_split = msg.split(b" ")
    msg_split[0] = msg_split[0].upper()

    match msg_split:
        case [b"QUIT"]:
            return QuitCmd(cmd="QUIT")
        case [b"STAT"]:
            return StatCmd(cmd="STAT")
        case [b"RETR", item_id]:
            return RetrCmd.model_validate({"cmd": "RETR", "id": item_id})
        case [b"LIST", item_id]:
            return ListCmd.model_validate({"cmd": "LIST", "id": item_id})
        case [b"LIST"]:
            return ListCmd(cmd="LIST", id=None)
        case [b"UIDL", item_id]:
            return UidlCmd.model_validate({"cmd": "UIDL", "id": item_id})
        case [b"UIDL"]:
            return UidlCmd(cmd="UIDL", id=None)
        case [b"DELE", item_id]:
            return DeleCmd.model_validate({"cmd": "DELE", "id": item_id})
        case [b"NOOP"]:
            return NoopCmd(cmd="NOOP")
        case [b"RSET"]:
            return RsetCmd(cmd="RSET")
        case [b"USER", user]:
            return UserCmd.model_validate({"cmd": "USER", "user": user})
        case [b"PASS", password]:
            return PassCmd.model_validate({"cmd": "PASS", "password": password})
        case [b"TOP", item_id, lines]:
            return TopCmd.model_validate({"cmd": "TOP", "id": item_id, "lines": lines})
        case [b"CAPA"]:
            return CapaCmd(cmd="CAPA")
        case _:
            raise PopError(RES_SYNTAX_ERR)
