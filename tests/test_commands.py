"""Tests parsing of commands"""

import pypop.command as pc


def test_parse_msg_to_cmd():
    """Test all commands"""

    tests = [
        (
            b"USER john.doe",
            pc.UserCmd(
                cmd="USER",
                user="john.doe",
            ),
        ),
        (
            b"PASS foobar123",
            pc.PassCmd(
                cmd="PASS",
                password="foobar123",
            ),
        ),
        (
            b"RETR 1",
            pc.RetrCmd(
                cmd="RETR",
                id=1,
            ),
        ),
        (
            b"LIST",
            pc.ListCmd(
                cmd="LIST",
                id=None,
            ),
        ),
        (
            b"LIST 1",
            pc.ListCmd(
                cmd="LIST",
                id=1,
            ),
        ),
        (
            b"UIDL",
            pc.UidlCmd(
                cmd="UIDL",
                id=None,
            ),
        ),
        (
            b"UIDL 1",
            pc.UidlCmd(
                cmd="UIDL",
                id=1,
            ),
        ),
        (
            b"DELE 1",
            pc.DeleCmd(
                cmd="DELE",
                id=1,
            ),
        ),
        (
            b"NOOP",
            pc.NoopCmd(
                cmd="NOOP",
            ),
        ),
        (
            b"RSET",
            pc.RsetCmd(
                cmd="RSET",
            ),
        ),
        (
            b"USER john.doe",
            pc.UserCmd(cmd="USER", user="john.doe"),
        ),
        (
            b"PASS password",
            pc.PassCmd(
                cmd="PASS",
                password="password",
            ),
        ),
        (
            b"TOP 1 100",
            pc.TopCmd(
                cmd="TOP",
                id=1,
                lines=100,
            ),
        ),
        (
            b"CAPA",
            pc.CapaCmd(
                cmd="CAPA",
            ),
        ),
    ]

    for test in tests:
        assert pc.parse_msg_to_cmd(test[0]) == test[1]
