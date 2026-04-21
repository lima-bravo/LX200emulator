#!/usr/bin/env python3
"""
lx200emulator_v2.py

LX200 GPS Telescope Emulator — protocol-driven state machine
Based on Meade Telescope Serial Command Protocol (2012.01) / MeadeLX200protocol.json

Emulates a Meade LX200 GPS over TCP (pair with ser2net or connect directly).
Maintains full telescope state: RA/Dec, AltAz (computed via coordinate math),
site info, mount mode, tracking state, slew state, precision mode, etc.

Usage:
    python lx200emulator_v2.py <port>    # run server on given TCP port
    python lx200emulator_v2.py test      # run self-tests
"""

import argparse
import socket
import time
import datetime
import sys
import math
import threading
import re


# ---------------------------------------------------------------------------
# Emulator profiles — one per supported telescope model
# ---------------------------------------------------------------------------

EMULATOR_PROFILES = {
    'lx200gps': {
        'product_name':    'LX200GPS',
        'firmware':        '4.2g',
        'mount_default':   'A',
        'description':     'Meade LX200 GPS (default)',
    },
    'lx200classic': {
        'product_name':    'LX200',
        'firmware':        '2.0i',
        'mount_default':   'P',
        'description':     'Meade LX200 classic (< 16")',
    },
    'lx200_16': {
        'product_name':    'LX200 16',
        'firmware':        '2.0i',
        'mount_default':   'P',
        'description':     'Meade LX200 classic 16"',
    },
    'autostar': {
        'product_name':    'Autostar',
        'firmware':        '3.1Ef',
        'mount_default':   'A',
        'description':     'Meade Autostar (ETX / DS series)',
    },
    'autostar2': {
        'product_name':    'Autostar II',
        'firmware':        '2.5j',
        'mount_default':   'A',
        'description':     'Meade Autostar II (LX200ACF / RCX)',
    },
}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def plog(msg: str):
    ts = datetime.datetime.now().strftime('%Y-%m-%d@%H:%M:%S')
    print(f'[{ts}] {msg}')


# ---------------------------------------------------------------------------
# Coordinate conversion
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
    t = (jd - 2451545.0) / 36525.0
    gmst = (6.697374558
            + 2400.0513369 * t
            + 0.0000258622 * t ** 2
            - 1.7222e-9 * t ** 3)
    return gmst % 24.0


def radec_to_altaz(ra_h: float, dec_deg: float,
                   lat_deg: float, lon_deg: float,
                   utc_dt: datetime.datetime) -> tuple:
    """
    Convert RA/Dec (hours/degrees J2000) to Alt/Az for a given site and time.
    Returns (alt_deg, az_deg) — az measured N through E.
    """
    gmst = gmst_hours(utc_dt)
    lst = (gmst + lon_deg / 15.0) % 24.0
    ha_deg = ((lst - ra_h) % 24.0) * 15.0      # hour angle in degrees

    ha_r  = math.radians(ha_deg)
    dec_r = math.radians(dec_deg)
    lat_r = math.radians(lat_deg)

    sin_alt = (math.sin(dec_r) * math.sin(lat_r)
               + math.cos(dec_r) * math.cos(lat_r) * math.cos(ha_r))
    alt_r = math.asin(max(-1.0, min(1.0, sin_alt)))

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
    """Convert Alt/Az to RA/Dec (hours/degrees). Returns (ra_h, dec_deg)."""
    gmst = gmst_hours(utc_dt)
    lst  = (gmst + lon_deg / 15.0) % 24.0

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
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_ra_high(ra_h: float) -> str:
    """HH:MM:SS#"""
    ra_h = ra_h % 24.0
    h  = int(ra_h)
    mf = (ra_h - h) * 60.0
    m  = int(mf)
    s  = int((mf - m) * 60.0)
    return f'{h:02d}:{m:02d}:{s:02d}#'


def fmt_ra_low(ra_h: float) -> str:
    """HH:MM.T#  (T = tenths of minute)"""
    ra_h = ra_h % 24.0
    h  = int(ra_h)
    mf = (ra_h - h) * 60.0
    m  = int(mf)
    t  = int((mf - m) * 10.0)
    return f'{h:02d}:{m:02d}.{t}#'


def fmt_sdms_high(deg: float) -> str:
    """sDD*MM'SS#"""
    sign = '+' if deg >= 0 else '-'
    d = abs(deg)
    dd = int(d)
    mf = (d - dd) * 60.0
    mm = int(mf)
    ss = int((mf - mm) * 60.0)
    return f"{sign}{dd:02d}*{mm:02d}'{ss:02d}#"


def fmt_sdms_low(deg: float) -> str:
    """sDD*MM#"""
    sign = '+' if deg >= 0 else '-'
    d  = abs(deg)
    dd = int(d)
    mf = (d - dd) * 60.0
    mm = int(mf)
    return f'{sign}{dd:02d}*{mm:02d}#'


def fmt_az_high(az: float) -> str:
    """DDD*MM'SS#"""
    az = az % 360.0
    dd = int(az)
    mf = (az - dd) * 60.0
    mm = int(mf)
    ss = int((mf - mm) * 60.0)
    return f"{dd:03d}*{mm:02d}'{ss:02d}#"


def fmt_az_low(az: float) -> str:
    """DDD*MM#"""
    az = az % 360.0
    dd = int(az)
    mf = (az - dd) * 60.0
    mm = int(mf)
    return f'{dd:03d}*{mm:02d}#'


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_ra(s: str) -> float | None:
    """Parse HH:MM:SS or HH:MM.T into decimal hours. Returns None on error."""
    try:
        s = s.strip()
        if s.count(':') == 2:
            h, mn, sc = s.split(':')
            return int(h) + int(mn) / 60.0 + float(sc) / 3600.0
        if ':' in s and '.' in s:
            h, rest = s.split(':')
            mn, t = rest.split('.')
            return int(h) + int(mn) / 60.0 + int(t) / 600.0
        if ':' in s:
            h, mn = s.split(':')
            return int(h) + float(mn) / 60.0
    except (ValueError, AttributeError):
        pass
    return None


def parse_dec(s: str) -> float | None:
    """Parse sDD*MM:SS / sDD*MM'SS / sDD*MM into decimal degrees."""
    try:
        s = s.strip()
        sign = 1
        if s and s[0] in '+-':
            sign = -1 if s[0] == '-' else 1
            s = s[1:]
        s = s.replace("'", ':').replace('*', ':')
        parts = s.split(':')
        deg = int(parts[0])
        mn  = int(parts[1]) if len(parts) > 1 else 0
        sc  = float(parts[2]) if len(parts) > 2 else 0.0
        return sign * (deg + mn / 60.0 + sc / 3600.0)
    except (ValueError, IndexError, AttributeError):
        return None


# ===========================================================================
# Telescope State Machine
# ===========================================================================

class TelescopeStateMachine:
    """
    Full LX200 GPS telescope emulator.

    Internal representation
    -----------------------
    ra, dec          Current pointing, hours / degrees (J2000)
    target_ra/dec    Slew target
    AltAz            Computed on demand from ra/dec + site + time

    All command strings arrive as ':XYZ' (colon present, '#' already stripped).
    Responses are '#'-terminated strings per the protocol; None means no reply.
    """

    # Named slew speeds (degrees / second)
    SLEW_RATE_DEG_S = {
        'G': 0.002,   # Guide  (~0.5× sidereal)
        'C': 0.1,     # Center (~6′/s)
        'M': 1.0,     # Find   (~1°/s)
        'S': 4.0,     # Slew   (max ~4°/s)
    }

    # Handset menu structure (navigation via :EK## keypress commands)
    menu_structure = {
        'Object': {
            'Solar System': {
                'Mercury': [], 'Venus': [], 'Mars': [], 'Jupiter': [],
                'Saturn': [], 'Uranus': [], 'Neptune': [], 'Pluto': [],
                'Moon': ['Overview', 'Landing Sites', 'Craters', 'Mountains',
                         'Mare,Lakes...', 'Valleys,Rills..'],
                'Asteroids': ['Select', 'Add', 'Delete', 'Edit'],
                'Comets':    ['Select', 'Add', 'Delete', 'Edit'],
            },
            'Constellation': {},
            'Deep Sky': {
                'Named Objects': [], 'Galaxies': [], 'Nebulas': [],
                'Planetary Neb.': [], 'Star Clusters': [], 'Quasars': [],
                'Black Holes': [], 'Abell Clusters': [], 'Arp Galaxies': [],
                'MCG': [], 'UGC': [], 'Herschel': [], 'IC Objects': [],
                'NGC Objects': [], 'Caldwell Objects': [],
                'Messier Objects': [], 'Custom Catalogs': [],
            },
            'Star': {
                'Named': [], 'Hipparcos Cat.': [], 'SAO Catalog': [],
                'HD Catalog': [], 'HR Catalog': [], 'Multiple': [],
                'GCVS(variables)': [], 'Nearby': [], 'With Planets': [],
            },
            'Satellite':    ['Select', 'Add', 'Delete', 'Edit'],
            'User Object':  ['Select', 'Add', 'Delete', 'Edit'],
            'Landmarks':    ['Select', 'Add', 'Delete', 'Edit'],
            'Identify':     ['Browse', 'Start Search', 'Edit Parameters'],
        },
        'Event': {
            'Sunrise': [], "Sun's Transit": [], 'Sunset': [],
            'Moonrise': [], "Moon's Transit": [], 'Moonset': [],
            'Moon Phases': ['Next Full Moon', 'Next New Moon',
                            'Next 1st Qtr', 'Next 3rd Qtr'],
            'Meteor Showers': [], 'Solar Eclipses': [], 'Lunar Eclipses': [],
            'Min. of Algol': [], 'Autumn Equinox': [], 'Vernal Equinox': [],
            'Winter Solstice': [], 'Summer Solstice': [],
        },
        'Guided Tour': {},
        'Glossary': {},
        'Utilities': {
            'Ambient Temp.': [],
            'Timer': ['Set', 'Start/Stop'],
            'Alarm': ['Set', 'On/Off'],
            'Eyepiece Calc.': ['Field of View', 'Magnification', 'Suggest'],
            'Brightest Star': [], 'Brightness Adj.': [], 'Panel Light': [],
            'Aux Port Power': [], 'Beep': [], 'Battery Alarm': [],
            'Landmark Survey': [], 'Sleep Scope': [], 'Park Scope': [],
        },
        'Setup': {
            'Align': ['Easy', 'One Star', 'Two Star', 'Align on Home', 'Automatic'],
            'Date': [], 'Time': [], 'Daylight Savings': [], 'GPS-UTC Offset': [],
            'Telescope': {
                'Mount': [], 'Telescope Model': [], 'Focal Length': [],
                'Max Slew Rate': [], 'Mnt.Upper Limit': [], 'Mnt.Lower Limit': [],
                'Park Position': ['Use Current', 'Use Default'],
                'Calibrate Home': [],
                'Anti-Backlash': ['RA/Az Percent', 'Dec/Alt Percent',
                                   'RA/AZ Train', 'Dec/Alt Train'],
                'Cal. Sensors': [], 'Tracking Rate': [], 'Guiding Rate': [],
                'Dec. Guiding': [], 'Reverse L/R': [], 'Reverse UP/DOWN': [],
                'Home Sensors': [], 'GPS Alignment': [],
                'RA PEC':  ['On/Off', 'Restore Factory', 'Train', 'Update'],
                'DEC PEC': ['On/Off', 'Restore Factory', 'Train', 'Update'],
                'Field Derotator': [], 'High Precision': [],
            },
        },
        'Targets': {},
        'Site': {},
    }

    # -----------------------------------------------------------------------
    def __init__(self, mode: str = 'lx200gps'):
        # ---- Emulator profile ----
        profile = EMULATOR_PROFILES.get(mode, EMULATOR_PROFILES['lx200gps'])
        self.emulator_mode    = mode
        self.product_name     = profile['product_name']
        self.firmware_version = profile['firmware']

        # ---- Pointing (J2000 RA hours / Dec degrees) ----
        self.ra  = 5.5139    # Orion Nebula area
        self.dec = -5.3911

        # ---- Slew target ----
        self.target_ra  = self.ra
        self.target_dec = self.dec

        # ---- Site (New York area defaults) ----
        self.site_latitude  =  40.75   # +N degrees
        self.site_longitude = -74.0    # +E / -W degrees
        self.utc_offset     =  -5.0    # hours east of UTC
        self.dst            = False
        self.site_names     = ['Home', 'Site2', 'Site3', 'Site4']
        self.current_site   = 0

        # ---- Mount / alignment ----
        self.mount_mode      = profile['mount_default']
        self.tracking        = True
        self.alignment_stars = 0     # 0=unaligned … 3=three-star

        # ---- Precision ----
        self.high_precision = True   # False → low-precision output format

        # ---- Named slew rate + limits ----
        self.slew_rate     = 'S'    # G / C / M / S
        self.max_slew_rate = 4      # degrees/s ceiling (set by :SwN#)
        self.high_limit    = 85     # max altitude for slewing (degrees)
        self.low_limit     = 5      # min altitude for slewing (degrees)

        # ---- Continuous directional motion (:Mn/s/e/w# — :Q# stops) ----
        self.moving_n = False
        self.moving_s = False
        self.moving_e = False
        self.moving_w = False

        # ---- Slew-to-target state ----
        self.slewing      = False
        self._slew_thread = None
        self._slew_lock   = threading.Lock()

        # ---- Background motion thread ----
        self._motion_running = True
        self._motion_thread  = threading.Thread(
            target=self._motion_loop, daemon=True, name='motion')
        self._motion_thread.start()

        # ---- Object search / browse filters ----
        self.find_field_diameter = 15    # arcminutes
        self.brighter_limit      = -2.0
        self.fainter_limit       = 14.0
        self.smaller_limit       = 0     # arcmin (smallest object)
        self.larger_limit        = 999   # arcmin (largest object)
        self.quality             = 'GD'
        self.object_filter       = 'GPDCO'

        # ---- Handset display ----
        self.display_line0 = 'Select Item:    '
        self.display_line1 = ' Object         '

        # ---- Sleep ----
        self.is_sleeping = False

        # ---- Focuser ----
        self.focuser_position = 0
        self.focuser_moving   = False
        self.focuser_speed    = 2    # 1..4

        # ---- Tracking rate ----
        self.tracking_rate_hz = 60.0   # 60 Hz = one RA revolution in 24 h

        # ---- Reticule ----
        self.reticule_brightness = 5
        self.reticule_flash_rate = 0

        # ---- PEC / smart drive ----
        self.pec_ra_enabled       = False
        self.pec_dec_enabled      = False
        self.smart_drive_enabled  = False
        self.smart_mount_enabled  = False

        # ---- Anti-backlash ----
        self.backlash_alt = 0
        self.backlash_az  = 0

        # ---- Home ----
        self.home_status = 1   # 0=failed, 1=found, 2=in-progress

        # ---- Custom slew rates (:RA / :RE) ----
        self.custom_ra_rate  = None
        self.custom_dec_rate = None
        self.guide_rate_arcsec = 15.0

        # ---- Send buffer + lock ----
        self.send_buffer  = ''
        self._buf_lock    = threading.Lock()

        # ---- Menu navigation ----
        self.current_menu_keys = [list(self.menu_structure.keys())[0]]

    # =======================================================================
    # Internal helpers
    # =======================================================================

    def _utc_now(self) -> datetime.datetime:
        return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    def _local_now(self) -> datetime.datetime:
        offset = self.utc_offset + (1.0 if self.dst else 0.0)
        return self._utc_now() + datetime.timedelta(hours=offset)

    def _lst(self) -> float:
        """Local Sidereal Time in hours."""
        gmst = gmst_hours(self._utc_now())
        return (gmst + self.site_longitude / 15.0) % 24.0

    def _altaz(self) -> tuple:
        """Current Alt/Az for present RA/Dec, site, and UTC time."""
        return radec_to_altaz(
            self.ra, self.dec,
            self.site_latitude, self.site_longitude,
            self._utc_now())

    # ---- Formatters --------------------------------------------------------

    def _fmt_ra(self, ra: float) -> str:
        return fmt_ra_high(ra) if self.high_precision else fmt_ra_low(ra)

    def _fmt_dec(self, dec: float) -> str:
        return fmt_sdms_high(dec) if self.high_precision else fmt_sdms_low(dec)

    def _fmt_alt(self, alt: float) -> str:
        return self._fmt_dec(alt)

    def _fmt_az(self, az: float) -> str:
        return fmt_az_high(az) if self.high_precision else fmt_az_low(az)

    # ---- Display -----------------------------------------------------------

    def _set_display(self, line: str):
        self.display_line1 = f'{line:16}'
        plog(f'Display: [{self.display_line1}]')

    def _basic_display(self) -> str:
        return f'\x97{self.display_line0}{self.display_line1}#'

    # ---- Buffer management -------------------------------------------------

    def _push(self, s: str):
        if s is None:
            return
        with self._buf_lock:
            self.send_buffer += s

    def add_to_send_buffer(self, s: str):
        self._push(s)

    def pop_from_send_buffer(self, length: int = 16) -> str:
        with self._buf_lock:
            chunk = self.send_buffer[:length]
            self.send_buffer = self.send_buffer[length:]
        return chunk

    def has_data(self) -> bool:
        return len(self.send_buffer) > 0

    def clear_send_buffer(self):
        with self._buf_lock:
            self.send_buffer = ''

    def nack(self) -> str:
        return '\x15'

    # =======================================================================
    # Background threads
    # =======================================================================

    def _motion_loop(self):
        """Update RA/Dec continuously while any directional motion flag is set."""
        dt = 0.1   # 100 ms tick
        while self._motion_running:
            time.sleep(dt)
            if not (self.moving_n or self.moving_s
                    or self.moving_e or self.moving_w):
                continue
            rate  = self.SLEW_RATE_DEG_S.get(self.slew_rate, 1.0)
            delta = rate * dt
            if self.moving_n:
                self.dec = min(90.0, self.dec + delta)
            if self.moving_s:
                self.dec = max(-90.0, self.dec - delta)
            if self.moving_e:   # East → RA increases (objects rise from east)
                self.ra = (self.ra + delta / 15.0) % 24.0
            if self.moving_w:
                self.ra = (self.ra - delta / 15.0) % 24.0

    def _slew_to_target(self):
        """Background thread: move RA/Dec toward target at current slew speed."""
        rate = min(
            self.SLEW_RATE_DEG_S.get(self.slew_rate, 4.0),
            float(self.max_slew_rate))
        dt = 0.05
        while True:
            d_ra_h  = (self.target_ra  - self.ra  + 12.0) % 24.0 - 12.0
            d_dec   = self.target_dec - self.dec
            d_ra_deg = d_ra_h * 15.0
            dist    = math.hypot(d_ra_deg, d_dec)
            if dist < 0.0005:
                self.ra  = self.target_ra
                self.dec = self.target_dec
                break
            step = rate * dt
            if step >= dist:
                self.ra  = self.target_ra
                self.dec = self.target_dec
                break
            frac     = step / dist
            self.ra  = (self.ra  + frac * d_ra_h) % 24.0
            self.dec =  self.dec + frac * d_dec
            time.sleep(dt)
        self.slewing = False
        self._set_display('Slew complete')
        plog(f'Slew complete  RA={self.ra:.4f}h  Dec={self.dec:.4f}°')

    # =======================================================================
    # Command entry point
    # =======================================================================

    def process_command(self, cmd: str):
        """
        Dispatch one command.  cmd includes the leading ':' but NOT the '#'.
        The ACK byte (\\x06) is passed without the colon.
        """
        plog(f'CMD [{cmd}]')
        result = self._dispatch(cmd)
        if result is not None:
            self._push(result)

    # =======================================================================
    # Main dispatcher
    # =======================================================================

    def _dispatch(self, cmd: str) -> str | None:

        # ACK byte — query alignment mode
        if cmd == '\x06':
            return self.mount_mode

        if not cmd.startswith(':'):
            return None

        body = cmd[1:]   # strip ':'

        # ----------------------------------------------------------------
        # Single-character / fixed commands (checked before prefix tests)
        # ----------------------------------------------------------------
        if body == 'H':
            self.time_format_24h = not self.time_format_24h
            return None

        if body == 'I':
            self._set_display('Initializing...')
            return None

        if body == 'P':
            self.high_precision = not self.high_precision
            return 'HIGH PRECISION' if self.high_precision else 'LOW PRECISION'

        if body == 'U':
            self.high_precision = not self.high_precision
            return None

        if body == 'D':
            return '|#' if self.slewing else '#'

        # ----------------------------------------------------------------
        # Alignment :A
        # ----------------------------------------------------------------
        if body == 'Aa': return self._do_auto_align()
        if body == 'AL': self.mount_mode = 'L'; self.tracking = False;  return None
        if body == 'AP': self.mount_mode = 'P'; self.tracking = True;   return None
        if body == 'AA': self.mount_mode = 'A'; self.tracking = True;   return None

        # ----------------------------------------------------------------
        # Backlash :$B
        # ----------------------------------------------------------------
        if body.startswith('$BA'):
            try: self.backlash_alt = int(body[3:])
            except ValueError: pass
            return None
        if body.startswith('$BZ'):
            try: self.backlash_az = int(body[3:])
            except ValueError: pass
            return None

        # ----------------------------------------------------------------
        # Reticule :B
        # ----------------------------------------------------------------
        if body == 'B+':
            self.reticule_brightness = min(9, self.reticule_brightness + 1); return None
        if body == 'B-':
            self.reticule_brightness = max(0, self.reticule_brightness - 1); return None
        if body.startswith('BD'):
            try: self.reticule_flash_rate = int(body[2:])
            except ValueError: pass
            return None
        if len(body) == 2 and body[0] == 'B' and body[1].isdigit():
            self.reticule_flash_rate = int(body[1]); return None

        # ----------------------------------------------------------------
        # Sync :C
        # ----------------------------------------------------------------
        if body == 'CL': return None   # lunar sync — ignored
        if body == 'CM': return self._do_sync()

        # ----------------------------------------------------------------
        # Fan / heater :f (lowercase)
        # ----------------------------------------------------------------
        if body in ('f+', 'f-', 'fp+', 'fp-'): return None
        if body == 'fT': return '+20.000#'
        if body == 'fC': return '+18.000#'
        if body.startswith('fH'): return None

        # ----------------------------------------------------------------
        # Focuser :F (uppercase)
        # ----------------------------------------------------------------
        if body == 'F+':  self.focuser_moving = True;  return None
        if body == 'F-':  self.focuser_moving = True;  return None
        if body == 'FQ':  self.focuser_moving = False; return None
        if body == 'FB':  return '1' if self.focuser_moving else '0'
        if body == 'FF':  self.focuser_speed = 4; return None
        if body == 'FS':  self.focuser_speed = 1; return None
        if body == 'Fp':  return f'{self.focuser_position}#'
        if body.startswith('FC'): return None
        if body.startswith('FL'): return None
        if body.startswith('FP'): return None
        if len(body) == 2 and body[0] == 'F' and body[1].isdigit():
            self.focuser_speed = int(body[1]); return None

        # ----------------------------------------------------------------
        # GPS :g (lowercase)
        # ----------------------------------------------------------------
        if body == 'g+':  self.gps_enabled = True;  return None  # type: ignore[attr-defined]
        if body == 'g-':  self.gps_enabled = False; return None  # type: ignore[attr-defined]
        if body == 'gT':  return '1'
        if body == 'gps': return '$GPGGA,000000.00,0000.0000,N,00000.0000,W,0,00,,0.0,M,,,,0000*00#'

        # ----------------------------------------------------------------
        # Get telescope info :G
        # ----------------------------------------------------------------
        if body.startswith('G'):
            return self._cmd_get(body[1:])

        # ----------------------------------------------------------------
        # Home commands :h (lowercase)
        # ----------------------------------------------------------------
        if body.startswith('h'):
            return self._cmd_home(body[1:])

        # ----------------------------------------------------------------
        # Object library :L
        # ----------------------------------------------------------------
        if body.startswith('L'):
            return self._cmd_library(body[1:])

        # ----------------------------------------------------------------
        # Movement :M
        # ----------------------------------------------------------------
        if body.startswith('M'):
            return self._cmd_move(body[1:])

        # ----------------------------------------------------------------
        # Smart drive :$Q
        # ----------------------------------------------------------------
        if body.startswith('$Q'):
            return self._cmd_smart_drive(body[2:])

        # ----------------------------------------------------------------
        # Halt :Q
        # ----------------------------------------------------------------
        if body.startswith('Q'):
            return self._cmd_halt(body[1:])

        # ----------------------------------------------------------------
        # Field derotator :r (lowercase) — accept, no response
        # ----------------------------------------------------------------
        if body.startswith('r'):
            return None

        # ----------------------------------------------------------------
        # Slew rate :R
        # ----------------------------------------------------------------
        if body.startswith('R'):
            return self._cmd_slew_rate(body[1:])

        # ----------------------------------------------------------------
        # Set commands :S
        # ----------------------------------------------------------------
        if body.startswith('S'):
            return self._cmd_set(body[1:])

        # ----------------------------------------------------------------
        # Tracking :T
        # ----------------------------------------------------------------
        if body.startswith('T'):
            return self._cmd_tracking(body[1:])

        # ----------------------------------------------------------------
        # PEC readout :V
        # ----------------------------------------------------------------
        if body.startswith('VD') or body.startswith('VR'):
            return '1.0000'

        # ----------------------------------------------------------------
        # Site select :W
        # ----------------------------------------------------------------
        if len(body) == 2 and body[0] == 'W' and body[1].isdigit():
            n = int(body[1]) - 1
            if 0 <= n <= 3:
                self.current_site = n
            return None

        # ----------------------------------------------------------------
        # Help text :?
        # ----------------------------------------------------------------
        if body.startswith('?'):
            return 'No help available#'

        # ----------------------------------------------------------------
        # Handset display :E
        # ----------------------------------------------------------------
        if body.startswith('E'):
            return self._cmd_handset(body[1:])

        plog(f'Unknown command body: [{body}]')
        return self.nack()

    # =======================================================================
    # :G — get telescope information
    # =======================================================================

    def _cmd_get(self, sub: str) -> str | None:

        # --- Alignment menu entries ---
        if sub in ('0', '1', '2'):
            return '#'

        # --- Time / date ---
        if sub == 'a':   # local time 12-hour
            lt = self._local_now()
            h  = lt.hour % 12 or 12
            return f'{h:02d}:{lt.minute:02d}:{lt.second:02d}#'

        if sub == 'C':   # calendar date
            return self._local_now().strftime('%m/%d/%y') + '#'

        if sub == 'c':   # clock format
            return '24#' if self.time_format_24h else '12#'

        if sub == 'G':   # UTC offset
            sign = '+' if self.utc_offset >= 0 else ''
            return f'{sign}{int(self.utc_offset)}#'

        if sub == 'g':   # site longitude sDDD*MM
            lon  = self.site_longitude
            sign = '+' if lon >= 0 else '-'
            d    = int(abs(lon))
            m    = int((abs(lon) - d) * 60.0)
            return f'{sign}{d:03d}*{m:02d}#'

        if sub == 'H':   # DST
            return '1#' if self.dst else '0#'

        if sub == 'h':   # high limit
            return f'+{self.high_limit:02d}*#'

        if sub == 'L':   # local time 24-hour
            return self._local_now().strftime('%H:%M:%S') + '#'

        if sub == 'o':   # lower limit
            return f'{self.low_limit:02d}*#'

        if sub == 'S':   # sidereal time
            lst = self._lst()
            h   = int(lst)
            mf  = (lst - h) * 60.0
            mn  = int(mf)
            sc  = int((mf - mn) * 60.0)
            return f'{h:02d}:{mn:02d}:{sc:02d}#'

        if sub == 't':   # site latitude sDD*MM
            lat  = self.site_latitude
            sign = '+' if lat >= 0 else '-'
            d    = int(abs(lat))
            m    = int((abs(lat) - d) * 60.0)
            return f'{sign}{d:02d}*{m:02d}#'

        if sub == 'T':   # tracking rate Hz
            return f'{self.tracking_rate_hz:.1f}#'

        # --- Coordinates ---
        if sub == 'A':                       # telescope altitude
            alt, _ = self._altaz()
            return self._fmt_alt(alt)

        if sub == 'D':                       # telescope Dec
            return self._fmt_dec(self.dec)

        if sub == 'd':                       # target Dec
            return self._fmt_dec(self.target_dec)

        if sub == 'R':                       # telescope RA
            return self._fmt_ra(self.ra)

        if sub == 'r':                       # target RA
            return self._fmt_ra(self.target_ra)

        if sub == 'Z':                       # telescope azimuth
            _, az = self._altaz()
            return self._fmt_az(az)

        # --- Scope status ---
        if sub == 'W':
            tracking = 'T' if self.tracking else 'N'
            return f'{self.mount_mode}{tracking}{self.alignment_stars}#'

        # --- Firmware ---
        if sub == 'VD':
            return datetime.datetime.now().strftime('%b %d %Y') + '#'
        if sub == 'VN': return f'{self.firmware_version}#'
        if sub == 'VO': return '0#'
        if sub == 'VP': return f'{self.product_name}#'
        if sub == 'VT':
            return datetime.datetime.now().strftime('%H:%M:%S') + '#'

        # --- Find / browse limits ---
        if sub == 'b':  return f'+{self.brighter_limit:.1f}#'
        if sub == 'F':  return f'{self.find_field_diameter:03d}#'
        if sub == 'f':  return f'+{self.fainter_limit:.1f}#'
        if sub == 'l':  return f"{self.larger_limit:03d}'#"
        if sub == 's':  return f"{self.smaller_limit:03d}'#"
        if sub == 'q':  return f'{self.quality}#'
        if sub == 'y':  return f'{self.object_filter}#'

        # --- Site names ---
        if sub == 'M':  return f'{self.site_names[0]}#'
        if sub == 'N':  return f'{self.site_names[1]}#'
        if sub == 'O':  return f'{self.site_names[2]}#'
        if sub == 'P':  return f'{self.site_names[3]}#'

        # --- Backlash ---
        if sub == 'pB': return f'{self.backlash_az} {self.backlash_alt}#'
        if sub == 'pH': return '00#'
        if sub == 'pS': return '000#'

        # --- Meridian distance ---
        if sub == 'm':
            lst  = self._lst()
            dist = (lst - self.ra) % 24.0
            if dist > 12.0:
                dist -= 24.0
            sign = '+' if dist >= 0 else '-'
            d    = int(abs(dist) * 15.0)
            return f'{sign}{d:02d}*00#'

        # --- Selenographic (Moon) — not tracking Moon ---
        if sub == 'E':  return '+99*99#'
        if sub == 'e':  return '+999*99#'

        plog(f':G unknown sub [{sub}]')
        return self.nack()

    # =======================================================================
    # :S — set telescope parameters
    # =======================================================================

    def _cmd_set(self, sub: str) -> str | None:

        # :Sa sDD*MM[#]  — target altitude
        m = re.match(r'^a([+-]\d+[*:]\d+(?:[:\x27]\d+)?)$', sub)
        if m:
            val = parse_dec(m.group(1))
            if val is not None:
                ra, dec = altaz_to_radec(
                    val, 0.0,
                    self.site_latitude, self.site_longitude, self._utc_now())
                self.target_ra  = ra
                self.target_dec = dec
                return '1' if self.low_limit <= val <= self.high_limit else '0'
            return '0'

        # :Sb sMM.M  — brighter magnitude limit
        m = re.match(r'^b([+-]?\d+\.?\d*)$', sub)
        if m:
            self.brighter_limit = float(m.group(1)); return '0'

        # :SB n  — baud rate (no-op on TCP)
        if len(sub) == 2 and sub[0] == 'B' and sub[1].isdigit():
            return '1'

        # :SC MM/DD/YY  — set date
        m = re.match(r'^C(\d{2}/\d{2}/\d{2})$', sub)
        if m:
            return '1Updating Planetary Data#\r #'

        # :Sd sDD*MM[:SS]  — target Dec
        m = re.match(r'^d([+-]?\d+[*:]\d+(?:[:\x27]\d+)?)$', sub)
        if m:
            val = parse_dec(m.group(1))
            if val is not None and -90.0 <= val <= 90.0:
                self.target_dec = val; return '1'
            return '0'

        # :SF NNN  — find field diameter
        m = re.match(r'^F(\d+)$', sub)
        if m:
            d = int(m.group(1))
            self.find_field_diameter = d
            return '1' if 1 <= d <= 999 else '0'

        # :Sf sMM.M  — faint magnitude limit
        m = re.match(r'^f([+-]?\d+\.?\d*)$', sub)
        if m:
            self.fainter_limit = float(m.group(1)); return '1'

        # :Sg DDD*MM  — site longitude
        m = re.match(r'^g(\d+\*\d+)$', sub)
        if m:
            val = parse_dec(m.group(1))
            if val is not None:
                self.site_longitude = val; return '1'
            return '0'

        # :SG sHH.H  — UTC offset
        m = re.match(r'^G([+-]?\d+\.?\d*)$', sub)
        if m:
            self.utc_offset = float(m.group(1)); return '1'

        # :SH D  — DST on/off
        m = re.match(r'^H([01])$', sub)
        if m:
            self.dst = m.group(1) == '1'; return None

        # :Sh DD  — high limit
        m = re.match(r'^h(\d+)$', sub)
        if m:
            self.high_limit = int(m.group(1)); return '1'

        # :Sl NNN  — smaller size limit
        m = re.match(r'^l(\d+)$', sub)
        if m:
            self.smaller_limit = int(m.group(1)); return '1'

        # :SL HH:MM:SS  — set local time (accepted but we use system time)
        m = re.match(r'^L\d{2}:\d{2}:\d{2}$', sub)
        if m:
            return '1'

        # :Sm+/-  — smart-mount flexure
        if sub == 'm+': self.smart_mount_enabled = True;  return None
        if sub == 'm-': self.smart_mount_enabled = False; return None

        # :SM/:SN/:SO/:SP <string>  — site names
        m = re.match(r'^([MNOP])(.+)$', sub)
        if m:
            idx = 'MNOP'.index(m.group(1))
            self.site_names[idx] = m.group(2)[:15]
            return '1'

        # :So DD*  — lower limit
        m = re.match(r'^o(\d+)\*?$', sub)
        if m:
            self.low_limit = int(m.group(1)); return '1'

        # :SpB num num  — backlash values
        m = re.match(r'^pB(\d+) (\d+)$', sub)
        if m:
            self.backlash_az  = int(m.group(1))
            self.backlash_alt = int(m.group(2))
            return '1'

        # :Sq  — step quality limit
        if sub == 'q':
            cycle = ['VP', 'PR', 'FR', 'GD', 'VG', 'EX', 'SU']
            idx   = cycle.index(self.quality) if self.quality in cycle else 3
            self.quality = cycle[(idx + 1) % len(cycle)]
            return None

        # :Sr HH:MM:SS or HH:MM.T  — target RA
        m = re.match(r'^r(.+)$', sub)
        if m:
            val = parse_ra(m.group(1))
            if val is not None and 0.0 <= val < 24.0:
                self.target_ra = val; return '1'
            return '0'

        # :Ss NNN  — larger size limit
        m = re.match(r'^s(\d+)$', sub)
        if m:
            self.larger_limit = int(m.group(1)); return '1'

        # :SS HH:MM:SS  — set sidereal time (accepted)
        m = re.match(r'^S\d{2}:\d{2}:\d{2}$', sub)
        if m:
            return '1'

        # :St sDD*MM  — site latitude
        m = re.match(r'^t([+-]?\d+\*\d+)$', sub)
        if m:
            val = parse_dec(m.group(1))
            if val is not None and -90.0 <= val <= 90.0:
                self.site_latitude = val; return '1'
            return '0'

        # :ST dddd.ddd  — tracking rate Hz (Autostar II)
        m = re.match(r'^T(\d+\.?\d*)$', sub)
        if m:
            self.tracking_rate_hz = float(m.group(1)); return '2'

        # :ST+/-  — inc/dec tracking rate
        if sub == 'T+': self.tracking_rate_hz += 0.1; return None
        if sub == 'T-': self.tracking_rate_hz -= 0.1; return None

        # PEC via :S prefix
        if sub == 'TA+': self.pec_dec_enabled = True;  return None
        if sub == 'TA-': self.pec_dec_enabled = False; return None
        if sub == 'TZ+': self.pec_ra_enabled  = True;  return None
        if sub == 'TZ-': self.pec_ra_enabled  = False; return None

        # :Sw N  — max slew rate
        m = re.match(r'^w(\d)$', sub)
        if m:
            n = int(m.group(1))
            if 2 <= n <= 8:
                self.max_slew_rate = n; return '1'
            return '0'

        # :Sy GPDCO  — object filter string
        m = re.match(r'^y([GPDCOgpdco]{5})$', sub)
        if m:
            self.object_filter = m.group(1); return '1'

        # :Sz DDD*MM  — target azimuth
        m = re.match(r'^z(\d+\*\d+)$', sub)
        if m:
            val = parse_dec(m.group(1))
            if val is not None:
                alt, _ = self._altaz()
                ra, dec = altaz_to_radec(
                    alt, val,
                    self.site_latitude, self.site_longitude, self._utc_now())
                self.target_ra  = ra
                self.target_dec = dec
                return '1'
            return '0'

        plog(f':S unknown sub [{sub}]')
        return self.nack()

    # =======================================================================
    # :M — movement
    # =======================================================================

    def _cmd_move(self, sub: str) -> str | None:
        if sub == 'n': self.moving_n = True;  return None
        if sub == 's': self.moving_s = True;  return None
        if sub == 'e': self.moving_e = True;  return None
        if sub == 'w': self.moving_w = True;  return None

        if sub in ('S', 'A'):               # :MS# or :MA# — slew to target
            return self._do_slew()

        if re.match(r'^g[nsew]\d+$', sub):  # :Mgn/s/e/wDDDD# — guide pulse
            return None

        if sub.startswith('gS'):             # :MgS<x># — StarLock
            return None

        return self.nack()

    def _do_slew(self) -> str:
        alt, _ = radec_to_altaz(
            self.target_ra, self.target_dec,
            self.site_latitude, self.site_longitude,
            self._utc_now())
        if alt < self.low_limit:
            return '1Object below horizon     #'
        if alt > self.high_limit:
            return '2Object above high limit  #'
        self._set_display('Slewing...')
        self.slewing = True
        with self._slew_lock:
            if self._slew_thread is None or not self._slew_thread.is_alive():
                self._slew_thread = threading.Thread(
                    target=self._slew_to_target, daemon=True, name='slew')
                self._slew_thread.start()
        return '0'

    # =======================================================================
    # :Q — halt motion
    # =======================================================================

    def _cmd_halt(self, sub: str) -> None:
        if sub in ('', 'e', 'w', 'n', 's', '#'):
            if sub in ('', '#'):
                self.moving_n = self.moving_s = False
                self.moving_e = self.moving_w = False
                self.slewing  = False
            elif sub == 'n': self.moving_n = False
            elif sub == 's': self.moving_s = False
            elif sub == 'e': self.moving_e = False
            elif sub == 'w': self.moving_w = False
        return None

    # =======================================================================
    # :R — slew rate
    # =======================================================================

    def _cmd_slew_rate(self, sub: str) -> None:
        if sub == 'G': self.slew_rate = 'G'; return None
        if sub == 'C': self.slew_rate = 'C'; return None
        if sub == 'M': self.slew_rate = 'M'; return None
        if sub == 'S': self.slew_rate = 'S'; return None

        m = re.match(r'^A(\d+\.?\d*)$', sub)   # :RA DD.D#
        if m: self.custom_ra_rate  = float(m.group(1)); return None
        m = re.match(r'^E(\d+\.?\d*)$', sub)   # :RE DD.D#
        if m: self.custom_dec_rate = float(m.group(1)); return None
        m = re.match(r'^g(\d+\.?\d*)$', sub)   # :Rg SS.S#
        if m: self.guide_rate_arcsec = float(m.group(1)); return None

        return None

    # =======================================================================
    # :T — tracking
    # =======================================================================

    def _cmd_tracking(self, sub: str) -> None:
        if sub == '+': self.tracking_rate_hz = min(70.0, self.tracking_rate_hz + 0.1)
        elif sub == '-': self.tracking_rate_hz = max(50.0, self.tracking_rate_hz - 0.1)
        elif sub == 'L': self.tracking_rate_hz = 57.9          # lunar
        elif sub == 'Q': self.tracking_rate_hz = 60.0          # sidereal
        elif sub == 'S': self.tracking_rate_hz = 60.0          # solar (approx)
        elif sub == 'M': pass                                    # custom
        return None

    # =======================================================================
    # :h — home commands
    # =======================================================================

    def _cmd_home(self, sub: str) -> str | None:
        if sub == 'C':
            self.home_status = 2
            threading.Timer(2.0, lambda: setattr(self, 'home_status', 1)).start()
            return None
        if sub == 'F': return None
        if sub.startswith('I') and len(sub) >= 13: return '1'
        if sub == 'N':
            self.is_sleeping = True
            self._set_display('Sleeping...')
            return None
        if sub == 'P': return None   # slew to park position
        if sub == 'S': return None   # set park position
        if sub == 'W':
            self.is_sleeping = False
            self._set_display('Waking up...')
            return None
        if sub == '?':
            return str(self.home_status)
        return None

    # =======================================================================
    # :L — object library
    # =======================================================================

    def _cmd_library(self, sub: str) -> str | None:
        if sub == 'B': return None
        if sub == 'F': return None
        if sub == 'N': return None
        if sub == 'f': return '0 - Objects found#'
        if sub == 'I': return 'M31 Andromeda Galaxy#'
        if sub.startswith('C'): return None
        if sub.startswith('M'): return None
        if sub.startswith('S') and not sub[1:2].isalpha(): return None
        if sub.startswith('o'): return '1'
        if sub.startswith('s'): return '1'
        return self.nack()

    # =======================================================================
    # :$Q — smart drive / PEC
    # =======================================================================

    def _cmd_smart_drive(self, sub: str) -> str | None:
        if sub == '':
            self.smart_drive_enabled = not self.smart_drive_enabled; return None
        if sub == 'A+':  self.pec_dec_enabled = True;         return None
        if sub == 'A-':  self.pec_dec_enabled = False;        return None
        if sub == 'C':   return '00000#'
        if sub == 'S+':  self.smart_mount_enabled = True;     return None
        if sub == 'S-':  self.smart_mount_enabled = False;    return None
        if sub == 'W':   return '1'
        if sub == 'Z+':  self.pec_ra_enabled = True;          return None
        if sub == 'Z-':  self.pec_ra_enabled = False;         return None
        if sub.startswith('G'): return '0 0#'
        if sub.startswith('P'): return None
        return None

    # =======================================================================
    # Sync (:CM)
    # =======================================================================

    def _do_sync(self) -> str:
        self.ra  = self.target_ra
        self.dec = self.target_dec
        self.alignment_stars = min(3, self.alignment_stars + 1)
        self._set_display('Sync complete')
        plog(f'Sync  RA={self.ra:.4f}h  Dec={self.dec:.4f}°')
        return 'Coordinates     matched.       #'

    # =======================================================================
    # Auto-align (:Aa)
    # =======================================================================

    def _do_auto_align(self) -> str:
        self._set_display('Aligning...')
        self.alignment_stars = 3
        return '1'

    # =======================================================================
    # Handset / display (:E)
    # =======================================================================

    def _cmd_handset(self, sub: str) -> str | None:
        if sub == 'D':
            return self._basic_display()
        if sub.startswith('K'):
            return self._handset_keypress(sub[1:])
        return self._basic_display()

    def _handset_keypress(self, code: str) -> str | None:
        if code == '13':   # Enter
            self._navigate_menu('R'); return None
        if code == '9':    # Mode
            self._navigate_menu('L'); return None
        if code == '68':   # Down arrow
            self._navigate_menu('U'); return None
        if code == '85':   # Up arrow
            self._navigate_menu('D'); return None
        if code == '71':   # GoTo
            self._set_display('GoTo...'); return None
        return self._basic_display()

    # =======================================================================
    # Menu navigation
    # =======================================================================

    def _navigate_menu(self, direction: str):
        menu = self._level_menu(self.current_menu_keys)
        key  = self.current_menu_keys[-1]

        if direction == 'U':
            key = self._prev_entry(menu, key)
            self.current_menu_keys[-1] = key
        elif direction == 'D':
            key = self._next_entry(menu, key)
            self.current_menu_keys[-1] = key
        elif direction == 'L':
            if len(self.current_menu_keys) > 1:
                self.current_menu_keys.pop()
            key = self.current_menu_keys[-1]
        elif direction == 'R':
            sub = self._sub_menu(self.current_menu_keys)
            if sub:
                key = sub[0]
                self.current_menu_keys.append(key)

        self._set_display(key)

    def _level_menu(self, keys):
        local = self.menu_structure
        level = []
        for k in keys:
            if isinstance(local, dict):
                level = list(local.keys())
                local = local.get(k, {})
            else:
                level = list(local)
        return level

    def _sub_menu(self, keys):
        local = self.menu_structure
        for k in keys:
            if isinstance(local, dict):
                local = local.get(k, {})
            else:
                local = {}
        return list(local.keys()) if isinstance(local, dict) else list(local)

    def _prev_entry(self, menu, item):
        fields = list(menu.keys()) if isinstance(menu, dict) else list(menu)
        idx    = fields.index(item) if item in fields else 0
        return fields[idx - 1]

    def _next_entry(self, menu, item):
        fields = list(menu.keys()) if isinstance(menu, dict) else list(menu)
        idx    = fields.index(item) if item in fields else 0
        return fields[(idx + 1) % len(fields)]


# ===========================================================================
# Network server
# ===========================================================================

def listen_and_process(client_socket: socket.socket,
                        state_machine: TelescopeStateMachine):
    """Receive commands from one client, dispatch, send responses."""
    show_response = True
    recv_buf      = ''

    while True:
        try:
            data = client_socket.recv(1024)
        except (ConnectionResetError, OSError):
            plog('Connection closed by client')
            break

        if not data:
            plog('Client disconnected')
            break

        recv_buf += data.decode('latin-1', errors='replace')

        # Suppress noisy polling pair (:ED# + :G0#)
        if recv_buf.strip() == ':ED#:G0#':
            show_response = False
        else:
            if recv_buf.strip():
                show_response = True
                plog(f'RX [{recv_buf.strip()}]')

        # Process every '#'-terminated token in the buffer
        while '#' in recv_buf:
            token, recv_buf = recv_buf.split('#', 1)
            token = token.strip()
            if not token:
                continue

            # Handle ACK byte separately (no leading ':')
            if token == '\x06':
                state_machine.process_command('\x06')
                continue

            if token.startswith(':'):
                state_machine.process_command(token)

        # Flush send buffer
        while state_machine.has_data():
            chunk = state_machine.pop_from_send_buffer()
            if show_response:
                plog(f'TX [{chunk!r}]')
            try:
                client_socket.sendall(chunk.encode('latin-1'))
            except (BrokenPipeError, OSError):
                plog('Send failed — connection lost')
                return

        time.sleep(0.05)


def emulate_telescope(port: int, mode: str = 'lx200gps'):
    profile       = EMULATOR_PROFILES.get(mode, EMULATOR_PROFILES['lx200gps'])
    state_machine = TelescopeStateMachine(mode=mode)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', port))
    server.listen(1)
    plog(f"Emulating: {profile['description']}  "
         f"(product='{profile['product_name']}'  fw={profile['firmware']})")
    plog(f'Listening on 0.0.0.0:{port}')

    while True:
        client, addr = server.accept()
        plog(f'Client connected from {addr[0]}:{addr[1]}')
        try:
            listen_and_process(client, state_machine)
        finally:
            client.close()
            plog('Connection closed')


# ===========================================================================
# Self-tests
# ===========================================================================

def run_tests():
    print('=== Coordinate round-trip test ===')
    utc = datetime.datetime(2024, 3, 20, 12, 0, 0)
    lat, lon = 40.75, -74.0
    test_pairs = [
        (5.5139,  -5.3911),   # Orion Nebula
        (23.9999,  89.0),     # near north pole
        (0.0,       0.0),     # vernal equinox
        (18.6156,  38.7833),  # Vega
    ]
    for ra_in, dec_in in test_pairs:
        alt, az   = radec_to_altaz(ra_in, dec_in, lat, lon, utc)
        ra_r, dec_r = altaz_to_radec(alt, az, lat, lon, utc)
        # RA wraps at 24h — treat 0h and 24h as identical
        err_ra    = min(abs(ra_r - ra_in), abs(ra_r - ra_in - 24), abs(ra_r - ra_in + 24)) * 15.0
        err_dec   = abs(dec_r - dec_in)
        ok        = '✓' if err_ra < 0.01 and err_dec < 0.01 else '✗'
        print(f'  {ok}  RA={ra_in:.4f}h  Dec={dec_in:.4f}°'
              f'  → Alt={alt:.2f}°  Az={az:.2f}°'
              f'  → RA={ra_r:.4f}h  Dec={dec_r:.4f}°'
              f'  (err {err_ra:.4f}°, {err_dec:.4f}°)')

    print('\n=== Format test ===')
    sm = TelescopeStateMachine()
    sm.high_precision = True
    print(f'  RA  high: {sm._fmt_ra(5.5139)}')
    print(f'  Dec high: {sm._fmt_dec(-5.3911)}')
    print(f'  Az  high: {sm._fmt_az(270.5)}')
    sm.high_precision = False
    print(f'  RA  low : {sm._fmt_ra(5.5139)}')
    print(f'  Dec low : {sm._fmt_dec(-5.3911)}')
    print(f'  Az  low : {sm._fmt_az(270.5)}')

    print('\n=== Command dispatch test ===')
    sm = TelescopeStateMachine()
    tests = [
        (':GVP',  'LX200GPS#'),
        (':GVN',  '4.2g#'),
        (':GW',   None),          # just check no crash
        (':Sr05:30:30', '1'),
        (':Sd-05*23:00', '1'),
        (':GR',   None),
        (':GD',   None),
        (':GA',   None),
        (':GZ',   None),
        (':U',    None),          # toggle precision
        (':GR',   None),
    ]
    for cmd, expected in tests:
        sm.clear_send_buffer()
        sm.process_command(cmd)
        resp = sm.send_buffer or '(none)'
        ok   = '✓' if (expected is None or resp == expected) else '✗'
        print(f'  {ok}  {cmd:20s} → [{resp}]  (expected [{expected}])')

    print('\n=== Menu navigation test ===')
    sm = TelescopeStateMachine()
    for key in 'DDDRLLLUD':
        sm._navigate_menu(key)
    print(f'  Final menu position: {sm.current_menu_keys}')
    print(f'  Display: [{sm.display_line1}]')

    print('\nAll tests complete.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Meade LX200 telescope emulator over TCP',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Available emulation modes:\n' + '\n'.join(
            f'  {k:14s}  {v["description"]}' for k, v in EMULATOR_PROFILES.items()
        ),
    )
    parser.add_argument(
        'port',
        help='TCP port to listen on, or "test" to run self-tests',
    )
    parser.add_argument(
        '--emulate',
        metavar='MODE',
        default='lx200gps',
        choices=list(EMULATOR_PROFILES.keys()),
        help='telescope model to emulate (default: lx200gps)',
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
