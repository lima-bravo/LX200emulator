
from __future__ import annotations
import argparse
import asyncio
from typing import Optional

from .config import load_config, EmulatorConfig
from .protocol.parser import StreamParser
from .core.state import TelescopeState, LinkState, MotionState
from .core.policy import is_allowed, NAK_BYTE
from .core.reducer import handle_command, tick

TICK_MS = 50

async def client_session(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, cfg: EmulatorConfig) -> None:
    parser = StreamParser(max_len=cfg.max_frame_len)
    state = TelescopeState(link=LinkState.CONNECTED, motion=MotionState.IDLE_TRACKING)

    async def ticker():
        nonlocal state
        while True:
            await asyncio.sleep(TICK_MS / 1000.0)
            state = tick(state, TICK_MS, cfg)

    tick_task = asyncio.create_task(ticker())

    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break

            cmds = parser.feed(data)
            for cmd in cmds:
                # lock/busy policy
                pol = is_allowed(state.motion, cmd)
                if not pol.allowed:
                    if pol.immediate_reply is not None:
                        writer.write(pol.immediate_reply)
                        await writer.drain()
                    continue

                # apply command
                state, reply = handle_command(state, cmd, cfg)
                if reply is not None:
                    writer.write(reply)
                    await writer.drain()
    finally:
        tick_task.cancel()
        try:
            await tick_task
        except asyncio.CancelledError:
            pass
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

