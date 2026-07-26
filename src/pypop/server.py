"""Functions to listen an serve."""

import asyncio as aio

from pypop.session import PopSession
from pypop.types import BUFFER_SIZE, RES_READY, PopConfig


class DebugWriter(aio.StreamWriter):
    """Write which debugs every message"""

    def __init__(
        self,
        *,
        original,
        addr: str,
        debug_fn,
    ):
        self.original = original
        self.addr = addr
        self.debug_fn = debug_fn
        super().__init__(
            transport=original._transport,
            protocol=original._protocol,
            reader=original._reader,
            loop=original._loop,
        )

    def write(self, data):
        self.debug_fn(f"{self.addr} <= {data!r}")
        return self.original.write(data)


def get_client_handler(cfg: PopConfig):
    """Get client handler function given cfg"""

    login_attempts: dict[str, float] = {}

    async def handle_client(
        reader: aio.StreamReader,
        writer: aio.StreamWriter,
    ) -> None:
        """Handles client IO"""

        peer = writer.get_extra_info("peername")
        addr = f"{peer[0]}:{peer[1]}"
        if cfg.debug:
            writer = DebugWriter(
                original=writer,
                addr=addr,
                debug_fn=cfg.debug_fn,
            )
            cfg.debug_fn(f"{addr} == connection started")
        try:
            pop_session = PopSession(writer, cfg, login_attempts)
            writer.write(RES_READY)
            await writer.drain()
            loop = aio.get_running_loop()
            command_deadline = loop.time() + cfg.command_timeout
            while True:
                try:
                    data = await aio.wait_for(
                        reader.read(BUFFER_SIZE),
                        timeout=max(0, command_deadline - loop.time()),
                    )
                except TimeoutError:
                    break
                if not data:
                    break
                if cfg.debug:
                    cfg.debug_fn(f"{addr} => {data!r}")
                command_received = b"\r\n" in pop_session.last_chunk_part + data
                keep_alive = await pop_session.handle_chunk(data)
                if not keep_alive:
                    break
                if command_received:
                    command_deadline = loop.time() + cfg.command_timeout
        except Exception as e:
            raise e
        finally:
            if cfg.debug:
                cfg.debug_fn(f"{addr} == closing connection")
            writer.close()
            await writer.wait_closed()

    return handle_client


async def listen(cfg: PopConfig):
    """Listen and serve POP3"""

    server = await aio.start_server(get_client_handler(cfg), cfg.host, cfg.port)

    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    print(f"Listening on {addrs}")

    async with server:
        await server.serve_forever()
