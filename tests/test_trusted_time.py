from trusted_time import TrustedClock, sntp_time


class Fake:
    def __init__(self):
        self.tick_v = 1000.0
        self.system_v = 5_000_000.0
        self.ntp_v = None

    def tick(self):
        return self.tick_v

    def system(self):
        return self.system_v

    def ntp(self):
        return self.ntp_v


def make(f, last=None):
    return TrustedClock(last, ntp=f.ntp, tick=f.tick, system=f.system)


def test_clock_change_is_ignored():
    f = Fake()
    c = make(f)
    f.tick_v += 60
    f.system_v += 60 + 8 * 3600   # user moves the clock 8 hours forward
    assert c.now_ts() == 5_000_060.0
    assert round(c.offset()) == -8 * 3600


def test_ntp_sync_wins_over_system_clock():
    f = Fake()
    f.ntp_v = 6_000_000.0
    c = make(f)
    assert c.synced and c.now_ts() == 6_000_000.0
    f.tick_v += 10
    assert c.now_ts() == 6_000_010.0


def test_offline_rollback_uses_last_trusted():
    f = Fake()
    c = make(f, last=5_100_000.0)  # system clock was rolled back before this start
    assert c.now_ts() == 5_100_000.0


def test_retry_and_resync_timing():
    f = Fake()
    c = make(f)
    f.ntp_v = 7_000_000.0
    assert c.maybe_sync() is False       # retry not due yet
    f.tick_v += 121
    assert c.maybe_sync() is True
    assert c.now_ts() == 7_000_000.0


def test_real_ntp_or_offline():
    t = sntp_time(timeout=2)
    assert t is None or t > 1_700_000_000
