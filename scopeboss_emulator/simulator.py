from __future__ import annotations

import datetime as dt
import math
import threading
import time
from dataclasses import dataclass

from .config import EmulatorConfig
from .protocol_versions import PROTOCOL_PROFILES


def _fmt_ra(ra_h: float, high_precision: bool) -> str:
    ra_h %= 24.0
    hh = int(ra_h)
    minutes_f = (ra_h - hh) * 60.0
    mm = int(minutes_f)
    if high_precision:
        ss = int((minutes_f - mm) * 60.0)
        return f"{hh:02d}:{mm:02d}:{ss:02d}#"
    tenth = int((minutes_f - mm) * 10.0)
    return f"{hh:02d}:{mm:02d}.{tenth}#"


def _fmt_dec(dec_deg: float, high_precision: bool) -> str:
    sign = "+" if dec_deg >= 0 else "-"
    abs_deg = abs(dec_deg)
    dd = int(abs_deg)
    min_f = (abs_deg - dd) * 60.0
    mm = int(min_f)
    if high_precision:
        ss = int((min_f - mm) * 60.0)
        return f"{sign}{dd:02d}*{mm:02d}'{ss:02d}#"
    return f"{sign}{dd:02d}*{mm:02d}#"


def _parse_ra(payload: str) -> float | None:
    try:
        if payload.count(":") == 2:
            hh, mm, ss = payload.split(":")
            return int(hh) + int(mm) / 60.0 + float(ss) / 3600.0
        if ":" in payload and "." in payload:
            hh, rest = payload.split(":")
            mm, tenth = rest.split(".")
            return int(hh) + int(mm) / 60.0 + int(tenth) / 600.0
    except ValueError:
        return None
    return None


def _parse_dec(payload: str) -> float | None:
    try:
        sign = 1.0
        if payload.startswith("+"):
            payload = payload[1:]
        elif payload.startswith("-"):
            payload = payload[1:]
            sign = -1.0
        normal = payload.replace("*", ":").replace("'", ":")
        parts = normal.split(":")
        deg = float(parts[0])
        minutes = float(parts[1]) if len(parts) > 1 else 0.0
        seconds = float(parts[2]) if len(parts) > 2 else 0.0
        return sign * (deg + minutes / 60.0 + seconds / 3600.0)
    except (ValueError, IndexError):
        return None


@dataclass
class MountState:
    ra_h: float = 5.5139
    dec_deg: float = -5.3911
    target_ra_h: float = 5.5139
    target_dec_deg: float = -5.3911
    tracking_enabled: bool = True
    high_precision: bool = True
    slewing: bool = False
    moving_n: bool = False
    moving_s: bool = False
    moving_e: bool = False
    moving_w: bool = False
    slew_rate: str = "S"
    homing: bool = False
    sleeping: bool = False


class TelescopeSimulator:
    def __init__(self, config: EmulatorConfig) -> None:
        self._config = config
        self._profile = PROTOCOL_PROFILES[config.protocol_version]
        self._state = MountState(
            high_precision=config.high_precision_default,
            tracking_enabled=True,
        )
        self._state.target_ra_h = self._state.ra_h
        self._state.target_dec_deg = self._state.dec_deg
        self._shutdown = False
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run_loop, daemon=True, name="lx200-sim")
        self._worker.start()

    def close(self) -> None:
        self._shutdown = True
        self._worker.join(timeout=1.0)

    def process(self, command: str | bytes) -> bytes | None:
        with self._lock:
            if command == b"\x06":
                mode = self._config.mount_mode_byte or self._profile.default_mount_mode
                return mode.encode("ascii", errors="ignore")[:1]

            if isinstance(command, bytes):
                text = command.decode("ascii", errors="ignore")
            else:
                text = command

            if not text.startswith(":") or not text.endswith("#"):
                return None
            body = text[1:-1]
            return self._dispatch(body)

    def _dispatch(self, body: str) -> bytes | None:
        if body == "U":
            self._state.high_precision = not self._state.high_precision
            return None
        if body == "D":
            return (b"|#" if self._state.slewing else b"#")
        if body == "GVP":
            return f"{self._profile.product_name}#".encode()
        if body == "GVN":
            return f"{self._profile.firmware_version}#".encode()
        if body == "GVD":
            return dt.datetime.now().strftime("%b %d %Y#").encode()
        if body == "GVT":
            return dt.datetime.now().strftime("%H:%M:%S#").encode()
        if body == "GW":
            tracking = "T" if self._state.tracking_enabled else "N"
            mode = self._config.mount_mode_byte or self._profile.default_mount_mode
            return f"{mode}{tracking}1#".encode()
        if body == "GR":
            return _fmt_ra(self._state.ra_h, self._state.high_precision).encode()
        if body == "GD":
            return _fmt_dec(self._state.dec_deg, self._state.high_precision).encode()
        if body.startswith("Sr"):
            parsed = _parse_ra(body[2:])
            if parsed is None or not (0 <= parsed < 24):
                return b"0"
            self._state.target_ra_h = parsed
            return b"1"
        if body.startswith("Sd"):
            parsed = _parse_dec(body[2:])
            if parsed is None or not (-90 <= parsed <= 90):
                return b"0"
            self._state.target_dec_deg = parsed
            return b"1"
        if body == "CM":
            self._state.ra_h = self._state.target_ra_h
            self._state.dec_deg = self._state.target_dec_deg
            return b"Coordinates     matched.       #"
        if body == "MS":
            self._state.slewing = True
            return b"0"
        if body == "Mn":
            self._state.moving_n = True
            self._state.moving_s = False
            return None
        if body == "Ms":
            self._state.moving_s = True
            self._state.moving_n = False
            return None
        if body == "Me":
            self._state.moving_e = True
            self._state.moving_w = False
            return None
        if body == "Mw":
            self._state.moving_w = True
            self._state.moving_e = False
            return None
        if body.startswith("Q"):
            self._state.moving_n = self._state.moving_s = False
            self._state.moving_e = self._state.moving_w = False
            self._state.slewing = False
            return None
        if body in {"RG", "RC", "RM", "RS"}:
            self._state.slew_rate = body[1]
            return None
        # ---- Home / park / sleep commands --------------------------------
        # :hS# / :hF# — start seek-home (no response per protocol)
        if body in {"hS", "hF"}:
            self._state.homing = True
            return None
        # :h?# — query home status: 0=failed, 1=found, 2=in-progress
        if body == "h?":
            code = "2" if getattr(self._state, "homing", False) else "1"
            return code.encode()
        # :hP# — park scope (no response)
        if body == "hP":
            return None
        # :hN# — sleep scope (no response)
        if body == "hN":
            self._state.sleeping = True
            return None
        # :hW# — wake scope (no response)
        if body == "hW":
            self._state.sleeping = False
            return None
        # ---- Focus -------------------------------------------------------
        if body in {"F+", "F-", "FQ", "FF", "FS",
                    "F1", "F2", "F3", "F4"}:
            return None
        # ---- Slew rates (numeric) ----------------------------------------
        if len(body) == 2 and body[0] == "R" and body[1].isdigit():
            return None
        # ---- Alignment ---------------------------------------------------
        if body in {"AL", "AP", "AA", "Aa"}:
            return None
        # ---- Initialize --------------------------------------------------
        if body == "I":
            return None
        return b"\x15"

    def _run_loop(self) -> None:
        tick_s = self._config.motion.tick_ms / 1000.0
        while not self._shutdown:
            with self._lock:
                self._tick_locked(tick_s)
            time.sleep(tick_s)

    def _rate_deg_s(self) -> float:
        rates = {
            "G": self._config.motion.guide_deg_per_s,
            "C": self._config.motion.center_deg_per_s,
            "M": self._config.motion.find_deg_per_s,
            "S": self._config.motion.slew_deg_per_s,
        }
        return rates.get(self._state.slew_rate, self._config.motion.slew_deg_per_s)

    def _tick_locked(self, tick_s: float) -> None:
        rate = self._rate_deg_s()
        delta_deg = rate * tick_s
        if self._state.tracking_enabled and not self._state.slewing and not any(
            [self._state.moving_e, self._state.moving_w]
        ):
            sidereal_deg_s = 360.0 / 86164.0905
            self._state.ra_h = (self._state.ra_h + sidereal_deg_s * tick_s / 15.0) % 24.0

        if self._state.moving_n:
            self._state.dec_deg = min(90.0, self._state.dec_deg + delta_deg)
        if self._state.moving_s:
            self._state.dec_deg = max(-90.0, self._state.dec_deg - delta_deg)
        if self._state.moving_e:
            self._state.ra_h = (self._state.ra_h + delta_deg / 15.0) % 24.0
        if self._state.moving_w:
            self._state.ra_h = (self._state.ra_h - delta_deg / 15.0) % 24.0

        if self._state.slewing:
            d_ra_h = (self._state.target_ra_h - self._state.ra_h + 12.0) % 24.0 - 12.0
            d_dec = self._state.target_dec_deg - self._state.dec_deg
            d_ra_deg = d_ra_h * 15.0
            distance = math.hypot(d_ra_deg, d_dec)
            if distance < 1e-4:
                self._state.ra_h = self._state.target_ra_h
                self._state.dec_deg = self._state.target_dec_deg
                self._state.slewing = False
                return
            step = min(distance, delta_deg)
            frac = 0.0 if distance == 0 else step / distance
            self._state.ra_h = (self._state.ra_h + d_ra_h * frac) % 24.0
            self._state.dec_deg += d_dec * frac

