"""
Unit tests for reducer: handle_command state/reply and tick timer transitions.
Uses fast_cfg so park/home durations are short; no TCP or server required.
"""
from __future__ import annotations

import pytest
from dataclasses import replace

from scopeboss_emulator.core.reducer import handle_command, tick
from scopeboss_emulator.core.state import (
    TelescopeState,
    MotionState,
    HomeStatus,
    FocusState,
    Timers,
)
from scopeboss_emulator.protocol.types import CmdAck, CmdFrame

pytestmark = pytest.mark.unit


def test_ack_returns_mount_mode_byte(idle_state, fast_cfg):
    state, reply = handle_command(idle_state, CmdAck(), fast_cfg)
    assert state == idle_state
    assert reply == b"P"


def test_park_sets_parking_and_timer(idle_state, fast_cfg):
    state, reply = handle_command(idle_state, CmdFrame(text=":hP#"), fast_cfg)
    assert reply is None
    assert state.motion == MotionState.PARKING
    assert state.timers.park_done_at_ms is not None
    assert state.timers.park_done_at_ms == idle_state.now_ms + fast_cfg.park_duration_ms


def test_tick_park_completes_to_parked_ready(idle_state, fast_cfg):
    state, _ = handle_command(idle_state, CmdFrame(text=":hP#"), fast_cfg)
    assert state.motion == MotionState.PARKING
    park_done_at = state.timers.park_done_at_ms
    while state.now_ms < park_done_at:
        state = tick(state, 50, fast_cfg)
    assert state.motion == MotionState.PARKED_READY
    assert state.timers.park_done_at_ms is None


def test_home_sets_homing_and_home_timer(idle_state, fast_cfg):
    state, reply = handle_command(idle_state, CmdFrame(text=":hS#"), fast_cfg)
    assert reply is None
    assert state.motion == MotionState.HOMING
    assert state.home_status == HomeStatus.IN_PROGRESS
    assert state.timers.home_done_at_ms is not None


def test_home_query_returns_2_while_homing(idle_state, fast_cfg):
    state, _ = handle_command(idle_state, CmdFrame(text=":hS#"), fast_cfg)
    _, reply = handle_command(state, CmdFrame(text=":h?#"), fast_cfg)
    assert reply == b"2"


def test_tick_home_completes_to_idle_tracking(idle_state, fast_cfg):
    state, _ = handle_command(idle_state, CmdFrame(text=":hS#"), fast_cfg)
    home_done_at = state.timers.home_done_at_ms
    while state.motion == MotionState.HOMING and state.now_ms < home_done_at:
        state = tick(state, 50, fast_cfg)
    assert state.motion == MotionState.IDLE_TRACKING
    assert state.home_status == HomeStatus.FOUND
    assert state.timers.home_done_at_ms is None


def test_home_query_returns_1_after_home_success(idle_state, fast_cfg):
    state, _ = handle_command(idle_state, CmdFrame(text=":hS#"), fast_cfg)
    home_done_at = state.timers.home_done_at_ms
    while state.now_ms < home_done_at:
        state = tick(state, 50, fast_cfg)
    _, reply = handle_command(state, CmdFrame(text=":h?#"), fast_cfg)
    assert reply == b"1"


def test_sleep_sets_sleeping_and_focus_idle(idle_state, fast_cfg):
    state = replace(idle_state, focus=FocusState.FOCUS_IN)
    state, reply = handle_command(state, CmdFrame(text=":hN#"), fast_cfg)
    assert reply is None
    assert state.motion == MotionState.SLEEPING
    assert state.focus == FocusState.FOCUS_IDLE


def test_wake_sets_idle_tracking(idle_state, fast_cfg):
    state, _ = handle_command(idle_state, CmdFrame(text=":hN#"), fast_cfg)
    assert state.motion == MotionState.SLEEPING
    state, reply = handle_command(state, CmdFrame(text=":hW#"), fast_cfg)
    assert reply is None
    assert state.motion == MotionState.IDLE_TRACKING


def test_focus_in_out_stop(idle_state, fast_cfg):
    state, _ = handle_command(idle_state, CmdFrame(text=":F+#"), fast_cfg)
    assert state.focus == FocusState.FOCUS_IN
    state, _ = handle_command(state, CmdFrame(text=":F-#"), fast_cfg)
    assert state.focus == FocusState.FOCUS_OUT
    state, _ = handle_command(state, CmdFrame(text=":FQ#"), fast_cfg)
    assert state.focus == FocusState.FOCUS_IDLE


def test_focus_speed_1_to_4(idle_state, fast_cfg):
    for n in (1, 2, 3, 4):
        state, _ = handle_command(idle_state, CmdFrame(text=f":F{n}#"), fast_cfg)
        assert state.focus_speed == n


def test_focus_speed_ff_fs(idle_state, fast_cfg):
    state, _ = handle_command(idle_state, CmdFrame(text=":FF#"), fast_cfg)
    assert state.focus_speed == 4
    state, _ = handle_command(idle_state, CmdFrame(text=":FS#"), fast_cfg)
    assert state.focus_speed == 1


def test_unknown_command_sets_last_error(idle_state, fast_cfg):
    state, reply = handle_command(idle_state, CmdFrame(text=":XX#"), fast_cfg)
    assert reply is None
    assert state.last_error == "unhandled::XX#"


def test_home_status_append_hash(idle_state, fast_cfg):
    cfg_hash = replace(fast_cfg, home_status_append_hash=True)
    state, _ = handle_command(idle_state, CmdFrame(text=":hS#"), cfg_hash)
    _, reply = handle_command(state, CmdFrame(text=":h?#"), cfg_hash)
    assert reply == b"2#"
