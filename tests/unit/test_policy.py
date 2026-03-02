"""
Unit tests for policy.is_allowed: NAK when busy (HOMING/PARKING/SLEEPING), allowed commands per state.
No TCP or server required.
"""
from __future__ import annotations

import pytest

from scopeboss_emulator.core.policy import is_allowed, NAK_BYTE, PolicyResult
from scopeboss_emulator.core.state import MotionState
from scopeboss_emulator.protocol.types import CmdAck, CmdFrame

pytestmark = pytest.mark.unit


def test_ack_always_allowed():
    for motion in MotionState:
        assert is_allowed(motion, CmdAck()) == PolicyResult(True)


def test_sleeping_allows_only_wake_home_query_ack():
    """In SLEEPING: allow :hW#, :h?#, :hN#, :FQ#, and ACK; NAK the rest."""
    pol = is_allowed(MotionState.SLEEPING, CmdFrame(text=":hW#"))
    assert pol.allowed
    assert pol.immediate_reply is None

    assert is_allowed(MotionState.SLEEPING, CmdFrame(text=":h?#")).allowed
    assert is_allowed(MotionState.SLEEPING, CmdFrame(text=":hN#")).allowed
    assert is_allowed(MotionState.SLEEPING, CmdFrame(text=":FQ#")).allowed

    pol = is_allowed(MotionState.SLEEPING, CmdFrame(text=":hP#"))
    assert not pol.allowed
    assert pol.immediate_reply == NAK_BYTE

    pol = is_allowed(MotionState.SLEEPING, CmdFrame(text=":hS#"))
    assert not pol.allowed
    assert pol.immediate_reply == NAK_BYTE

    pol = is_allowed(MotionState.SLEEPING, CmdFrame(text=":F+#"))
    assert not pol.allowed
    assert pol.immediate_reply == NAK_BYTE


def test_homing_naks_park_and_slew_style_commands():
    """In HOMING: allow :h?#, :hN#, :FQ#, :hW#, ACK; NAK :hP#, :hS#, :hF#, etc."""
    assert is_allowed(MotionState.HOMING, CmdFrame(text=":h?#")).allowed
    assert is_allowed(MotionState.HOMING, CmdFrame(text=":hN#")).allowed
    assert is_allowed(MotionState.HOMING, CmdFrame(text=":FQ#")).allowed
    assert is_allowed(MotionState.HOMING, CmdFrame(text=":hW#")).allowed

    pol = is_allowed(MotionState.HOMING, CmdFrame(text=":hP#"))
    assert not pol.allowed
    assert pol.immediate_reply == NAK_BYTE

    pol = is_allowed(MotionState.HOMING, CmdFrame(text=":hS#"))
    assert not pol.allowed
    assert pol.immediate_reply == NAK_BYTE


def test_parking_naks_same_as_homing():
    assert is_allowed(MotionState.PARKING, CmdFrame(text=":h?#")).allowed
    pol = is_allowed(MotionState.PARKING, CmdFrame(text=":hP#"))
    assert not pol.allowed
    assert pol.immediate_reply == NAK_BYTE


def test_idle_tracking_allows_all_commands():
    assert is_allowed(MotionState.IDLE_TRACKING, CmdFrame(text=":hP#")).allowed
    assert is_allowed(MotionState.IDLE_TRACKING, CmdFrame(text=":hS#")).allowed
    assert is_allowed(MotionState.IDLE_TRACKING, CmdFrame(text=":F+#")).allowed


def test_parked_ready_allows_all_commands():
    assert is_allowed(MotionState.PARKED_READY, CmdFrame(text=":hP#")).allowed
    assert is_allowed(MotionState.PARKED_READY, CmdFrame(text=":hW#")).allowed
