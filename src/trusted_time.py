"""Trusted time, so changing the Windows clock can't unlock anything.

The service keeps its own clock: a base time (from internet time servers when reachable, otherwise the
system clock) plus the Windows tick counter (which the user can't change and which keeps counting during
sleep). It publishes the difference to the system clock in the database; the GUI uses that offset.
Changing the time zone would shift local time (and so blocked hours): a new time zone only counts after 24 hours
(summer / winter time changes of the same zone count at once).
"""
import ctypes
import socket
import struct
import time
from datetime import datetime, timedelta

OFFSET_KEY = "clock_offset"          # trusted - system, seconds
LAST_TRUSTED_KEY = "clock_last_trusted"
NTP_SERVERS = ["time.windows.com", "pool.ntp.org", "time.google.com"]
NTP_EPOCH_DELTA = 2208988800         # 1900-01-01 -> 1970-01-01
RESYNC_SEC = 30 * 60
RETRY_SEC = 2 * 60
ZONE_KEY = "clock_zone"              # JSON {"name", "offset" (accepted zone, UTC offset s), "pending", "since"}
ZONE_DELAY_SEC = 24 * 3600
ZONE_REG = r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation"


def sntp_time(servers=NTP_SERVERS, timeout: float = 3) -> float | None:
    """Current time (unix seconds) from the first responding NTP server, or None."""
    for server in servers:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(timeout)
                s.sendto(b"\x1b" + 47 * b"\0", (server, 123))
                data, _ = s.recvfrom(48)
            secs, frac = struct.unpack("!II", data[40:48])
            if secs:
                return secs - NTP_EPOCH_DELTA + frac / 2**32
        except OSError:
            continue
    return None


def _tick() -> float:
    ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    return ctypes.windll.kernel32.GetTickCount64() / 1000


class TrustedClock:
    def __init__(self, last_trusted: float | None = None, ntp=sntp_time, tick=_tick, system=time.time):
        self._ntp, self._tick, self._system = ntp, tick, system
        # Offline start: never go back before the last trusted time we saw (clock rolled back).
        self.base_wall = max(system(), last_trusted or 0)
        self.base_tick = tick()
        self.synced = False
        self.next_sync = 0.0
        self.zone_shift = 0.0   # seconds from Windows' current time zone to the accepted one (see ZoneGuard)
        self.maybe_sync()

    def maybe_sync(self) -> bool:
        """Re-sync with internet time when due. Returns True if a sync happened."""
        if self._tick() < self.next_sync:
            return False
        t = self._ntp()
        if t is None:
            self.next_sync = self._tick() + RETRY_SEC
            return False
        self.base_wall, self.base_tick = t, self._tick()
        self.synced = True
        self.next_sync = self.base_tick + RESYNC_SEC
        return True

    def now_ts(self) -> float:
        return self.base_wall + (self._tick() - self.base_tick)

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.now_ts() + self.zone_shift)

    def offset(self) -> float:
        return self.now_ts() + self.zone_shift - self._system()


def zone_name() -> str:
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ZONE_REG) as key:
        return str(winreg.QueryValueEx(key, "TimeZoneKeyName")[0])


def utc_offset(ts: float) -> float:
    """Windows' current time zone offset at `ts` (seconds east of UTC)."""
    return datetime.fromtimestamp(ts).astimezone().utcoffset().total_seconds()


def zone_step(state: dict | None, name: str, offset: float, now_ts: float) -> tuple[dict, float, str | None]:
    """Keep using the accepted time zone for 24 h after a change. Returns (new state, shift in seconds to add to
    Windows' local time, message to log or None)."""
    if not state:
        return {"name": name, "offset": offset}, 0.0, None
    if name == state["name"]:   # same zone (summer / winter time follows Windows)
        return {"name": name, "offset": offset}, 0.0, None
    state = dict(state)
    message = None
    if state.get("pending") != name:
        state.update(pending=name, since=now_ts)
        message = f"Time zone changed to {name} - Lockdown keeps {state['name']} for 24 hours"
    elif now_ts - state["since"] >= ZONE_DELAY_SEC:
        return {"name": name, "offset": offset}, 0.0, f"Time zone {name} accepted after 24 hours"
    return state, state["offset"] - offset, message


def now_from_db(db) -> datetime:
    """Trusted local time for the GUI/tray (uses the offset the service publishes)."""
    return datetime.now() + timedelta(seconds=float(db.get_setting(OFFSET_KEY, "0")))
