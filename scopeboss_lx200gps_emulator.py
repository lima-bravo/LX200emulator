#!/usr/bin/env python3
"""
scopeboss_lx200gps_emulator.py

Single-module TCP emulator for a subset of the Meade LX200GPS serial protocol.
Implements Home/Park/Sleep/Wake + Focuser, plus:
- ACK (0x06) mount-mode query response
- Busy/NAK (0x15) lock policy
- TCP byte-stream parser (#-terminated frames)

Protocol grounding:
- Commands are ASCII and typically terminated by '#'. 13
- LX200GPS may respond with NAK (0x15) when busy; controller retries. 13
- Home commands :hS# :hF# :h?# :hP# :hN# :hW# are defined; :h?# returns 0/1/2. 3
- Focus commands :F+# :F-# :FQ# and :F<n># (1..4) are defined. 3
- Operational guidance: after Park, you can power off (wait for motors to stop). 4
"""

from future import annotations

import argparse
import asyncio
import json
import random
import time
from dataclasses import dataclass, asdict
from enum import Enum, auto
from typing import List, Optional, Tuple, Union


# -----------------------------
# Protocol constants
# -----------------------------
ACK_BYTE = 0x06  # alignment/mount mode query 12
NAK_BYTE = 0x15  # busy/unavailable response 13
HASH = ord("#")


# -----------------------------
# Config
# -----------------------------
class HomeMode(str, Enum):
    TIMER = "timer"
    SCRIPTED = "scripted"


@dataclass
class EmulatorConfig:
    host: str = "127.0.0.1"
    port: int = 4030

    # ACK response mount-mode byte. Commonly P/L/D per protocol listing (Polar/Land/AltAz). 2
    mount_mode_byte: str = "P"

    # Parser safety
    max_frame_len: int = 256

    # Busy behavior
    nak_on_lock: bool = True
    # optional probabilistic NAK injection (stress-testing). default 0 for deterministic.
    nak_probability: float = 0.0

    # Home modeling
    home_mode: HomeMode = HomeMode.TIMER
    home_duration_ms: int = 2000
    home_timer_succeed: bool = True
    # Scripted home status responses for :h?# (2=in progress, 1=found, 0=failed) 3
    home_script: Optional[List[int]] = None

    # Park modeling
    park_duration_ms: int = 5000

    # Response formatting
    home_status_append_hash: bool = False

    # Debug-only commands (non-Meade)
    allow_debug_commands: bool = False


def load_config(path: Optional[str]) -> EmulatorConfig:
    if not path:
        return EmulatorConfig()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    cfg = EmulatorConfig()
    for k, v in data.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)

    # enum normalize
    if isinstance(cfg.home_mode, str):
        cfg.home_mode = HomeMode(cfg.home_mode)

    return cfg


# -----------------------------
# Command types & parser
# -----------------------------
@dataclass(frozen=True)
class CmdAck:
    pass


@dataclass(frozen=True)
class CmdFrame:
    text: str  # includes trailing '#', e.g. ':hP#'


ProtocolCommand = Union[CmdAck, CmdFrame]


class StreamParser:
    """Streaming byte parser: emits CmdAck for single 0x06 bytes (when not in a frame),
    and CmdFrame for ASCII frames terminated with '#'.
    """

    def init(self, max_len: int = 256):
        self.max_len = max_len
        self.buf = bytearray()

    def feed(self, data: bytes) -> List[ProtocolCommand]:
        out: List[ProtocolCommand] = []
        for b in data:
            # ACK is single-byte query, only if we're not accumulating a frame
            if b == ACK_BYTE and len(self.buf) == 0:
                out.append(CmdAck())
                continue

            self.buf.append(b)
            if len(self.buf) > self.max_len:
                # parse error: drop buffer
                self.buf.clear()
                continue

            if b == HASH:
                raw = bytes(self.buf[:-1])
                self.buf.clear()
                try:
                    text = raw.decode("ascii", errors="strict")
                except UnicodeDecodeError:
                    continue
                out.append(CmdFrame(text=text + "#"))
        return out


# -----------------------------
# State machine definitions
# -----------------------------
class MotionState(Enum):
    IDLE_TRACKING = auto()
    HOMING = auto()
    PARKING = auto()
    PARKED_READY = auto()
    SLEEPING = auto()
    POWERED_OFF = auto()


class HomeStatus(Enum):
    UNKNOWN = auto()
    IN_PROGRESS = auto()
    FOUND = auto()
    FAILED = auto()


class FocusState(Enum):
    FOCUS_IDLE = auto()
    FOCUS_IN = auto()
    FOCUS_OUT = auto()


@dataclass
class EmulatorState:
    now_ms: int = 0
    motion: MotionState = MotionState.IDLE_TRACKING
    home_status: HomeStatus = HomeStatus.UNKNOWN
    focus: FocusState = FocusState.FOCUS_IDLE
    focus_speed: Optional[int] = None  # 1..4
    last_error: Optional[str] = None

    # timers
    park_done_at_ms: Optional[int] = None
    home_done_at_ms: Optional[int] = None

    # scripted home state (session-local cursor)
    home_script: Optional[List[int]] = None
    home_script_index: int = 0

    def to_debug_dict(self) -> dict:
        d = {
            "now_ms": self.now_ms,
            "motion": self.motion.name,
            "home_status": self.home_status.name,
            "focus": self.focus.name,
            "focus_speed": self.focus_speed,
            "last_error": self.last_error,
            "park_done_at_ms": self.park_done_at_ms,
            "home_done_at_ms": self.home_done_at_ms,
            "home_script": self.home_script,
            "home_script_index": self.home_script_index,
        }
        return d


# -----------------------------
# Lock policy
# -----------------------------
def is_allowed(state: EmulatorState, cmd: ProtocolCommand, cfg: EmulatorConfig) -> Tuple[bool, Optional[bytes]]:
    """Return (allowed, immediate_reply). If not allowed, immediate_reply is usually NAK."""

    # probabilistic NAK injection (optional)
    if cfg.nak_probability > 0.0 and random.random() < cfg.nak_probability:
        return False, bytes([NAK_BYTE])

    if isinstance(cmd, CmdAck):
        return True, None

    if not isinstance(cmd, CmdFrame):
        return False, bytes([NAK_BYTE])

    t = cmd.text

    # Debug commands (non-Meade)
    if t.startswith("::") and cfg.allow_debug_commands:
        return True, None
    if t.startswith("::") and not cfg.allow_debug_commands:
        return False, bytes([NAK_BYTE])

    # Always allowed observer/safety commands
    if t == ":h?#":  # query home status 3
        return True, None
    if t == ":hN#":  # sleep 3
        return True, None
    if t == ":FQ#":  # stop focusing 3
        return True, None

    # Sleeping: only allow wake + home status + ACK
    if state.motion == MotionState.SLEEPING:
        if t == ":hW#":  # wake 3
            return True, None
        return False, bytes([NAK_BYTE]) if cfg.nak_on_lock else (True, None)

    # Busy operations: HOMING/PARKING
    if state.motion in (MotionState.HOMING, MotionState.PARKING):
        # Disallow most commands while busy; allow wake harmlessly
        if t == ":hW#":
            return True, None
        return False, bytes([NAK_BYTE]) if cfg.nak_on_lock else (True, None)

    # Otherwise allowed
    return True, None


# -----------------------------
# Reducer / command handling
# -----------------------------
def handle_command(state: EmulatorState, cmd: ProtocolCommand, cfg: EmulatorConfig) -> Tuple[EmulatorState, Optional[bytes], bool]:
    """
    Apply a single command.
    Returns: (state, reply_bytes_or_None, close_connection_flag)
    """
    close_conn = False

    # ACK query -> return mount mode byte (single byte)
    if isinstance(cmd, CmdAck):
        b = cfg.mount_mode_byte.encode("ascii", errors="ignore")[:1] or b"P"
        return state, b, close_conn

    if not isinstance(cmd, CmdFrame):
        state.last_error = "unknown_cmd_type"
        return state, None, close_conn

    t = cmd.text

    # Debug commands (non-Meade)
    if t == "::STATE#" and cfg.allow_debug_commands:
        payload = json.dumps(state.to_debug_dict(), separators=(",", ":")).encode("utf-8") + b"\n"
        return state, payload, close_conn

    if t == "::POWEROFF#" and cfg.allow_debug_commands:
        # Emulator-level power-off: close socket and mark powered off.
        state.motion = MotionState.POWERED_OFF
        close_conn = True
        return state, None, close_conn

    # Home commands (h-family) 3
    if t in (":hS#", ":hF#"):
        state.motion = MotionState.HOMING
        state.home_status = HomeStatus.IN_PROGRESS
        state.home_done_at_ms = state.now_ms + int(cfg.home_duration_ms)
        state.last_error = None
        # initialize scripted sequence cursor from config, session-local copy
        if cfg.home_mode == HomeMode.SCRIPTED and cfg.home_script:
            state.home_script = list(cfg.home_script)
            state.home_script_index = 0
        else:
            state.home_script = None
            state.home_script_index = 0
        return state, None, close_conn

    if t == ":h?#":
        code = home_status_code(state, cfg)
        payload = str(code).encode("ascii")
        if cfg.home_status_append_hash:
            payload += b"#"
        return state, payload, close_conn

    # Park command (returns nothing in protocol text) 3
    if t == ":hP#":
        state.motion = MotionState.PARKING
        state.park_done_at_ms = state.now_ms + int(cfg.park_duration_ms)
        state.last_error = None
        return state, None, close_conn

    # Sleep/Wake 3
    if t == ":hN#":
        state.motion = MotionState.SLEEPING
        state.focus = FocusState.FOCUS_IDLE
        state.last_error = None
        return state, None, close_conn

    if t == ":hW#":
        state.motion = MotionState.IDLE_TRACKING
        state.last_error = None
        return state, None, close_conn

    # Focuser commands 3
    if t == ":F+#":
        state.focus = FocusState.FOCUS_IN
        state.last_error = None
        return state, None, close_conn

    if t == ":F-#":
        state.focus = FocusState.FOCUS_OUT
        state.last_error = None
        return state, None, close_conn

    if t == ":FQ#":
        state.focus = FocusState.FOCUS_IDLE
        state.last_error = None
        return state, None, close_conn

    if t in (":FF#", ":FS#"):
        # Aliases listed in protocol for focus speed extremes 3
        state.focus_speed = 4 if t == ":FF#" else 1
        state.last_error = None
        return state, None, close_conn

    # :F<n># speed set, n=1..4 for LX200GPS 3
    if len(t) == 4 and t.startswith(":F") and t.endswith("#"):
        ch = t[2]
        if ch.isdigit():
            n = int(ch)
            if 1 <= n <= 4:
                state.focus_speed = n
                state.last_error = None
                return state, None, close_conn

    # Unknown command: ignore but record error
    state.last_error = f"unhandled:{t}"
    return state, None, close_conn


def tick(state: EmulatorState, dt_ms: int, cfg: EmulatorConfig) -> EmulatorState:
    state.now_ms += dt_ms

    # Park completion -> PARKED_READY (emulator model; :hP# has no completion response in cited text) 3
    if state.park_done_at_ms is not None and state.now_ms >= state.park_done_at_ms:
        if state.motion == MotionState.PARKING:
            state.motion = MotionState.PARKED_READY
        state.park_done_at_ms = None

    # Home completion (timer-based) sets final status and returns to idle
    if state.home_done_at_ms is not None and state.now_ms >= state.home_done_at_ms:
        if state.motion == MotionState.HOMING and cfg.home_mode == HomeMode.TIMER:
            state.home_status = HomeStatus.FOUND if cfg.home_timer_succeed else HomeStatus.FAILED
            state.motion = MotionState.IDLE_TRACKING
        state.home_done_at_ms = None

    return state


def home_status_code(state: EmulatorState, cfg: EmulatorConfig) -> int:
    """
    :h?# returns:
      0 = Home Search Failed
      1 = Home Search Found
      2 = Home Search in Progress
    3
    """
    if cfg.home_mode == HomeMode.SCRIPTED and state.home_script:
        # If homing, feed scripted sequence
        if state.home_script_index < len(state.home_script):
            code = int(state.home_script[state.home_script_index])
            state.home_script_index += 1

            # update emulator state based on code
            if code == 2:
                state.motion = MotionState.HOMING
                state.home_status = HomeStatus.IN_PROGRESS
            elif code == 1:
                state.home_status = HomeStatus.FOUND
                state.motion = MotionState.IDLE_TRACKING
            elif code == 0:
                state.home_status = HomeStatus.FAILED
                state.motion = MotionState.IDLE_TRACKING
            else:
                # invalid scripted value -> treat as failed
                state.home_status = HomeStatus.FAILED
                state.motion = MotionState.IDLE_TRACKING
                code = 0
            return code

        # script exhausted -> map current state
        # fall through to mapping below

    if state.motion == MotionState.HOMING or state.home_status == HomeStatus.IN_PROGRESS:
        return 2
    if state.home_status == HomeStatus.FOUND:
        return 1
    if state.home_status == HomeStatus.FAILED:
        return 0
    # unknown -> treat as not found/failed
    return 0


# -----------------------------
# TCP server
# -----------------------------
TICK_MS = 50


async def client_session(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, cfg: EmulatorConfig) -> None:
    peer = writer.get_extra_info("peername")
    print(f"[conn] {peer}")
    parser = StreamParser(max_len=cfg.max_frame_len)
    state = EmulatorState(motion=MotionState.IDLE_TRACKING)

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
                allowed, immediate = is_allowed(state, cmd, cfg)
                if not allowed:
                    if immediate is not None:
                        writer.write(immediate)
                        await writer.drain()
                    continue

                state, reply, close_conn = handle_command(state, cmd, cfg)
                if reply is not None:
                    writer.write(reply)
                    await writer.drain()
                if close_conn:
                    # simulate power-off by closing socket
                    writer.close()
                    await writer.wait_closed()
                    return
    finally:
        tick_task.cancel()
        try:
            await tick_task
        except asyncio.CancelledError:
            pass
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        print(f"[disc] {peer}")


async def run_server(cfg: EmulatorConfig) -> None:
    server = await asyncio.start_server(lambda r, w: client_session(r, w, cfg), cfg.host, cfg.port)
    addrs = ", ".join(str(sock.getsockname()) for sock in (server.sockets or []))
    print(f"ScopeBoss LX200GPS emulator listening on {addrs}")
    async with server:
        await server.serve_forever()


def main():
    ap = argparse.ArgumentParser(description="ScopeBoss LX200GPS TCP serial emulator (single module)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", default=4030, type=int)
    ap.add_argument("--config", default=None, help="path to JSON config")
    ap.add_argument("--debug", action="store_true", help="enable debug-only commands ::STATE# and ::POWEROFF#")
    args = ap.parse_args()

    cfg = load_config(args.config)
    cfg.host = args.host
    cfg.port = args.port
    if args.debug:
        cfg.allow_debug_commands = True

    asyncio.run(run_server(cfg))


if name == "main":
    main()

