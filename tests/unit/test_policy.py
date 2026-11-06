"""Unit tests for protocol-version profile behavior."""
from __future__ import annotations

import pytest

from scopeboss_emulator.config import EmulatorConfig
from scopeboss_emulator.simulator import TelescopeSimulator

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("version", "expected_product", "expected_firmware"),
    [
        ("lx200classic", "LX200#", "2.0i#"),
        ("lx200gps", "LX200GPS#", "4.2g#"),
        ("autostar2", "Autostar II#", "2.5j#"),
    ],
)
def test_protocol_profile_identity(version: str, expected_product: str, expected_firmware: str):
    sim = TelescopeSimulator(EmulatorConfig(protocol_version=version))
    try:
        assert sim.process(":GVP#") == expected_product.encode()
        assert sim.process(":GVN#") == expected_firmware.encode()
    finally:
        sim.close()


def test_ack_returns_mount_mode_byte():
    sim = TelescopeSimulator(EmulatorConfig(protocol_version="lx200gps", mount_mode_byte="L"))
    try:
        assert sim.process(b"\x06") == b"L"
    finally:
        sim.close()
