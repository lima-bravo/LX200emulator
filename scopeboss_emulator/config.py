### `scopeboss_emulator/config.py`

from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Optional, List, Literal


HomeMode = Literal["scripted", "timer"]

@dataclass(frozen=True)
class HomeBehavior:
    mode: HomeMode = "timer"
    # scripted sequence of codes returned by :h?# (2=in progress,1=found,0=failed)
    script: Optional[List[int]] = None
    duration_ms: int = 2000  # if timer-based, how long until found/failed
    succeed: bool = True     # timer-based outcome

@dataclass(frozen=True)
class EmulatorConfig:
    # ACK (0x06) mount mode indicator. The 2010 protocol text shows D=AltAz, L=Land, P=Polar. [4](https://astromart.com/classifieds/astromart-classifieds/telescope-catadioptric/show/meade-lx200-gps-autostar-with-feather-touch-focuser)
    mount_mode_byte: str = "P"

    # Parser
    max_frame_len: int = 256

    # Busy/NAK behavior
    nak_on_lock: bool = True

    # Home/Park
    park_duration_ms: int = 5000
    home: HomeBehavior = HomeBehavior()

    # Response formatting for :h?# (protocol defines codes but not terminator in cited snippet) [2](http://company7.com/library/meade/LX200CommandSet.pdf)
    home_status_append_hash: bool = False

def load_config(path: str | None) -> EmulatorConfig:
    if not path:
        return EmulatorConfig()
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    home_data = data.get("home", {})
    home = HomeBehavior(
        mode=home_data.get("mode", "timer"),
        script=home_data.get("script"),
        duration_ms=int(home_data.get("duration_ms", 2000)),
        succeed=bool(home_data.get("succeed", True)),
    )
    return EmulatorConfig(
        mount_mode_byte=data.get("mount_mode_byte", "P"),
        max_frame_len=int(data.get("max_frame_len", 256)),
        nak_on_lock=bool(data.get("nak_on_lock", True)),
        park_duration_ms=int(data.get("park_duration_ms", 5000)),
        home=home,
        home_status_append_hash=bool(data.get("home_status_append_hash", False)),
    )
