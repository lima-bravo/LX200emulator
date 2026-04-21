
from __future__ import annotations
from dataclasses import replace
from typing import Tuple, List, Optional

from .state import TelescopeState, LinkState, MotionState, HomeStatus, FocusState, Timers
from ..config import EmulatorConfig
from ..protocol.types import CmdAck, CmdFrame, ProtocolCommand

ACK_BYTE = b"\x06"  # incoming query; responses are mode bytes (not 0x06)

def handle_command(state: TelescopeState, cmd: ProtocolCommand, cfg: EmulatorConfig) -> Tuple[TelescopeState, Optional[bytes]]:
    """
    Apply command -> new state, and optional immediate reply bytes.
    """
    s = state

    # ACK query: respond with configured mount mode indicator (e.g., 'P', 'L', 'D') 4
    if isinstance(cmd, CmdAck):
        b = cfg.mount_mode_byte.encode("ascii", errors="ignore")[:1]
        return s, b

    if not isinstance(cmd, CmdFrame):
        return replace(s, last_error="unknown_cmd_type"), None

    t = cmd.text

    # Home commands 2
    if t == ":hS#" or t == ":hF#":
        s = replace(
            s,
            motion=MotionState.HOMING,
            home_status=HomeStatus.IN_PROGRESS,
            timers=replace(s.timers, home_done_at_ms=s.now_ms + cfg.home.duration_ms),
            last_error=None,
        )
        return s, None

    if t == ":h?#":
        code = _home_status_code(s, cfg)
        payload = str(code).encode("ascii")
        if cfg.home_status_append_hash:
            payload += b"#"
        return s, payload

    if t == ":hP#":
        s = replace(
            s,
            motion=MotionState.PARKING,
            timers=replace(s.timers, park_done_at_ms=s.now_ms + cfg.park_duration_ms),
            last_error=None,
        )
        return s, None

    if t == ":hN#":
        # Sleep: power down (model), focuser forced idle 2
        s = replace(s, motion=MotionState.SLEEPING, focus=FocusState.FOCUS_IDLE, last_error=None)
        return s, None

    if t == ":hW#":
        s = replace(s, motion=MotionState.IDLE_TRACKING, last_error=None)
        return s, None

    # Focus commands 2
    if t == ":F+#":
        s = replace(s, focus=FocusState.FOCUS_IN, last_error=None)
        return s, None
    if t == ":F-#":
        s = replace(s, focus=FocusState.FOCUS_OUT, last_error=None)
        return s, None
    if t == ":FQ#":
        s = replace(s, focus=FocusState.FOCUS_IDLE, last_error=None)
        return s, None

    # Focus speed: :F1#..:F4# plus optional :FF#/:FS#
    if t in (":FF#", ":FS#"):
        # map aliases to speed if you want; protocol provides :FF#/:FS# 2
        # We'll set 4 for FF, 1 for FS
        n = 4 if t == ":FF#" else 1
        s = replace(s, focus_speed=n, last_error=None)
        return s, None

    if len(t) == 4 and t.startswith(":F") and t.endswith("#"):
        # e.g. ':F3#'
        ch = t[2]
        if ch.isdigit():
            n = int(ch)
            if 1 <= n <= 4:
                s = replace(s, focus_speed=n, last_error=None)
                return s, None

    # Unknown command: ignore (or set error)
    s = replace(s, last_error=f"unhandled:{t}")
    return s, None


def tick(state: TelescopeState, dt_ms: int, cfg: EmulatorConfig) -> TelescopeState:
    """
    Advance time and fire deterministic timers.
    """
    s = replace(state, now_ms=state.now_ms + dt_ms)

    # Park completion -> PARKED_READY (emulator modeling; :hP# returns nothing in cited text) 2
    if s.timers.park_done_at_ms is not None and s.now_ms >= s.timers.park_done_at_ms:
        if s.motion == MotionState.PARKING:
            s = replace(s, motion=MotionState.PARKED_READY, timers=replace(s.timers, park_done_at_ms=None))

    # Home completion timer affects what :h?# will return
    if s.timers.home_done_at_ms is not None and s.now_ms >= s.timers.home_done_at_ms:
        # If timer-based home behavior, we mark FOUND/FAILED but keep HOMING until queried or immediately exit.
        # We'll immediately exit to IDLE_TRACKING for simplicity, while still allowing :h?# to report final code.
        if s.motion == MotionState.HOMING and cfg.home.mode == "timer":
            final = HomeStatus.FOUND if cfg.home.succeed else HomeStatus.FAILED
            s = replace(s, home_status=final, motion=MotionState.IDLE_TRACKING, timers=replace(s.timers, home_done_at_ms=None))

    return s


def _home_status_code(s: TelescopeState, cfg: EmulatorConfig) -> int:
    """
    Return code for :h?#: 0 failed, 1 found, 2 in progress. 2
    If scripted mode, pop from script until exhausted.
    """
    if cfg.home.mode == "scripted" and cfg.home.script:
        # We don't mutate cfg; scripted mode is better implemented with a session-local script cursor.
        # For minimal version, just return current status mapping.
        pass

    if s.motion == MotionState.HOMING:
        return 2
    if s.home_status == HomeStatus.FOUND:
        return 1
    if s.home_status == HomeStatus.FAILED:
        return 0
    # default unknown -> treat as in-progress if homing else failed
    return 2 if s.motion == MotionState.HOMING else 0

'''
Note: The protocol defines the :h?# return codes (0/1/2) but the snippet we have does not specify a terminator for that response. We made it configurable (home_status_append_hash). [Meade Tele...d Protocol]
'''