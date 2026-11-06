from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Literal

ProtocolVersion = Literal["lx200classic", "lx200gps", "autostar2"]


@dataclass(frozen=True)
class SiteConfig:
    latitude_deg: float = 40.75
    longitude_deg: float = -74.0
    utc_offset_hours: float = -5.0
    dst: bool = False


@dataclass(frozen=True)
class MotionConfig:
    guide_deg_per_s: float = 0.002
    center_deg_per_s: float = 0.1
    find_deg_per_s: float = 1.0
    slew_deg_per_s: float = 4.0
    tick_ms: int = 50


@dataclass(frozen=True)
class EmulatorConfig:
    protocol_version: ProtocolVersion = "lx200gps"
    max_frame_len: int = 256
    mount_mode_byte: str = "A"
    high_precision_default: bool = True
    high_limit_deg: float = 85.0
    low_limit_deg: float = 5.0
    site: SiteConfig = SiteConfig()
    motion: MotionConfig = MotionConfig()


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return default


def load_config(path: str | None) -> EmulatorConfig:
    if not path:
        return EmulatorConfig()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    site_data = data.get("site", {})
    motion_data = data.get("motion", {})
    return EmulatorConfig(
        protocol_version=data.get("protocol_version", "lx200gps"),
        max_frame_len=int(data.get("max_frame_len", 256)),
        mount_mode_byte=str(data.get("mount_mode_byte", "A"))[:1] or "A",
        high_precision_default=_as_bool(
            data.get("high_precision_default", True),
            default=True,
        ),
        high_limit_deg=float(data.get("high_limit_deg", 85.0)),
        low_limit_deg=float(data.get("low_limit_deg", 5.0)),
        site=SiteConfig(
            latitude_deg=float(site_data.get("latitude_deg", 40.75)),
            longitude_deg=float(site_data.get("longitude_deg", -74.0)),
            utc_offset_hours=float(site_data.get("utc_offset_hours", -5.0)),
            dst=_as_bool(site_data.get("dst", False), default=False),
        ),
        motion=MotionConfig(
            guide_deg_per_s=float(motion_data.get("guide_deg_per_s", 0.002)),
            center_deg_per_s=float(motion_data.get("center_deg_per_s", 0.1)),
            find_deg_per_s=float(motion_data.get("find_deg_per_s", 1.0)),
            slew_deg_per_s=float(motion_data.get("slew_deg_per_s", 4.0)),
            tick_ms=int(motion_data.get("tick_ms", 50)),
        ),
    )
