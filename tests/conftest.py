"""Shared fixtures for scopeboss_emulator tests."""
from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest

from scopeboss_emulator.config import EmulatorConfig, MotionConfig
from scopeboss_emulator.server import run_server

# ---------------------------------------------------------------------------
# Emulator config used by unit tests
# ---------------------------------------------------------------------------

@pytest.fixture
def fast_cfg() -> EmulatorConfig:
    """Config with fast tick/rates for deterministic unit tests."""
    return EmulatorConfig(
        protocol_version="lx200gps",
        mount_mode_byte="P",
        max_frame_len=256,
        motion=MotionConfig(
            guide_deg_per_s=0.05,
            center_deg_per_s=0.5,
            find_deg_per_s=2.0,
            slew_deg_per_s=20.0,
            tick_ms=10,
        ),
    )


# ---------------------------------------------------------------------------
# Session-scoped live server for integration tests
# ---------------------------------------------------------------------------

EMULATOR_HOST = "127.0.0.1"
EMULATOR_PORT = 4030


def _start_server_thread(host: str, port: int, cfg: EmulatorConfig) -> asyncio.AbstractEventLoop:
    """Spin up the asyncio server in a daemon thread; return its event loop."""
    loop = asyncio.new_event_loop()

    def _run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_server(host, port, cfg))

    t = threading.Thread(target=_run, daemon=True, name="emulator-server")
    t.start()
    return loop


def _wait_for_port(host: str, port: int, timeout: float = 5.0) -> None:
    """Block until the server accepts connections or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except (ConnectionRefusedError, OSError):
            time.sleep(0.05)
    raise RuntimeError(
        f"Emulator on {host}:{port} did not become ready within {timeout}s"
    )


@pytest.fixture(scope="session", autouse=True)
def live_emulator() -> None:
    """
    Start the scopeboss_emulator TCP server once per pytest session.

    Integration tests connect directly to 127.0.0.1:4030.
    Unit tests (pytest.mark.unit) don't use the socket, but starting the
    server is harmless — the daemon thread exits with the process.
    """
    cfg = EmulatorConfig(
        protocol_version="lx200gps",
        motion=MotionConfig(tick_ms=10),  # fast tick for snappy integration tests
    )
    loop = _start_server_thread(EMULATOR_HOST, EMULATOR_PORT, cfg)
    _wait_for_port(EMULATOR_HOST, EMULATOR_PORT)
    yield
    # Stop the event loop cleanly; daemon thread exits automatically.
    loop.call_soon_threadsafe(loop.stop)
