
from __future__ import annotations
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Optional, List

class LinkState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECOVERING = auto()

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

@dataclass(frozen=True)
class Timers:
    park_done_at_ms: Optional[int] = None
    home_done_at_ms: Optional[int] = None

@dataclass(frozen=True)
class TelescopeState:
    now_ms: int = 0
    link: LinkState = LinkState.DISCONNECTED
    motion: MotionState = MotionState.POWERED_OFF
    home_status: HomeStatus = HomeStatus.UNKNOWN
    focus: FocusState = FocusState.FOCUS_IDLE
    focus_speed: Optional[int] = None  # 1..4
    timers: Timers = Timers()
    last_error: Optional[str] = None

