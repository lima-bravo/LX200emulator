
from __future__ import annotations
import argparse
import asyncio

from .config import load_config, EmulatorConfig
from .protocol.parser import StreamParser
from .protocol.types import CmdAck, CmdFrame
from .simulator import TelescopeSimulator

async def client_session(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, cfg: EmulatorConfig) -> None:
    parser = StreamParser(max_len=cfg.max_frame_len)
    simulator = TelescopeSimulator(cfg)

    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break

            cmds = parser.feed(data)
            for cmd in cmds:
                if isinstance(cmd, CmdAck):
                    reply = simulator.process(b"\x06")
                elif isinstance(cmd, CmdFrame):
                    reply = simulator.process(cmd.text)
                else:
                    reply = None
                if reply is not None:
                    writer.write(reply)
                    await writer.drain()
    finally:
        simulator.close()
        writer.close()
        await writer.wait_closed()


async def run_server(host: str, port: int, cfg: EmulatorConfig) -> None:
    server = await asyncio.start_server(lambda r, w: client_session(r, w, cfg), host, port)
    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"ScopeBoss emulator listening on {addrs}")
    async with server:
        await server.serve_forever()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", default=4030, type=int)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)
    asyncio.run(run_server(args.host, args.port, cfg))

if __name__ == "__main__":
    main()

