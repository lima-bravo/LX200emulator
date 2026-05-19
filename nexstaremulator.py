#!/usr/bin/env python3
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""
nexstaremulator.py

Celestron NexStar HC / SynScan Telescope Emulator — TCP state machine.
Emulates a Celestron Advanced GT (model 6) over TCP.

Compatible with SkySafari, Stellarium, and ScopeBoss — connect as
"Celestron NexStar" to localhost at the chosen port.

Usage:
    uv run nexstaremulator.py <port>           # listen on TCP port
    uv run nexstaremulator.py test             # run self-tests
    uv run nexstaremulator.py 4031 --emulate syncscan

NexStar HC protocol reference:
    Celestron NexStar Communication Protocol v1.2 (2009)
    All query responses end with '#'.  Commands have no terminator
    (SkySafari appends '#' as a delimiter — that is handled gracefully).

Coordinate encoding (32-bit precise mode):
    angle → int(angle / 360.0 × 2³²) & 0xFFFFFFFF, 8 uppercase hex chars
    RA is encoded as its degree-equivalent (hours × 15).
    Declination is encoded as positive angle: negative dec → dec + 360°,
    and decoded back: value > 180° → value − 360°.
"""

import argparse
import datetime
import math
import socket
import sys
import threading
import time


# ---------------------------------------------------------------------------
# Emulator profiles
# ---------------------------------------------------------------------------

EMULATOR_PROFILES = {
    'advancedgt': {
        'model_code':  6,
        'model_name':  'Advanced GT',
        'firmware':    '4.0',
        'description': 'Celestron Advanced GT (default)',
    },
    'cge': {
        'model_code':  14,
        'model_name':  'CGE',
        'firmware':    '4.0',
        'description': 'Celestron CGE',
    },
    'cgepro': {
        'model_code':  20,
        'model_name':  'CGE Pro',
        'firmware':    '4.0',
        'description': 'Celestron CGE Pro',
    },
    'avx': {
        'model_code':  22,
        'model_name':  'Advanced VX',
        'firmware':    '4.0',
        'description': 'Celestron Advanced VX',
    },
    'syncscan': {
        'model_code':  6,
        'model_name':  'SynScan (EQ6)',
        'firmware':    '3.37',
        'description': 'Sky-Watcher SynScan (NexStar-compatible)',
    },
}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def plog(msg: str) -> None:
    ts = datetime.datetime.now().strftime('%Y-%m-%d@%H:%M:%S')
    print(f'[{ts}] {msg}')


# ---------------------------------------------------------------------------
# Coordinate math  (shared with lx200emulator_v2 — keep in sync)
# ---------------------------------------------------------------------------

def julian_date(dt: datetime.datetime) -> float:
    """Julian date from a UTC datetime."""
    a = (14 - dt.month) // 12
    y = dt.year + 4800 - a
    m = dt.month + 12 * a - 3
    jdn = (dt.day + (153 * m + 2) // 5 + 365 * y
           + y // 4 - y // 100 + y // 400 - 32045)
    return jdn + (dt.hour - 12) / 24.0 + dt.minute / 1440.0 + dt.second / 86400.0


def gmst_hours(dt: datetime.datetime) -> float:
    """Greenwich Mean Sidereal Time in hours for a UTC datetime."""
    jd = julian_date(dt)
    t  = (jd - 2451545.0) / 36525.0
    gmst = (6.697374558
            + 2400.0513369 * t
            + 0.0000258622 * t ** 2
            - 1.7222e-9    * t ** 3)
    return gmst % 24.0


def radec_to_altaz(ra_h: float, dec_deg: float,
                   lat_deg: float, lon_deg: float,
                   utc_dt: datetime.datetime) -> tuple:
    """RA/Dec → (alt_deg, az_deg).  Az measured N through E."""
    gmst  = gmst_hours(utc_dt)
    lst   = (gmst + lon_deg / 15.0) % 24.0
    ha    = ((lst - ra_h) % 24.0) * 15.0          # hour angle (degrees)

    ha_r  = math.radians(ha)
    dec_r = math.radians(dec_deg)
    lat_r = math.radians(lat_deg)

    sin_alt = (math.sin(dec_r) * math.sin(lat_r)
               + math.cos(dec_r) * math.cos(lat_r) * math.cos(ha_r))
    alt_r   = math.asin(max(-1.0, min(1.0, sin_alt)))

    cos_alt = math.cos(alt_r)
    if abs(cos_alt) < 1e-10:
        return math.degrees(alt_r), 0.0

    cos_az = ((math.sin(dec_r) - math.sin(alt_r) * math.sin(lat_r))
              / (cos_alt * math.cos(lat_r)))
    az_deg = math.degrees(math.acos(max(-1.0, min(1.0, cos_az))))
    if math.sin(ha_r) > 0:
        az_deg = 360.0 - az_deg

    return math.degrees(alt_r), az_deg


def altaz_to_radec(alt_deg: float, az_deg: float,
                   lat_deg: float, lon_deg: float,
                   utc_dt: datetime.datetime) -> tuple:
    """Alt/Az → (ra_h, dec_deg)."""
    gmst  = gmst_hours(utc_dt)
    lst   = (gmst + lon_deg / 15.0) % 24.0

    alt_r = math.radians(alt_deg)
    az_r  = math.radians(az_deg)
    lat_r = math.radians(lat_deg)

    sin_dec = (math.sin(alt_r) * math.sin(lat_r)
               + math.cos(alt_r) * math.cos(lat_r) * math.cos(az_r))
    dec_r   = math.asin(max(-1.0, min(1.0, sin_dec)))
    dec_deg = math.degrees(dec_r)

    cos_dec = math.cos(dec_r)
    if abs(cos_dec) < 1e-10:
        return lst % 24.0, dec_deg

    cos_ha = ((math.sin(alt_r) - math.sin(dec_r) * math.sin(lat_r))
              / (cos_dec * math.cos(lat_r)))
    ha_deg = math.degrees(math.acos(max(-1.0, min(1.0, cos_ha))))
    if math.sin(az_r) > 0:
        ha_deg = 360.0 - ha_deg

    ra_h = (lst - ha_deg / 15.0) % 24.0
    return ra_h, dec_deg


# ---------------------------------------------------------------------------
# NexStar coordinate encoding / decoding
# ---------------------------------------------------------------------------

_2_32 = 4_294_967_296.0   # 2³² — NOT UInt32.max (off-by-one)


def encode_angle(degrees: float) -> str:
    """Encode 0–360° as 8 uppercase hex chars."""
    norm = degrees % 360.0
    if norm < 0.0:
        norm += 360.0
    raw = int(norm / 360.0 * _2_32) & 0xFFFF_FFFF
    return f'{raw:08X}'


def decode_angle(hex8: str) -> float:
    """Decode 8 hex chars to 0–360°."""
    try:
        raw = int(hex8, 16)
    except ValueError:
        return 0.0
    return raw / _2_32 * 360.0


def encode_ra(ra_hours: float) -> str:
    return encode_angle(ra_hours * 15.0)


def decode_ra(hex8: str) -> float:
    return decode_angle(hex8) / 15.0


def encode_dec(dec_deg: float) -> str:
    return encode_angle(dec_deg if dec_deg >= 0.0 else dec_deg + 360.0)


def decode_dec(hex8: str) -> float:
    deg = decode_angle(hex8)
    return deg - 360.0 if deg > 180.0 else deg


def encode_angle16(degrees: float) -> str:
    """Encode 0–360° as 4 uppercase hex chars (16-bit low-precision mode)."""
    norm = degrees % 360.0
    if norm < 0.0:
        norm += 360.0
    raw = int(norm / 360.0 * 65536) & 0xFFFF
    return f'{raw:04X}'


def decode_angle16(hex4: str) -> float:
    try:
        raw = int(hex4, 16)
    except ValueError:
        return 0.0
    return raw / 65536.0 * 360.0


# ---------------------------------------------------------------------------
# NexStar state machine
# ---------------------------------------------------------------------------

class NexStarStateMachine:
    """
    Models a Celestron Advanced GT mount.

    Default position: Crab Nebula (M1) from Amsterdam, same as ScopeSimulator.swift.
    """

    def __init__(self, mode: str = 'advancedgt') -> None:
        profile       = EMULATOR_PROFILES.get(mode, EMULATOR_PROFILES['advancedgt'])
        self.model_code: int   = profile['model_code']
        self.firmware:   str   = profile['firmware']
        self.mode:       str   = mode

        # Telescope state
        self.ra_hours:    float = 5.5756      # Crab Nebula  05h34m32s
        self.dec_degrees: float = 22.0144     # Crab Nebula  +22°00′52″
        self.alt_degrees: float = 52.0
        self.az_degrees:  float = 178.0

        # Site
        self.lat_degrees: float =  52.5       # Amsterdam
        self.lon_degrees: float =   4.9       # East-positive

        # Time
        self.utc_offset:  float =   1.0       # CET (hours ahead of UTC)

        # Mount status
        self.tracking:    bool  = True
        self.tracking_mode: int = 2           # 0=off 1=AltAz 2=EQ-N 3=EQ-S
        self.aligned:     bool  = True
        self.slewing:     bool  = False

        # GoTo target (set by 'r'/'b' commands)
        self._target_ra:  float | None = None
        self._target_dec: float | None = None

        # Slew thread
        self._slew_lock   = threading.Lock()
        self._slew_thread: threading.Thread | None = None

        # Send buffer
        self._send_buf: str = ''
        self._buf_lock  = threading.Lock()

    # ------------------------------------------------------------------
    # Send buffer
    # ------------------------------------------------------------------

    def _push(self, data: str) -> None:
        with self._buf_lock:
            self._send_buf += data

    def has_data(self) -> bool:
        with self._buf_lock:
            return bool(self._send_buf)

    def pop_from_send_buffer(self, length: int = 64) -> str:
        with self._buf_lock:
            chunk = self._send_buf[:length]
            self._send_buf = self._send_buf[length:]
            return chunk

    def clear_send_buffer(self) -> None:
        with self._buf_lock:
            self._send_buf = ''

    # ------------------------------------------------------------------
    # Time helpers
    # ------------------------------------------------------------------

    def _utc_now(self) -> datetime.datetime:
        return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    def _altaz(self) -> tuple:
        """Compute current Alt/Az from stored RA/Dec."""
        return radec_to_altaz(
            self.ra_hours, self.dec_degrees,
            self.lat_degrees, self.lon_degrees,
            self._utc_now(),
        )

    # ------------------------------------------------------------------
    # Slew simulation
    # ------------------------------------------------------------------

    def _start_slew_to_target(self) -> None:
        """Start a background thread that slews ra/dec toward the target."""
        with self._slew_lock:
            if self._slew_thread and self._slew_thread.is_alive():
                return    # already slewing
            self._slew_thread = threading.Thread(
                target=self._slew_loop, daemon=True)
            self._slew_thread.start()

    def _slew_loop(self) -> None:
        self.slewing = True
        plog(f'  Slewing to RA={self._target_ra:.4f}h  Dec={self._target_dec:.4f}°')
        time.sleep(2.0)          # simulate slew duration
        if self._target_ra is not None:
            self.ra_hours    = self._target_ra
            self.dec_degrees = self._target_dec
            alt, az = self._altaz()
            self.alt_degrees = alt
            self.az_degrees  = az
        self.slewing = False
        plog(f'  Slew complete  RA={self.ra_hours:.4f}h  Dec={self.dec_degrees:.4f}°')

    # ------------------------------------------------------------------
    # Command dispatcher
    # ------------------------------------------------------------------

    def process_command(self, cmd: str) -> None:
        """
        Process one NexStar command string (first char = command byte).
        cmd has already had any trailing '#' stripped.
        """
        plog(f'CMD [{cmd!r}]')
        response = self._dispatch(cmd)
        if response is not None:
            self._push(response)

    def _dispatch(self, cmd: str) -> str | None:
        if not cmd:
            return None

        first = cmd[0]
        body  = cmd[1:]

        # ------------------------------------------------------------------
        # Device info
        # ------------------------------------------------------------------
        if first == 'V':
            # Firmware version string, terminated with '#'
            return f'{self.firmware}#'

        if first == 'v':
            # Detailed version (major.minor.build as bytes) — return 6-byte variant
            # For simplicity return same as V
            return f'{self.firmware}#'

        if first == 'm':
            # Model code — single byte (binary) + '#'
            return bytes([self.model_code]).decode('latin-1') + '#'

        # ------------------------------------------------------------------
        # Position queries
        # ------------------------------------------------------------------
        if first == 'e':
            # Precise RA/Dec (32-bit)
            ra_hex  = encode_ra(self.ra_hours)
            dec_hex = encode_dec(self.dec_degrees)
            return f'{ra_hex},{dec_hex}#'

        if first == 'E':
            # Low-precision RA/Dec (16-bit)
            ra_hex  = encode_angle16(self.ra_hours * 15.0)
            dec_hex = encode_angle16(self.dec_degrees if self.dec_degrees >= 0
                                     else self.dec_degrees + 360.0)
            return f'{ra_hex},{dec_hex}#'

        if first == 'z':
            # Precise Az/Alt (32-bit) — Az first, then Alt
            alt, az = self._altaz()
            self.alt_degrees = alt
            self.az_degrees  = az
            az_hex  = encode_angle(az)
            alt_hex = encode_dec(alt)
            return f'{az_hex},{alt_hex}#'

        if first == 'Z':
            # Low-precision Az/Alt (16-bit)
            alt, az = self._altaz()
            az_hex  = encode_angle16(az)
            alt_hex = encode_angle16(alt if alt >= 0 else alt + 360.0)
            return f'{az_hex},{alt_hex}#'

        # ------------------------------------------------------------------
        # GoTo RA/Dec  'r XXXXXXXX,YYYYYYYY'
        # ------------------------------------------------------------------
        if first == 'r':
            parts = body.split(',')
            if len(parts) == 2:
                try:
                    self._target_ra  = decode_ra(parts[0][:8])
                    self._target_dec = decode_dec(parts[1][:8])
                    self._start_slew_to_target()
                except Exception:
                    pass
            return None   # fire-and-forget — no response until query

        # ------------------------------------------------------------------
        # GoTo Alt/Az  'b XXXXXXXX,YYYYYYYY'
        # ------------------------------------------------------------------
        if first == 'b':
            parts = body.split(',')
            if len(parts) == 2:
                try:
                    az  = decode_angle(parts[0][:8])
                    alt = decode_dec(parts[1][:8])
                    ra, dec = altaz_to_radec(alt, az,
                                             self.lat_degrees, self.lon_degrees,
                                             self._utc_now())
                    self._target_ra  = ra
                    self._target_dec = dec
                    self._start_slew_to_target()
                except Exception:
                    pass
            return None

        # ------------------------------------------------------------------
        # Cancel GoTo
        # ------------------------------------------------------------------
        if first == 'M':
            with self._slew_lock:
                self.slewing     = False
                self._target_ra  = None
                self._target_dec = None
            return None

        # ------------------------------------------------------------------
        # Status queries
        # ------------------------------------------------------------------
        if first == 'L':
            # Is GoTo in progress?  '0' = idle, '1' = slewing
            return ('1' if self.slewing else '0') + '#'

        if first == 'J':
            # Is alignment complete?
            return ('1' if self.aligned else '0') + '#'

        if first == 't':
            # Get tracking mode  (0=off, 1=AltAz, 2=EQ-N, 3=EQ-S) + '#'
            mode = self.tracking_mode if self.tracking else 0
            return bytes([mode]).decode('latin-1') + '#'

        if first == 'T':
            # Set tracking mode — one data byte follows
            if body:
                try:
                    mode = ord(body[0])
                    self.tracking_mode = mode
                    self.tracking      = (mode != 0)
                    plog(f'  Tracking mode → {mode}')
                except Exception:
                    pass
            return None

        # ------------------------------------------------------------------
        # Location ('w' get, 'W' set)
        # ------------------------------------------------------------------
        if first == 'w':
            lat_abs = abs(self.lat_degrees)
            lon_abs = abs(self.lon_degrees)
            lat_sign: int = 0 if self.lat_degrees >= 0 else 1
            lon_sign: int = 0 if self.lon_degrees >= 0 else 1
            data = bytes([
                int(lat_abs),
                int(lat_abs * 60) % 60,
                int(lat_abs * 3600) % 60,
                lat_sign,
                int(lon_abs),
                int(lon_abs * 60) % 60,
                int(lon_abs * 3600) % 60,
                lon_sign,
                0,          # pad byte
            ])
            return data.decode('latin-1') + '#'

        if first == 'W':
            # 9 bytes: latDeg latMin latSec latSign lonDeg lonMin lonSec lonSign 0
            raw = body.encode('latin-1')
            if len(raw) >= 8:
                lat = raw[0] + raw[1] / 60.0 + raw[2] / 3600.0
                lon = raw[4] + raw[5] / 60.0 + raw[6] / 3600.0
                self.lat_degrees = -lat if raw[3] else lat
                self.lon_degrees = -lon if raw[7] else lon
                plog(f'  Location set: lat={self.lat_degrees:.4f}°  '
                     f'lon={self.lon_degrees:.4f}°')
            return None

        # ------------------------------------------------------------------
        # Time ('h' get, 'H' set)
        # Format: hour min sec month day year offset(+128) dst
        # ------------------------------------------------------------------
        if first == 'h':
            now = self._utc_now() + datetime.timedelta(hours=self.utc_offset)
            dst_flag = 0
            offset_byte = int(self.utc_offset) + 128
            data = bytes([
                now.hour, now.minute, now.second,
                now.month, now.day, now.year % 100,
                offset_byte & 0xFF,
                dst_flag,
            ])
            return data.decode('latin-1') + '#'

        if first == 'H':
            raw = body.encode('latin-1')
            if len(raw) >= 8:
                offset = raw[6] - 128
                self.utc_offset = float(offset)
                plog(f'  Time set: UTC offset → {self.utc_offset:+.0f}h')
            return None

        # ------------------------------------------------------------------
        # AUX pass-through 'P' — slew / focus / etc.
        # Bytes: P length dest_id cmd_id data1 data2 data3 response_length
        # Just acknowledge silently; no actual hardware to drive.
        # ------------------------------------------------------------------
        if first == 'P':
            return None

        # ------------------------------------------------------------------
        # Sync 'S' — not standard NexStar HC but some apps use it
        # ------------------------------------------------------------------
        if first == 'S':
            parts = body.split(',')
            if len(parts) == 2:
                try:
                    self.ra_hours    = decode_ra(parts[0][:8])
                    self.dec_degrees = decode_dec(parts[1][:8])
                    plog(f'  Sync to RA={self.ra_hours:.4f}h  '
                         f'Dec={self.dec_degrees:.4f}°')
                except Exception:
                    pass
            return None

        # ------------------------------------------------------------------
        # Echo / handshake 'K' — reply with same byte
        # ------------------------------------------------------------------
        if first == 'K':
            echo = body[0] if body else '\x00'
            return echo + '#'

        # ------------------------------------------------------------------
        # Unknown / unimplemented — silent
        # ------------------------------------------------------------------
        plog(f'  Unknown command: {cmd!r}')
        return None


# ---------------------------------------------------------------------------
# Command framing
#
# NexStar commands have no standard terminator — but SkySafari and many
# planetarium apps append '#' as a delimiter.  We handle both cases:
#   • Single-char queries  (V m e E z Z L J t M): process when 1 byte ready
#   • Multi-byte commands with '#' delimiter  (r b T W H P S K): wait for '#'
#     OR accumulate the known fixed number of bytes.
#
# Strategy: maintain a byte buffer.  '#' bytes are treated as delimiters and
# discarded.  For each non-'#' byte received, check if it starts a
# single-char command (process immediately) or a multi-byte command
# (accumulate until we have enough bytes, or until the next '#').
# ---------------------------------------------------------------------------

# Number of bytes expected AFTER the command byte for multi-byte commands.
# Single-char commands have 0 additional bytes (process immediately).
_EXTRA_BYTES: dict[str, int] = {
    'T': 1,    # set tracking mode
    'r': 17,   # GoTo RA/Dec:  XXXXXXXX,YYYYYYYY
    'b': 17,   # GoTo Alt/Az:  XXXXXXXX,YYYYYYYY
    'P': 7,    # AUX pass-through
    'W': 9,    # set location
    'H': 8,    # set time
    'S': 17,   # sync (non-standard)
    'K': 1,    # echo
}

_SINGLE_CHAR = frozenset('VvmMeEzZLJt ch')   # commands with no additional data


def extract_command(buf: str) -> tuple[str | None, str]:
    """
    Pull one complete NexStar command off the front of `buf`.
    Returns (command_str, remaining_buf), or (None, buf) if not enough data yet.
    Leading '#' bytes are stripped silently.
    """
    # Skip delimiter bytes
    while buf and buf[0] == '#':
        buf = buf[1:]

    if not buf:
        return None, buf

    first = buf[0]
    extra = _EXTRA_BYTES.get(first, 0)

    if extra == 0:
        # Single-char command — ready immediately
        return first, buf[1:]

    # Multi-byte command: wait for extra bytes OR a '#' delimiter
    needed = 1 + extra
    if '#' in buf:
        end = buf.index('#')
        cmd = buf[:end]
        return cmd, buf[end + 1:]   # consume up to and including '#'

    if len(buf) < needed:
        return None, buf            # need more data

    return buf[:needed], buf[needed:]


# ---------------------------------------------------------------------------
# TCP server
# ---------------------------------------------------------------------------

def listen_and_process(client: socket.socket,
                        sm: NexStarStateMachine) -> None:
    """Handle one client connection until it disconnects."""
    recv_buf = ''

    while True:
        try:
            data = client.recv(256)
        except (ConnectionResetError, OSError):
            plog('Connection reset by client')
            break

        if not data:
            plog('Client disconnected')
            break

        chunk = data.decode('latin-1', errors='replace')
        recv_buf += chunk

        # Log incoming (suppress pure-'#' delimiter bursts)
        printable = recv_buf.replace('#', '').strip()
        if printable:
            plog(f'RX [{recv_buf!r}]')

        # Extract and dispatch all complete commands
        while True:
            cmd, recv_buf = extract_command(recv_buf)
            if cmd is None:
                break
            sm.process_command(cmd)

        # Flush outbound buffer
        while sm.has_data():
            chunk = sm.pop_from_send_buffer()
            plog(f'TX [{chunk!r}]')
            try:
                client.sendall(chunk.encode('latin-1'))
            except (BrokenPipeError, OSError):
                plog('Send failed — connection lost')
                return

        time.sleep(0.02)


def emulate_telescope(port: int, mode: str = 'advancedgt') -> None:
    profile = EMULATOR_PROFILES.get(mode, EMULATOR_PROFILES['advancedgt'])
    sm      = NexStarStateMachine(mode=mode)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', port))
    server.listen(1)
    plog(f"Emulating : {profile['description']}")
    plog(f"Firmware  : {profile['firmware']}  model code {profile['model_code']}")
    plog(f"Initial   : RA={sm.ra_hours:.4f}h  Dec={sm.dec_degrees:.4f}°  "
         f"(Crab Nebula)")
    plog(f"Site      : lat={sm.lat_degrees}°  lon={sm.lon_degrees}°  "
         f"(Amsterdam)")
    plog(f"Listening : 0.0.0.0:{port}")

    while True:
        client, addr = server.accept()
        plog(f'Client connected from {addr[0]}:{addr[1]}')
        try:
            listen_and_process(client, sm)
        finally:
            client.close()
            plog('Connection closed')


# ---------------------------------------------------------------------------
# Self-tests
# ---------------------------------------------------------------------------

def run_tests() -> None:
    print('=== NexStar coordinate encoding tests ===')
    cases = [
        ('angle 0°',    encode_angle(0),     '00000000', decode_angle, 0.0),
        ('angle 90°',   encode_angle(90),    '40000000', decode_angle, 90.0),
        ('angle 180°',  encode_angle(180),   '80000000', decode_angle, 180.0),
        ('angle 270°',  encode_angle(270),   'C0000000', decode_angle, 270.0),
        ('RA 6h',       encode_ra(6),        '40000000', decode_ra,    6.0),
        ('RA 12h',      encode_ra(12),       '80000000', decode_ra,    12.0),
        ('Dec +45°',    encode_dec(45),      None,       decode_dec,   45.0),
        ('Dec -45°',    encode_dec(-45),     'E0000000', decode_dec,   -45.0),  # -45+360=315° → 0xE0000000
        ('Dec -90°',    encode_dec(-90),     'C0000000', decode_dec,   -90.0),  # -90+360=270° → 0xC0000000
    ]
    for label, encoded, expected_hex, decoder, expected_val in cases:
        decoded = decoder(encoded)
        hex_ok  = (expected_hex is None or encoded == expected_hex)
        val_ok  = abs(decoded - expected_val) < 0.005
        ok = '✓' if hex_ok and val_ok else '✗'
        print(f'  {ok}  {label:15s}  enc={encoded}  dec={decoded:.4f}  '
              f'(expected {expected_val})')

    print()
    print('=== NexStar state machine tests ===')
    sm = NexStarStateMachine()

    def cmd(c: str) -> str:
        sm.clear_send_buffer()
        sm.process_command(c)
        buf = ''
        while sm.has_data():
            buf += sm.pop_from_send_buffer()
        return buf

    checks = [
        ('V firmware',      cmd('V'),    lambda r: r.endswith('#') and len(r) > 1),
        ('m model byte',    cmd('m'),    lambda r: r.endswith('#') and len(r) == 2
                                                    and ord(r[0]) == 6),
        ('e RA/Dec',        cmd('e'),    lambda r: r.endswith('#') and ',' in r
                                                    and len(r) == 18),
        ('z Az/Alt',        cmd('z'),    lambda r: r.endswith('#') and ',' in r
                                                    and len(r) == 18),
        ('L idle',          cmd('L'),    lambda r: r == '0#'),
        ('J aligned',       cmd('J'),    lambda r: r == '1#'),
        ('t tracking',      cmd('t'),    lambda r: r.endswith('#') and len(r) == 2),
        ('w location',      cmd('w'),    lambda r: r.endswith('#')
                                                    and r[:-1].encode('latin-1').__len__() == 9),
        ('K echo',          cmd('Kx'),   lambda r: r == 'x#'),
        ('M no resp',       cmd('M'),    lambda r: r == ''),
    ]
    for label, result, check in checks:
        ok = '✓' if check(result) else '✗'
        print(f'  {ok}  {label:20s}  → {result!r}')

    print()
    print('=== GoTo round-trip ===')
    ra_target, dec_target = 10.25, -35.5
    ra_hex  = encode_ra(ra_target)
    dec_hex = encode_dec(dec_target)
    goto_cmd = f'r{ra_hex},{dec_hex}'
    sm.clear_send_buffer()
    sm.process_command(goto_cmd)
    # Wait for slew thread
    time.sleep(2.5)
    err_ra  = abs(sm.ra_hours    - ra_target)
    err_dec = abs(sm.dec_degrees - dec_target)
    ok = '✓' if err_ra < 0.01 and err_dec < 0.01 else '✗'
    print(f'  {ok}  GoTo RA={ra_target}h Dec={dec_target}°  '
          f'→  RA={sm.ra_hours:.4f}h Dec={sm.dec_degrees:.4f}°  '
          f'(err {err_ra:.4f}h, {err_dec:.4f}°)')

    print()
    print('=== Command framing tests ===')
    frame_cases = [
        ('V single',        'V',          'V'),
        ('V with hash',     'V#',         'V'),
        ('t single',        't',          't'),
        ('r GoTo #-delim',  'r' + 'A'*8 + ',' + 'B'*8 + '#',
                             'r' + 'A'*8 + ',' + 'B'*8),
        ('hash only',       '#',          None),
    ]
    for label, buf, expected in frame_cases:
        extracted, remaining = extract_command(buf)
        ok = '✓' if extracted == expected else '✗'
        print(f'  {ok}  {label:25s}  got {extracted!r}  remaining {remaining!r}')

    print()
    print('All tests complete.')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Celestron NexStar HC telescope emulator over TCP',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Available emulation modes:\n' + '\n'.join(
            f'  {k:12s}  {v["description"]}' for k, v in EMULATOR_PROFILES.items()
        ),
    )
    parser.add_argument(
        'port',
        help='TCP port to listen on, or "test" to run self-tests',
    )
    parser.add_argument(
        '--emulate',
        metavar='MODE',
        default='advancedgt',
        choices=list(EMULATOR_PROFILES.keys()),
        help='mount model to emulate (default: advancedgt)',
    )
    args = parser.parse_args()

    if args.port == 'test':
        run_tests()
    else:
        try:
            port = int(args.port)
        except ValueError:
            parser.error(f'port must be an integer or "test", got: {args.port!r}')
        emulate_telescope(port, mode=args.emulate)


if __name__ == '__main__':
    main()
