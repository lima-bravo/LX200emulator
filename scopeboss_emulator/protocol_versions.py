from __future__ import annotations

from dataclasses import dataclass

from .config import ProtocolVersion


@dataclass(frozen=True)
class ProtocolProfile:
    name: str
    product_name: str
    firmware_version: str
    default_mount_mode: str


PROTOCOL_PROFILES: dict[ProtocolVersion, ProtocolProfile] = {
    "lx200classic": ProtocolProfile(
        name="lx200classic",
        product_name="LX200",
        firmware_version="2.0i",
        default_mount_mode="P",
    ),
    "lx200gps": ProtocolProfile(
        name="lx200gps",
        product_name="LX200GPS",
        firmware_version="4.2g",
        default_mount_mode="A",
    ),
    "autostar2": ProtocolProfile(
        name="autostar2",
        product_name="Autostar II",
        firmware_version="2.5j",
        default_mount_mode="A",
    ),
}

