"""Unit tests for threaded state-machine simulation behavior."""
from __future__ import annotations

import pytest
import time

from scopeboss_emulator.config import EmulatorConfig, MotionConfig
from scopeboss_emulator.simulator import TelescopeSimulator

pytestmark = pytest.mark.unit


def _make_sim() -> TelescopeSimulator:
    cfg = EmulatorConfig(
        protocol_version="lx200gps",
        motion=MotionConfig(
            guide_deg_per_s=0.01,
            center_deg_per_s=0.2,
            find_deg_per_s=1.0,
            slew_deg_per_s=60.0,
            tick_ms=10,
        ),
    )
    return TelescopeSimulator(cfg)


def test_set_and_get_target_coordinates():
    sim = _make_sim()
    try:
        assert sim.process(":Sr10:00:00#") == b"1"
        assert sim.process(":Sd+20*00'00#") == b"1"
        assert sim.process(":CM#") == b"Coordinates     matched.       #"
        assert sim.process(":GR#") == b"10:00:00#"
        assert sim.process(":GD#") == b"+20*00'00#"
    finally:
        sim.close()


def test_precision_toggle_changes_gr_format():
    sim = _make_sim()
    try:
        high = sim.process(":GR#")
        sim.process(":U#")
        low = sim.process(":GR#")
        assert high is not None and len(high.decode().split(":")) == 3
        assert low is not None and "." in low.decode()
    finally:
        sim.close()


def test_slew_progresses_in_background_thread():
    sim = _make_sim()
    try:
        sim.process(":Sr12:00:00#")
        sim.process(":Sd+10*00'00#")
        assert sim.process(":MS#") == b"0"
        time.sleep(0.25)
        ra = sim.process(":GR#")
        assert ra is not None
        assert ra != b"05:30:50#"
    finally:
        sim.close()


def test_unknown_command_returns_nak():
    sim = _make_sim()
    try:
        assert sim.process(":ZZ#") == b"\x15"
    finally:
        sim.close()
