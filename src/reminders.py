"""Reminders (run by the tray agent every few seconds): sleep, breaks, 20-20-20 and your own reminders.

The engine only decides; a `ui` object shows things: popup(key, title, text, buttons), close(key),
overlay(key, title, text, until, buttons), toast(text), start_mode(mode_id, until). Buttons are (label, action)
and the UI calls answer(key, action) back. While a full-screen app (a game) is in front, popups wait and a
Windows notification is shown instead; sleep and forced-break overlays still appear - that's their point.
With Do not disturb on, everything waits, the bedtime screen included: you asked for quiet, so it holds until
that is over rather than being skipped.
"""
import json
import random
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import emergency
import pause
from rules import TIME_FMT, days_text, parse_hhmm

TICK_SEC = 5
FAST_TICK_SEC = 0.5            # the soonest a tick comes when something timed is due before the next one (next_tick)
MIN_INTERVAL_SEC = 5           # the shortest "every" / "comes back" / snooze an alert can have (one tick)
UNPACED_UNDER_SEC = 60         # an interval under a minute (seconds: a nag you asked for) isn't held by the pace
USING_IDLE_SEC = 60            # input within the last minute = using the PC
BREAK_RESET_SEC = 5 * 60       # away this long = you had a break
TWENTY_SEC = 20 * 60
LATE_FIRE_MIN = 30             # a set-time reminder still fires up to 30 min late (PC was asleep etc.)

SLEEP_KEY, BREAK_KEY, CUSTOM_KEY = "reminders.sleep", "reminders.break", "reminders.custom"
# What each reminder says when you haven't written your own. {placeholders} are filled in; anything else you
# type is left as it is.
SLEEP_WARN_TEXT = "Bedtime is at {bedtime} - time to wrap up."
SLEEP_TEXT = "It's {time}. Sleep well - the screen can wait until tomorrow."
BREAK_TEXT = "You've been at the PC for {every} min. Take {length} min away from the screen."
TWENTY_TEXT = "20-20-20: look at something 20 feet (6 m) away for 20 seconds."
# Alert intervals are in seconds ("every_sec", "repeat_sec", "snooze_sec"); settings saved before that had them in
# minutes ("every", "repeat", "snooze") and are read through in_seconds.
DEFAULT_SLEEP = {"on": False, "bedtime": "23:00", "wake": "07:00", "before": 30, "repeat_sec": 5 * 60, "mode": "",
                 "warn_text": "", "text": "",
                 # The later it gets, the more often the bedtime screen comes back after you dismiss it - so that
                 # going to bed is easier than fending it off. Each tier is {"from": "HH:MM", "every_sec": seconds},
                 # read within the night; the latest tier whose time has passed wins. Fully editable.
                 "tiers": [{"from": "21:00", "every_sec": 15 * 60}, {"from": "00:00", "every_sec": 5 * 60},
                           {"from": "03:00", "every_sec": 60}],
                 "guarded": False,   # dismissing the bedtime screen needs the Anti-Bypass challenge
                 "dismiss_wait": 0}  # seconds before the bedtime screen's Dismiss works (0 = at once)
# Apps that stay usable during a strict break ("Allowed during breaks"): the break settings' "allow" list, program
# (.exe) names. A list saved before it existed has none, so it reads as this default; the user can take either out.
BREAK_ALLOW_DEFAULT = ("lockdown.exe", "systemsettings.exe")
BREAK_ALLOW_MAX = 40
BREAK_ALLOW_NAMES = {"lockdown.exe": "Lockdown", "systemsettings.exe": "Windows Settings"}
# "Lockdown" stands for every program Lockdown runs as: the window and agent, the challenge the uninstaller asks for, the
# uninstaller and the installer / updater. (A Store app such as Settings is found by its own process, not by the
# ApplicationFrameHost.exe frame around it: monitor.win.content_pid.)
LOCKDOWN_EXES = ("lockdown.exe", "lockdownservice.exe", "lockdownsetup.exe", "uninstall lockdown.exe")
DEFAULT_BREAK = {"on": True, "every_sec": 45 * 60, "length": 5, "strict": False, "snooze_sec": 5 * 60,
                 "max_snooze": 2, "twenty": False, "text": "", "twenty_text": "", "guarded": False,
                 "allow": list(BREAK_ALLOW_DEFAULT)}
DEFAULT_CUSTOM = {"on": True, "text": "", "kind": "interval", "every_sec": 60 * 60, "times": ["12:00"],
                  "days": [0, 1, 2, 3, 4, 5, 6], "window": ["10:00", "18:00"], "snooze": 5, "max_snooze": 3,
                  "check": 0, "packs": [], "quotes": "",
                  "hours": False,      # only between window[0] and window[1] (every kind, not just random)
                  "per_day": 0,        # stop for the day after this many Done (0 = no limit)
                  "guarded": False}    # important: dismissing needs the Anti-Bypass challenge
GROUP_MIN = 5          # a reminder due within this many minutes of one already on screen joins it
DISMISS_MARK = "✕"    # the X button: closes it without claiming you did it
PACE_MIN = 20          # at most one interruption per this many minutes - what comes due in between waits for it
BACKOFF_AFTER = 3      # waved away this many times in a row: it asks half as often for the rest of the day

# Short public-domain quotes
PACKS = {
    "Stoic": ["You have power over your mind - not outside events. - Marcus Aurelius",
              "Waste no more time arguing what a good man should be. Be one. - Marcus Aurelius",
              "We suffer more often in imagination than in reality. - Seneca",
              "It is not that we have a short time to live, but that we waste a lot of it. - Seneca",
              "First say to yourself what you would be; then do what you have to do. - Epictetus"],
    "Motivation": ["Well begun is half done. - Aristotle",
                   "The secret of getting ahead is getting started. - attributed to Mark Twain",
                   "It does not matter how slowly you go as long as you do not stop. - attributed to Confucius",
                   "Small steps every day.", "Future you will thank you."],
    "Health": ["Drink a glass of water.", "Roll your shoulders and stretch your neck.",
               "Look out of the window for a moment.", "Stand up and walk around for a minute."],
}


def parse_minutes(text: str, allow_off: bool = True, least: float = 5 / 60, most: float = 24 * 60) -> float:
    """What you typed into a "how often" box, as minutes: "30" and "30 min" -> 30, "45s" -> 0.75,
    "1h" -> 60, "1h30" -> 90, "off" / "0" -> 0. Seconds are kept as a fraction, so "every 30s" works."""
    t = text.strip().lower().replace(",", ".")
    if t in ("", "off", "0", "no", "never"):
        if not allow_off:
            raise ValueError("Write a time like 30, 45s or 1h30.")
        return 0
    if m := re.fullmatch(r"(\d+)\s*h(?:\s*(\d+)\s*(?:m|min|mins|minutes?)?)?", t):
        value = int(m[1]) * 60 + int(m[2] or 0)
    elif m := re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds?)", t):
        value = float(m[1]) / 60
    elif m := re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:m|min|mins|minutes?)?", t):
        value = float(m[1])
    else:
        raise ValueError("Write a time like 30, 45s or 1h30.")
    if not least <= value <= most:
        raise ValueError(f"Between {minutes_text(least)} and {minutes_text(most)}, please.")
    return value


def minutes_text(minutes: float) -> str:
    """The other way round, for the box: 0 -> "Off", 0.5 -> "30s", 5 -> "5 min", 90 -> "1h30"."""
    if not minutes:
        return "Off"
    seconds = round(minutes * 60)
    if seconds % 60 and seconds < 5 * 60:      # 45s, 90s - rather than "1.5 min"
        return f"{seconds}s"
    whole = round(seconds / 60)
    if whole >= 60:
        h, m = divmod(whole, 60)
        return f"{h}h{m:02d}" if m else f"{h}h"
    return f"{whole} min"


def parse_interval(text: str, allow_off: bool = False) -> int:
    """How often an alert comes, as typed ("30s", "5 min", a bare 5 = minutes, "1h30"), in whole seconds: from
    MIN_INTERVAL_SEC to 24 h. allow_off: "Off" / "0" -> 0."""
    return round(parse_minutes(text, allow_off=allow_off, least=MIN_INTERVAL_SEC / 60) * 60)


def seconds_text(seconds: int) -> str:
    """An interval in seconds for its box and its buttons: 30 -> "30s", 90 -> "90s", 300 -> "5 min", 5400 -> "1h30",
    0 -> "Off"."""
    seconds = round(seconds)
    return f"{seconds}s" if seconds % 60 else minutes_text(seconds // 60)


DISMISS_WAITS = {"Off": 0, "10 s": 10, "30 s": 30, "60 s": 60}   # quick picks; any number of seconds in between
MAX_DISMISS_WAIT = 600


def parse_seconds(text: str) -> int:
    """The bedtime screen's wait before Dismiss, as typed: "30" and "30s" -> 30, "2 min" -> 120, "off" -> 0."""
    t = str(text).strip().lower()
    if t in ("", "off", "0", "no", "never"):
        return 0
    if m := re.fullmatch(r"(\d+)\s*(?:s|sec|secs|seconds?)?", t):
        value = int(m[1])
    elif m := re.fullmatch(r"(\d+)\s*(?:m|min|mins|minutes?)", t):
        value = int(m[1]) * 60
    else:
        raise ValueError("Write the wait in seconds, like 30, or Off.")
    if value > MAX_DISMISS_WAIT:
        raise ValueError(f"A wait of at most {MAX_DISMISS_WAIT} seconds, please.")
    return value


def dismiss_wait(s: dict) -> int:
    """Seconds the bedtime screen's Dismiss stays off each time it comes up (0 = none)."""
    try:
        return max(0, min(MAX_DISMISS_WAIT, int(s.get("dismiss_wait") or 0)))
    except (TypeError, ValueError):
        return 0


def message(custom: str, default: str, **values) -> str:
    """What a reminder says: your own wording if you wrote any, otherwise the standard one. A {placeholder}
    that doesn't exist leaves the text as you typed it rather than breaking the reminder."""
    text = (custom or "").strip() or default
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):
        return text


def _minutes_to_seconds(d, keys: tuple) -> dict:
    if not isinstance(d, dict):
        return d
    d = dict(d)
    for k in keys:
        if k in d:
            minutes = d.pop(k)
            try:
                d[f"{k}_sec"] = round(float(minutes) * 60)
            except (TypeError, ValueError):
                pass   # (not a number: the default applies)
    return d


def in_seconds(key: str, saved):
    """Reminder settings saved before alert intervals were in seconds had them in minutes: the bedtime steps'
    "every" and the flat "repeat", the break's "every" and "snooze", your reminders' "every" (their snooze stays
    minutes). Read as "..._sec". A minutes key wins over a seconds one in the same dict: only an older copy, an old
    backup or a test writes it - Lockdown now writes only the seconds."""
    if key == SLEEP_KEY and isinstance(saved, dict):
        saved = _minutes_to_seconds(saved, ("repeat",))
        if isinstance(saved.get("tiers"), list):
            saved["tiers"] = [_minutes_to_seconds(t, ("every",)) for t in saved["tiers"]]
    elif key == BREAK_KEY:
        saved = _minutes_to_seconds(saved, ("every", "snooze"))
    elif key == CUSTOM_KEY and isinstance(saved, list):
        saved = [_minutes_to_seconds(r, ("every",)) for r in saved]
    return saved


def break_allow_exe(text) -> str:
    """What was picked or typed for "Allowed during breaks" as a lower-case program name ("C:\\x\\Discord.exe" ->
    "discord.exe"). A ValueError says why not: only programs can be told apart in front (a Java game or a script runs
    inside java.exe / python.exe, which would let every other one through)."""
    name = re.split(r"[\\/]", str(text or "").strip().strip('"').strip())[-1].strip().lower()
    if not name:
        raise ValueError("Pick an app, or write its file name like discord.exe.")
    if not name.endswith(".exe"):
        raise ValueError("Only programs (.exe) can stay open during a break - a Java game or a script runs inside "
                         "another program and can't be told apart. Pick that program itself, or its own .exe.")
    if len(name) > 100 or not re.fullmatch(r"[a-z0-9 ._()+&@~-]+", name):
        raise ValueError("That isn't a program file name.")
    return name


def allowed_apps(b) -> list[str]:
    """The apps allowed during a strict break, from the break settings: the program names, cleaned (a damaged entry is
    left out, the same one only once, at most BREAK_ALLOW_MAX). No list at all (settings from before it existed, a
    backup of those) is the default; an empty list is a choice and stays empty."""
    raw = b.get("allow") if isinstance(b, dict) else None
    if not isinstance(raw, (list, tuple)):
        return list(BREAK_ALLOW_DEFAULT)
    out = []
    for entry in raw:
        try:
            exe = break_allow_exe(entry)
        except ValueError:
            continue
        if exe not in out and len(out) < BREAK_ALLOW_MAX:
            out.append(exe)
    return out


def allowed_exes(apps) -> frozenset:
    """The process names that stay in front during a strict break for these apps: "lockdown.exe" stands for all of
    Lockdown's programs (and, run from source, for the interpreter that runs it)."""
    out = set(apps)
    if "lockdown.exe" in out:
        out.update(LOCKDOWN_EXES)
        if not getattr(sys, "frozen", False):
            out.add(Path(sys.executable).name.lower())
    return frozenset(out)


def break_allow_name(exe: str) -> str:
    return BREAK_ALLOW_NAMES.get(exe, exe)


def load(db, key: str, default):
    try:
        saved = json.loads(db.get_setting(key, "") or "null")
    except ValueError:
        saved = None
    saved = in_seconds(key, saved)
    if isinstance(default, dict):
        return {**default, **(saved or {})}
    return saved if saved is not None else default


def save(db, key: str, value):
    db.set_setting(key, json.dumps(value))


def custom_list(db) -> list[dict]:
    return [{**DEFAULT_CUSTOM, **r} for r in load(db, CUSTOM_KEY, [])]


def log(db, what: str, result: str, now: datetime):
    db.write("INSERT INTO reminder_log VALUES (?, ?, ?)", (now.strftime(TIME_FMT), what, result))


def counts(db, what: str, since: datetime) -> dict[str, int]:
    return counts_many(db, [what], since).get(what, {})


def counts_many(db, whats, since: datetime) -> dict[str, dict[str, int]]:
    """{what: {result: n}} since `since` for several reminders in one grouped query (the Reminders page used to
    run one per reminder). Days retention has rolled up count from `daily_reminders`, whole days only: when `since`
    is not midnight and its own day is already rolled up, that day is left out (the time of day isn't kept there).
    Every caller asks from a midnight, at most a week back - always inside the detail."""
    whats = list(dict.fromkeys(whats))
    if not whats:
        return {}
    marks = ", ".join("?" * len(whats))
    first_day = since.date() if since.time() == time(0) else since.date() + timedelta(days=1)
    rows = db.conn.execute(
        f"SELECT what, result, SUM(n) FROM ("
        f" SELECT what, result, COUNT(*) AS n FROM reminder_log WHERE what IN ({marks}) AND timestamp >= ?"
        f"  GROUP BY what, result"
        f" UNION ALL"
        f" SELECT what, result, SUM(count) AS n FROM daily_reminders WHERE what IN ({marks}) AND day >= ?"
        f"  GROUP BY what, result"
        f") GROUP BY what, result",
        (*whats, since.strftime(TIME_FMT), *whats, first_day.isoformat()))
    out: dict[str, dict[str, int]] = {}
    for what, result, n in rows:
        out.setdefault(what, {})[result] = n
    return out


def loosens_reminder(old: dict, new: dict) -> bool:
    """A change that weakens an IMPORTANT alert - turning it off, or removing its "important" flag.
    Those need the Anti-Bypass challenge; ordinary edits (times, message, frequency) do not. A shorter wait
    before the bedtime screen's Dismiss (or none) needs it too, important or not; a longer one is free."""
    if dismiss_wait(new) < dismiss_wait(old):
        return True
    if not old.get("guarded"):
        return False
    return bool((old.get("on") and not new.get("on")) or not new.get("guarded"))


def tiers_of(s: dict) -> list[dict]:
    """The sleep escalation tiers, earliest first. Empty means "use the flat `repeat` instead"."""
    return sorted(s.get("tiers") or [], key=lambda t: parse_hhmm(t["from"]))


def schedule_text(r: dict) -> str:
    if r["kind"] == "interval":
        text = f"every {seconds_text(r['every_sec'])} of use"
        if r.get("hours"):
            text += f", {r['window'][0]}-{r['window'][1]}"
    elif r["kind"] == "times":
        text = f"{days_text(r['days'])} at {', '.join(r['times'])}"
    else:
        text = f"once a day, at a random time {r['window'][0]}-{r['window'][1]}"
    if r["kind"] != "times" and r["days"] != list(range(7)):
        text += f" · {days_text(r['days'])}"
    if r.get("per_day"):
        text += f" · stops after {r['per_day']}x done"
    return text


class Engine:
    def __init__(self, db, ui, rng: random.Random | None = None):
        self.db, self.ui, self.rng = db, ui, rng or random.Random()
        self.now = datetime.now()
        self.fullscreen = False
        self.quiet = False          # Windows' Do not disturb (or a mode that mutes): everything waits
        self.open: set[str] = set()          # popups / overlays on screen
        self.waiting: dict[str, tuple] = {}  # popups held back while a full-screen app is in front (a notice meanwhile)
        self.continuous = 0.0                # seconds of use since the last break
        self.twenty = 0.0
        self.break_until: datetime | None = None
        self.break_snoozes = 0               # snoozes used for the current break prompt (strict mode limits these)
        self.snoozed: dict[str, datetime] = {}               # key -> fire again at
        self.snoozes_used: dict[str, int] = {}               # key -> snoozes since it was last done
        self.counters: dict[str, float] = {}                 # interval reminders: seconds of use so far
        self.use_left: dict[str, float] = {}                 # this tick: seconds of use until the break / one is due
        self.fired: set[tuple] = set()                       # (id, date, "HH:MM") already shown
        self.random_at: dict[tuple, time] = {}               # (id, date) -> today's random time
        self.checks: dict[str, datetime] = {}                # reminder id -> ask "did you do it?" at
        self.sleep_next: dict[str, datetime] = {}            # night -> next bedtime overlay
        self.done_today: dict[tuple, int] = {}               # (reminder, date) -> times done (the daily limit)
        self.grouped: dict[str, list[str]] = {}              # popup key -> the reminders it is showing
        self.last_interruption: datetime | None = None       # when a reminder or break prompt last came up
        self.pending: list[str] = []                         # reminders due while the pace said "not yet"
        self.folded: list[str] = []                          # reminders riding along in the break prompt
        self.streak: dict[str, int] = {}                     # reminder id / "break" -> dismissed in a row
        self.backed_off: dict[str, date] = {}                # reminder id / "break" -> the day it asks less
        self.break_due: dict | None = None                   # the break settings, when it comes up this tick
        self.paused: datetime | None = None                  # an emergency unlock paused bedtime/break alerts until
        self.silent: datetime | None = None                  # "Pause my blocks" silenced every notification until

    # ---------- showing ----------

    def _popup(self, key: str, title: str, text: str, buttons: list[tuple[str, str]]):
        if key in self.open:
            return
        if self.fullscreen:
            # Over a game no window of ours (it made the game stutter): a Windows notification with the same
            # buttons instead (ui.held), once. Answered there, it is done (held_answer); if not, the popup comes
            # up when the game is no longer in front.
            if key not in self.waiting:
                self.ui.held(key, title, text, buttons)
                self.waiting[key] = (title, text, buttons)
            return
        self.waiting.pop(key, None)
        self.open.add(key)
        self.ui.popup(key, title, text, buttons)
        if key == "break" or key.startswith("custom:"):
            self.last_interruption = self.now

    def _paced(self, now: datetime) -> bool:
        """Too soon for another interruption? Break, hydrate and pull-ups used to arrive one after another, some
        forty times a day. Now at most one comes up per PACE_MIN minutes, and whatever falls due in between waits
        and arrives with the next one. Something already on screen does not count against it: what comes due
        then simply joins it."""
        if any(k == "break" or k.startswith("custom:") for k in self.open):
            return False
        return self.last_interruption is not None and now - self.last_interruption < timedelta(minutes=PACE_MIN)

    def _every(self, key: str, seconds: float, now: datetime) -> float:
        """An interval in seconds - doubled for the rest of the day once you have waved it away BACKOFF_AFTER times
        running."""
        return seconds * (2 if self.backed_off.get(key) == now.date() else 1)

    def _dismissed(self, key: str, name: str, now: datetime):
        """A reminder you keep waving away is noise. After BACKOFF_AFTER in a row it asks half as often for the
        rest of the day, and says so once. Doing it (or taking the break) starts the count again."""
        self.streak[key] = self.streak.get(key, 0) + 1
        if self.streak[key] >= BACKOFF_AFTER and self.backed_off.get(key) != now.date():
            self.backed_off[key] = now.date()
            self.streak[key] = 0
            short = name if len(name) <= 40 else name[:39].rstrip() + "…"
            self.ui.toast(f'Skipped "{short}" {BACKOFF_AFTER} times in a row - it will ask half as often for the '
                          f"rest of today.")

    def _overlay(self, key: str, title: str, text: str, until: datetime | None, buttons: list[tuple[str, str]]):
        if self.quiet:
            return   # Do not disturb: it comes up on the tick after that ends (while it is still due)
        if key not in self.open:
            self.open.add(key)
            self.ui.overlay(key, title, text, until, buttons)

    def _close(self, key: str):
        if self.waiting.pop(key, None) is not None and key not in self.open:
            self.ui.close(key)   # (its notice goes too)
        if key in self.open:
            self.open.discard(key)
            self.ui.close(key)

    # ---------- tick ----------

    def tick(self, now: datetime, idle_sec: float, fullscreen: bool, dt: float = TICK_SEC,
             quiet: bool = False):
        """quiet: you told Windows (or a mode) not to disturb you - popups queue as they do over a game, and
        the bedtime screen holds too, which it doesn't do for a game."""
        self.now, self.quiet = now, quiet
        self.fullscreen = fullscreen = fullscreen or quiet
        # an emergency that paused the alerts, or "Pause my blocks" with "Silence all notifications" (pause.py)
        self.silent = pause.silent_until(self.db, now)
        self.paused = max(filter(None, (emergency.alerts_paused_until(self.db, now), self.silent)), default=None)
        using = idle_sec < USING_IDLE_SEC
        self.use_left = {}
        self._breaks(now, idle_sec, using, dt)
        self._sleep(now)
        if self.silent:
            # silenced: your reminders don't come up either (one on screen goes), until the pause ends
            for key in [k for k in (*self.open, *self.waiting) if k.startswith(("custom:", "check:"))]:
                self.grouped.pop(key, None)
                self._close(key)
            self.pending, self.folded = [], []
            return
        self._custom(now, using, dt)
        if self.break_due:
            # Shown last, so that a reminder falling due in the same tick is already folded in: drawn once,
            # rather than drawn and then torn down and drawn again with one more line.
            self._absorb()
            self._show_break(self.break_due)
            self.break_due = None
        if not fullscreen:
            for key, (title, text, buttons) in list(self.waiting.items()):
                self._popup(key, title, text, buttons)

    def next_tick(self, now: datetime) -> float:
        """Seconds until the next tick: TICK_SEC, or sooner when something timed falls due before then (the bedtime
        screen back after a dismiss, a snooze, "did you do it?", a strict break's end, and while you use the PC a break
        or reminder after so much use) - so an alert set to 5 or 30 seconds comes on time, not up to a tick late.
        Faster only right before that, at most every FAST_TICK_SEC."""
        due = [*self.sleep_next.values(), *self.snoozed.values(), *self.checks.values(), self.break_until]
        soon = [(t - now).total_seconds() for t in due if t and t > now]
        soon += [s for s in self.use_left.values() if s > 0]   # a break / reminder after so much use (while using)
        return max(FAST_TICK_SEC, min([TICK_SEC, *soon]))

    # ---------- breaks ----------

    def _breaks(self, now, idle_sec, using, dt):
        b = load(self.db, BREAK_KEY, DEFAULT_BREAK)
        if self.paused:
            # Emergency: the screen is yours until it ends - no break prompt, and a strict break that is
            # minimising your windows stops. Use keeps counting, so the next prompt comes when it's due.
            self._close("break")
            if self.break_until:
                self.break_until = None
                self.break_snoozes = 0
                self.ui.break_end()
                log(self.db, "break", "emergency", now)
            if idle_sec >= BREAK_RESET_SEC:
                self.continuous = 0
            elif using and b["on"]:
                self.continuous += dt
            return
        if self.break_until:
            if now >= self.break_until:
                self.ui.break_end()
                self.break_until = None
                self.continuous = 0
                self.break_snoozes = 0
                log(self.db, "break", "taken", now)
            return
        if not b["on"]:
            self.continuous = 0
            return
        if idle_sec >= BREAK_RESET_SEC:
            if self.continuous >= 10 * 60:   # a real stretch of use, then away = a break
                log(self.db, "break", "away", now)
            self.continuous = 0
            self._close("break")
        elif using:
            self.continuous += dt
            if b["twenty"]:
                self.twenty += dt
                if self.twenty >= TWENTY_SEC:
                    self.twenty = 0
                    self.ui.toast(message(b.get("twenty_text"), TWENTY_TEXT))
        if now < self.snoozed.get("break", now):
            return
        if using and "break" not in self.open:
            self.use_left["break"] = self._every("break", b["every_sec"], now) - self.continuous
        if self.continuous >= self._every("break", b["every_sec"], now) and "break" not in self.open:
            if b.get("strict") and self.break_snoozes >= b.get("max_snooze", 2):
                self._start_break(now, b)   # strict, out of snoozes -> starts by itself, and never waits for the pace
                return
            if "break" not in self.snoozed and b["every_sec"] >= UNPACED_UNDER_SEC and self._paced(now):
                return                      # too soon after the last one: it comes when the pace allows
            self.snoozed.pop("break", None)  # (back from a snooze: you asked for it now, so it does not wait)
            self.break_due = b              # (shown at the end of the tick - see tick)

    def _absorb(self):
        """One popup, not two: reminders waiting for the pace, or on screen now, ride along in the break."""
        for key in [k for k in self.open if k.startswith("custom:")]:
            self.folded += [rid for rid in self.grouped.pop(key, [key.split(":", 1)[1]]) if rid not in self.folded]
            self._close(key)
        self.folded += [rid for rid in self.pending if rid not in self.folded]
        self.pending = []

    def _outstanding(self, rid: str) -> bool:
        """Is this reminder still waiting for you - on screen (on its own, in a group, in the break prompt) or
        queued for the pace? Then it can't come due again: its next interval starts once this one is dealt with.
        Otherwise a reminder queued for a while was due again the moment it was shown, and opened a second
        popup next to the first."""
        if rid in self.pending or rid in self.folded:
            return True
        return any(rid in self.grouped.get(k, [k.split(":", 1)[1]]) for k in self.open if k.startswith("custom:"))

    def _show_break(self, b: dict):
        """The break prompt, with whatever reminders are riding along in it ("While you're up: ...")."""
        strict = b.get("strict")
        snoozes_left = self.break_snoozes < b.get("max_snooze", 2)
        buttons = [("Start break", "start")]
        if not strict or snoozes_left:
            buttons.append((f"Snooze {seconds_text(b.get('snooze_sec', 300))}", "snooze"))
        if not strict:          # a strict break must not gain a one-click way out
            buttons.append((DISMISS_MARK, "dismiss"))
        # {every} says "45 min" or "30s"; a "{every} min" (from when it was a number of minutes) says it the same
        text = (message((b.get("text") or "").replace("{every} min", "{every}"),
                        BREAK_TEXT.replace("{every} min", "{every}"), every=seconds_text(b["every_sec"]),
                        length=b["length"])
                + (f"\n\nStrict break: {b.get('max_snooze', 2) - self.break_snoozes} snooze"
                   f"{'s' * (b.get('max_snooze', 2) - self.break_snoozes != 1)} left, then it starts on its "
                   "own." if strict else ""))
        by_id = {x["id"]: x for x in custom_list(self.db)}
        lines = [by_id[rid]["text"] for rid in self.folded if rid in by_id]
        if lines:
            text += "\n\nWhile you're up:\n" + "\n".join(f"• {line}" for line in lines)
        if "break" in self.open:    # re-shown whole when another reminder joins it - but only if it is on
            self._close("break")    # screen: closing also drops the full-screen hold, which re-toasts it
        self._popup("break", "Time for a break", text, buttons)

    def _start_break(self, now, b):
        """A strict break: your windows are minimised until it's over (the UI enforces it)."""
        self.break_until = now + timedelta(minutes=b["length"])
        self.break_snoozes = 0
        self._close("break")
        self.ui.break_start(self.break_until)
        log(self.db, "break", "started", now)

    # ---------- sleep ----------

    def _night(self, s: dict, now: datetime) -> tuple[datetime, datetime] | None:
        """(bedtime, wake time) of the night we're in or about to start, if within the warning window."""
        bed_t, wake_t = parse_hhmm(s["bedtime"]), parse_hhmm(s["wake"])
        for day in (now.date() - timedelta(days=1), now.date()):
            bed = datetime.combine(day, bed_t)
            wake = datetime.combine(day, wake_t)
            if wake <= bed:
                wake += timedelta(days=1)
            if bed - timedelta(minutes=s["before"]) <= now < wake:
                return bed, wake
        return None

    def _sleep_interval(self, s: dict, bed: datetime, now: datetime) -> int:
        """Seconds until the bedtime screen returns after a dismiss - from the active escalation step (at least
        MIN_INTERVAL_SEC). A step's time is placed in the night counted from bedtime; one in the daytime gap before
        bedtime (at or after wake) counts from bedtime - also with a bedtime after midnight: a 23:00 or 00:30 step
        with bedtime 01:00 used to be dated the evening after (or past wake) and never applied (the phone's rule
        since 0.5.14). Two steps at the same time: the more frequent one. With no steps, or before the first, the
        flat `repeat_sec`."""
        def since_bed(t) -> int:            # minutes from bedtime to t, going forward through the night
            return (t.hour * 60 + t.minute - bed.hour * 60 - bed.minute) % 1440
        wake_off = since_bed(parse_hhmm(s["wake"])) or 1440
        best = None
        for t in tiers_of(s):
            off = since_bed(parse_hhmm(t["from"]))
            when = bed if off >= wake_off else bed + timedelta(minutes=off)
            every = int(t["every_sec"])
            if when <= now and (best is None or when > best[0] or (when == best[0] and every < best[1])):
                best = (when, every)
        return max(MIN_INTERVAL_SEC, best[1] if best else int(s.get("repeat_sec", 300)))

    def _sleep(self, now):
        s = load(self.db, SLEEP_KEY, DEFAULT_SLEEP)
        night = self._night(s, now) if s["on"] else None
        if not night:
            self._close("sleep")
            return
        bed, wake = night
        key = bed.strftime(TIME_FMT)
        if self.paused:   # an emergency paused the alerts: nothing on screen until it ends, then as before
            self._close("sleep")
            self._close("sleep-warn")
        if now < bed:
            if ("warn", key) not in self.fired and not self.paused:
                self.fired.add(("warn", key))
                self._popup("sleep-warn", "Bedtime soon",
                            message(s.get("warn_text"), SLEEP_WARN_TEXT, bedtime=f"{bed:%H:%M}",
                                    wake=f"{wake:%H:%M}", time=f"{now:%H:%M}"),
                            [("OK", "ok")])
            return
        if ("bed", key) not in self.fired:
            self.fired.add(("bed", key))
            self.sleep_next[key] = now
            if s["mode"]:
                self.ui.start_mode(s["mode"], wake)
        if now >= self.sleep_next.get(key, now) and "sleep" not in self.open and not self.paused:
            buttons = [("Dismiss", "dismiss"), ("Disable alerts", "disable")]
            if left := emergency.available(self.db, now):   # the sanctioned way out, no challenge: it costs a use
                buttons.append((f"Emergency ({left} left)", "emergency"))
            self._overlay("sleep", "Time for bed",
                          message(s.get("text"), SLEEP_TEXT, time=f"{now:%H:%M}", bedtime=f"{bed:%H:%M}",
                                  wake=f"{wake:%H:%M}"),
                          None, buttons)

    # ---------- your reminders ----------

    def _allowed_now(self, r: dict, now: datetime) -> bool:
        """The days you picked, the hours you picked, and whether you have already done it enough times today."""
        if now.weekday() not in r["days"]:
            return False
        if r.get("hours") and r["kind"] != "times":   # "times" already says when: its own times are the hours
            start, end = (parse_hhmm(x) for x in r["window"])
            t = now.time()
            inside = start <= t < end if start < end else (t >= start or t < end)   # overnight windows
            if not inside:
                return False
        return not self._done_for_today(r, now)

    def _done_for_today(self, r: dict, now: datetime) -> bool:
        """The limit counts what you have DONE, not what you were shown: five glasses of water is five Done."""
        if not r.get("per_day"):
            return False
        key = (r["id"], now.date())
        if key not in self.done_today:
            midnight = datetime.combine(now.date(), time(0))
            self.done_today[key] = counts(self.db, r["id"], midnight).get("done", 0)
        return self.done_today[key] >= r["per_day"]

    def _custom(self, now, using, dt):
        today = now.date()
        for r in custom_list(self.db):
            if not r["on"] or not r["text"].strip() or not self._allowed_now(r, now):
                continue
            key = f"custom:{r['id']}"
            if key in self.snoozed:
                if now >= self.snoozed[key] and key not in self.open:
                    del self.snoozed[key]
                    self._fire(r, now, paced=False)   # you asked for it back now: it does not wait
                continue
            if r["kind"] == "interval":
                if using and not self._outstanding(r["id"]):
                    self.counters[r["id"]] = self.counters.get(r["id"], 0) + dt
                if self.counters.get(r["id"], 0) >= self._every(r["id"], r["every_sec"], now):
                    self.counters[r["id"]] = 0
                    self._fire(r, now, paced=r["every_sec"] >= UNPACED_UNDER_SEC)
                if using and not self._outstanding(r["id"]):
                    self.use_left[key] = self._every(r["id"], r["every_sec"], now) - self.counters.get(r["id"], 0)
            elif r["kind"] == "times":
                for t in r["times"]:
                    at = datetime.combine(today, parse_hhmm(t))
                    if at <= now < at + timedelta(minutes=LATE_FIRE_MIN) and (r["id"], today, t) not in self.fired:
                        self.fired.add((r["id"], today, t))
                        self._fire(r, now)
            else:
                start, end = (parse_hhmm(x) for x in r["window"])
                pick = self.random_at.get((r["id"], today))
                if pick is None:
                    lo, hi = start.hour * 60 + start.minute, end.hour * 60 + end.minute
                    m = self.rng.randint(lo, max(lo, hi - 1))
                    pick = self.random_at[(r["id"], today)] = time(m // 60, m % 60)
                at = datetime.combine(today, pick)
                if at <= now < at + timedelta(minutes=LATE_FIRE_MIN) and (r["id"], today, "random") not in self.fired:
                    self.fired.add((r["id"], today, "random"))
                    self._fire(r, now)
        if self.pending and not self.fullscreen and not self._paced(now):
            # the pace allows another interruption: everything that waited comes up together, as one popup
            due, self.pending = self.pending, []
            by_id = {x["id"]: x for x in custom_list(self.db)}
            # still allowed? Its hours may have ended, or its "stop after N done today" been reached, while it waited
            due = [rid for rid in due if rid in by_id and by_id[rid]["on"] and self._allowed_now(by_id[rid], now)]
            if len(due) == 1:
                self._fire(by_id[due[0]], now, paced=False)
            elif due:                           # drawn once with every line, not once per reminder joining it
                key = f"custom:{due[0]}"
                self.grouped[key] = due
                self._regroup(key, now)
                self.last_interruption = now
        for rid, at in list(self.checks.items()):
            if now >= at:
                del self.checks[rid]
                r = next((x for x in custom_list(self.db) if x["id"] == rid), None)
                if r:
                    self._popup(f"check:{rid}", "Did you actually do it?", r["text"], [("Yes", "yes"), ("No", "no")])

    def _quote(self, r: dict) -> str:
        pool = [q.strip() for q in r["quotes"].splitlines() if q.strip()]
        for pack in r["packs"]:
            pool += PACKS.get(pack, [])
        return self.rng.choice(pool) if pool else ""

    def _fire(self, r: dict, now: datetime, paced: bool = True):
        """Show a reminder; Snooze only while it has snoozes left (then it stays until Done).
        One already on screen takes this one in with it, so two things you could do in one go interrupt you
        once instead of twice - and so does the break prompt. Too soon after the last interruption, it waits."""
        if self._outstanding(r["id"]):               # already waiting for you: once is enough
            return
        if self.break_due:                           # a break comes up this very tick: it rides along in that
            self.pending.append(r["id"])
            return
        if "break" in self.open:                     # you are about to get up anyway: it rides along
            if r["id"] not in self.folded:
                self.folded.append(r["id"])
                self._show_break(load(self.db, BREAK_KEY, DEFAULT_BREAK))
            return
        open_key = next((k for k in self.open if k.startswith("custom:")), None)
        if open_key and open_key != f"custom:{r['id']}" and r["id"] not in self.grouped.get(open_key, []):
            self.grouped.setdefault(open_key, [open_key.split(":", 1)[1]]).append(r["id"])
            self._regroup(open_key, now)
            return
        if paced and not open_key and self._paced(now):
            if r["id"] not in self.pending:
                self.pending.append(r["id"])
            return
        key = f"custom:{r['id']}"
        self.grouped[key] = [r["id"]]
        quote = self._quote(r)
        buttons = [("Done", "done")]
        if self.snoozes_used.get(key, 0) < r["max_snooze"]:
            buttons.append((f"Snooze {r['snooze']} min", "snooze"))
        buttons.append((DISMISS_MARK, "dismiss"))
        self._popup(key, "Reminder", r["text"] + (f"\n\n{quote}" if quote else ""), buttons)

    def _regroup(self, key: str, now: datetime):
        """Re-show a popup that has taken in another reminder: both lines, one Done for the pair."""
        wanted = self.grouped[key]
        by_id = {x["id"]: x for x in custom_list(self.db)}
        lines = [by_id[rid]["text"] for rid in wanted if rid in by_id]
        buttons = [("Done", "done")]
        if all(self.snoozes_used.get(key, 0) < by_id[rid]["max_snooze"] for rid in wanted if rid in by_id):
            buttons.append((f"Snooze {by_id[wanted[0]]['snooze']} min", "snooze"))
        buttons.append((DISMISS_MARK, "dismiss"))
        self._close(key)
        self.open.add(key)
        self.ui.popup(key, "Reminders" if len(lines) > 1 else "Reminder",
                      "\n".join(f"• {line}" for line in lines) if len(lines) > 1 else lines[0], buttons)

    # ---------- answers from the UI ----------

    def held_answer(self, key: str, action: str) -> bool:
        """A button on the notice of a held popup (see _popup). False if that popup is no longer waiting - shown
        since, answered or gone - so a late click on an old notice does nothing."""
        if self.waiting.pop(key, None) is None:
            return False
        self.answer(key, action)
        return True

    def answer(self, key: str, action: str):
        now = self.now
        self.open.discard(key)
        if key == "break":
            b = load(self.db, BREAK_KEY, DEFAULT_BREAK)
            folded, self.folded = self.folded, []
            by_id = {x["id"]: x for x in custom_list(self.db)}
            if action == "start":
                if b.get("strict"):
                    self._start_break(now, b)          # strict: enforce it (minimise windows)
                else:                                   # gentle: trust you took it
                    self.continuous = 0
                    self.break_snoozes = 0
                    log(self.db, "break", "taken", now)
                self.streak["break"] = 0
                for rid in folded:                      # up and away: not "done" (that is yours to say), not skipped
                    log(self.db, rid, "with break", now)
                    self.streak[rid] = 0
            elif action == "snooze":
                self.break_snoozes += 1
                self.snoozed["break"] = now + timedelta(seconds=b.get("snooze_sec", 300))
                log(self.db, "break", "snoozed", now)
                self.folded = folded                    # they come back with it
            elif action == "dismiss":
                self.continuous = 0     # skipped, not taken: it asks again after another full stretch of use
                self.break_snoozes = 0
                log(self.db, "break", "dismissed", now)
                self._dismissed("break", "Time for a break", now)
                for rid in folded:
                    log(self.db, rid, "dismissed", now)
                    if rid in by_id:
                        self._dismissed(rid, by_id[rid]["text"], now)
        elif key == "sleep" and (action == "emergency" or action.startswith("emergency:")):
            # "Emergency (N left)" on the bedtime screen: one use of the kind picked ("emergency:<id>"; a plain
            # "emergency": the shortest kind with a use left) pauses the bedtime and break alerts for its length.
            # The bedtime settings themselves are not touched.
            try:
                tier = action.partition(":")[2] or emergency.shortest_left(self.db, now)
                self.paused = emergency.unlock(self.db, [], now, alerts=True, tier=tier)
            except ValueError as e:
                self.ui.toast(str(e))
                self.answer("sleep", "dismiss")   # (as if dismissed: back on the escalation)
                return
            self.ui.close("sleep")   # a bedtime screen that came back while you were confirming goes too
            log(self.db, "sleep", "emergency", now)
            self.ui.toast(f"Emergency: bedtime and break alerts paused until {self.paused:%H:%M}.")
        elif key == "sleep":
            s = load(self.db, SLEEP_KEY, DEFAULT_SLEEP)
            night = self._night(s, now)
            if night:
                bed, wake = night
                nkey = bed.strftime(TIME_FMT)
                if action == "off_tonight":                 # behind the challenge: no more tonight
                    self.sleep_next[nkey] = wake
                    log(self.db, "sleep", "off tonight", now)
                elif action == "back":                      # backed out of Disable alerts / Emergency while
                    self.sleep_next[nkey] = now              # Dismiss was still waiting: straight back
                elif action.startswith("snooze:"):          # behind the challenge: your own amount
                    self.sleep_next[nkey] = now + timedelta(minutes=max(1, int(action.split(":", 1)[1])))
                    log(self.db, "sleep", "snoozed", now)
                else:                                        # "dismiss": back after the escalating interval
                    self.sleep_next[nkey] = now + timedelta(seconds=self._sleep_interval(s, bed, now))
                    log(self.db, "sleep", "dismissed", now)
        elif key.startswith("custom:"):
            by_id = {x["id"]: x for x in custom_list(self.db)}
            for rid in self.grouped.pop(key, [key.split(":", 1)[1]]):   # one answer for everything it showed
                r = by_id.get(rid)
                if not r:
                    continue
                if action == "done":
                    self.snoozes_used.pop(key, None)
                    self.streak[rid] = 0
                    log(self.db, rid, "done", now)
                    self.done_today[(rid, now.date())] = self.done_today.get((rid, now.date()), 0) + 1
                    self.counters[rid] = 0
                    if r["check"]:
                        self.checks[rid] = now + timedelta(minutes=r["check"])
                elif action == "snooze":
                    self.snoozes_used[key] = self.snoozes_used.get(key, 0) + 1
                    self.snoozed[key] = now + timedelta(minutes=r["snooze"])
                    log(self.db, rid, "snoozed", now)
                elif action == "dismiss":
                    # Skip this one. Nothing counts as done, so the daily limit is untouched and no "did you
                    # actually do it?" follows; the next one comes at its normal time, which the interval
                    # counter (reset when it fired) and `fired` already arrange.
                    self.snoozes_used.pop(key, None)
                    log(self.db, rid, "dismissed", now)
                    self._dismissed(rid, r["text"], now)
        elif key.startswith("check:"):
            rid = key.split(":", 1)[1]
            log(self.db, rid, "really done" if action == "yes" else "not done", now)
            if action == "no":
                r = next((x for x in custom_list(self.db) if x["id"] == rid), None)
                if r:
                    self._fire(r, now, paced=False)
