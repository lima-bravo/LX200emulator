
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from .state import MotionState
from ..protocol.types import CmdAck, CmdFrame, ProtocolCommand

NAK_BYTE = b"\x15"  # NAK (0x15) as busy/unavailable 12

@dataclass(frozen=True)
class PolicyResult:
    allowed: bool
    immediate_reply: Optional[bytes] = None

def is_allowed(motion: MotionState, cmd: ProtocolCommand) -> PolicyResult:
    """
    Deterministic lock policy:
    - In SLEEPING: allow only wake (:hW#), home status (:h?#), and ACK.
    - In HOMING/PARKING: allow :h?#, :hN#, :FQ#, and ACK; NAK many others.
    """
    if isinstance(cmd, CmdAck):
        return PolicyResult(True)

    if not isinstance(cmd, CmdFrame):
        return PolicyResult(False, NAK_BYTE)

    t = cmd.text

    # Always-allowed safety/observe commands:
    if t == ":h?#":
        return PolicyResult(True)
    if t == ":hN#":
        return PolicyResult(True)
    if t == ":FQ#":
        return PolicyResult(True)

    if motion == MotionState.SLEEPING:
        if t == ":hW#":
            return PolicyResult(True)
        # everything else unavailable
        return PolicyResult(False, NAK_BYTE)

    if motion in (MotionState.HOMING, MotionState.PARKING):
        # allow wake? (not meaningful unless sleeping, but harmless)
        if t == ":hW#":
            return PolicyResult(True)
        # NAK most commands while busy
        return PolicyResult(False, NAK_BYTE)

    # PARKED_READY: allow most except you may want to enforce "power off" flow.
    # We'll allow everything here; UI tests can still assert "power-off only in parked_ready".
    return PolicyResult(True)

