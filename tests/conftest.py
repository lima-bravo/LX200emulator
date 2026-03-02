"""
Shared fixtures for scopeboss_emulator tests.
Uses a fast-timer config so reducer/tick tests complete without sleep.
"""
from __future__ import annotations

import pytest

from scopeboss_emulator.config import EmulatorConfig, HomeBehavior
from scopeboss_emulator.core.state import (
    TelescopeState,
    LinkState,
    MotionState,
    Timers,
)


@pytest.fixture
def fast_cfg() -> EmulatorConfig:
    """Config with short durations for fast unit tests (no TCP, no sleep)."""
    return EmulatorConfig(
        mount_mode_byte="P",
        max_frame_len=256,
        nak_on_lock=True,
        park_duration_ms=100,
        home=HomeBehavior(mode="timer", duration_ms=100, succeed=True),
        home_status_append_hash=False,
    )


@pytest.fixture
def idle_state() -> TelescopeState:
    """Default idle state: connected, tracking, no timers."""
    return TelescopeState(
        now_ms=0,
        link=LinkState.CONNECTED,
        motion=MotionState.IDLE_TRACKING,
        timers=Timers(),
    )
