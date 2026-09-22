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
from datetime import datetime, time, timedelta

from rules import TIME_FMT, days_text, parse_hhmm

TICK_SEC = 5
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
DEFAULT_SLEEP = {"on": False, "bedtime": "23:00", "wake": "07:00", "before": 30, "repeat": 5, "mode": "",
                 "warn_text": "", "text": ""}
DEFAULT_BREAK = {"on": True, "every": 45, "length": 5, "strict": False, "snooze": 5, "max_snooze": 2,
                 "twenty": False, "text": "", "twenty_text": ""}
DEFAULT_CUSTOM = {"on": True, "text": "", "kind": "interval", "every": 60, "times": ["12:00"],
                  "days": [0, 1, 2, 3, 4, 5, 6], "window": ["10:00", "18:00"], "snooze": 5, "max_snooze": 3,
                  "check": 0, "packs": [], "quotes": "",
                  "hours": False,      # only between window[0] and window[1] (every kind, not just random)
                  "per_day": 0}        # stop for the day after this many Done (0 = no limit)
GROUP_MIN = 5          # a reminder due within this many minutes of one already on screen joins it
DISMISS_MARK = "✕"    # the X button: closes it without claiming you did it

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


def message(custom: str, default: str, **values) -> str:
    """What a reminder says: your own wording if you wrote any, otherwise the standard one. A {placeholder}
    that doesn't exist leaves the text as you typed it rather than breaking the reminder."""
    text = (custom or "").strip() or default
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):
        return text


def load(db, key: str, default):
    try:
        saved = json.loads(db.get_setting(key, "") or "null")
    except ValueError:
        saved = None
    if isinstance(default, dict):
        return {**default, **(saved or {})}
    return saved if saved is not None else default


def save(db, key: str, value):
    db.set_setting(key, json.dumps(value))


def custom_list(db) -> list[dict]:
    return [{**DEFAULT_CUSTOM, **r} for r in load(db, CUSTOM_KEY, [])]


def log(db, what: str, result: str, now: datetime):
    with db.conn:
        db.conn.execute("INSERT INTO reminder_log VALUES (?, ?, ?)", (now.strftime(TIME_FMT), what, result))


def counts(db, what: str, since: datetime) -> dict[str, int]:
    rows = db.conn.execute("SELECT result, COUNT(*) FROM reminder_log WHERE what = ? AND timestamp >= ? "
                           "GROUP BY result", (what, since.strftime(TIME_FMT)))
    return dict(rows.fetchall())


def schedule_text(r: dict) -> str:
    if r["kind"] == "interval":
        text = f"every {r['every']} min of use"
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
        self.waiting: dict[str, tuple] = {}  # popups held back while a full-screen app is in front
        self.continuous = 0.0                # seconds of use since the last break
        self.twenty = 0.0
        self.break_until: datetime | None = None
        self.break_snoozes = 0               # snoozes used for the current break prompt (strict mode limits these)
        self.snoozed: dict[str, datetime] = {}               # key -> fire again at
        self.snoozes_used: dict[str, int] = {}               # key -> snoozes since it was last done
        self.counters: dict[str, float] = {}                 # interval reminders: seconds of use so far
        self.fired: set[tuple] = set()                       # (id, date, "HH:MM") already shown
        self.random_at: dict[tuple, time] = {}               # (id, date) -> today's random time
        self.checks: dict[str, datetime] = {}                # reminder id -> ask "did you do it?" at
        self.sleep_next: dict[str, datetime] = {}            # night -> next bedtime overlay
        self.done_today: dict[tuple, int] = {}               # (reminder, date) -> times done (the daily limit)
        self.grouped: dict[str, list[str]] = {}              # popup key -> the reminders it is showing

    # ---------- showing ----------

    def _popup(self, key: str, title: str, text: str, buttons: list[tuple[str, str]]):
        if key in self.open:
            return
        if self.fullscreen:
            if key not in self.waiting:
                self.ui.toast(f"{title}: {text.splitlines()[0]}")
                self.waiting[key] = (title, text, buttons)
            return
        self.waiting.pop(key, None)
        self.open.add(key)
        self.ui.popup(key, title, text, buttons)

    def _overlay(self, key: str, title: str, text: str, until: datetime | None, buttons: list[tuple[str, str]]):
        if self.quiet:
            return   # Do not disturb: it comes up on the tick after that ends (while it is still due)
        if key not in self.open:
            self.open.add(key)
            self.ui.overlay(key, title, text, until, buttons)

    def _close(self, key: str):
        self.waiting.pop(key, None)
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
        using = idle_sec < USING_IDLE_SEC
        self._breaks(now, idle_sec, using, dt)
        self._sleep(now)
        self._custom(now, using, dt)
        if not fullscreen:
            for key, (title, text, buttons) in list(self.waiting.items()):
                self._popup(key, title, text, buttons)

    # ---------- breaks ----------

    def _breaks(self, now, idle_sec, using, dt):
        b = load(self.db, BREAK_KEY, DEFAULT_BREAK)
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
        if self.continuous >= b["every"] * 60 and "break" not in self.open:
            strict = b.get("strict")
            snoozes_left = self.break_snoozes < b.get("max_snooze", 2)
            if strict and not snoozes_left:
                self._start_break(now, b)   # strict, out of snoozes -> the break starts by itself
                return
            buttons = [("Start break", "start")]
            if not strict or snoozes_left:
                buttons.append((f"Snooze {b.get('snooze', 5)} min", "snooze"))
            if not strict:          # a strict break must not gain a one-click way out
                buttons.append((DISMISS_MARK, "dismiss"))
            self._popup("break", "Time for a break",
                        message(b.get("text"), BREAK_TEXT, every=b["every"], length=b["length"])
                        + (f"\n\nStrict break: {b.get('max_snooze', 2) - self.break_snoozes} snooze"
                           f"{'s' * (b.get('max_snooze', 2) - self.break_snoozes != 1)} left, then it starts on its "
                           "own." if strict else ""), buttons)

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

    def _sleep(self, now):
        s = load(self.db, SLEEP_KEY, DEFAULT_SLEEP)
        night = self._night(s, now) if s["on"] else None
        if not night:
            self._close("sleep")
            return
        bed, wake = night
        key = bed.strftime(TIME_FMT)
        if now < bed:
            if ("warn", key) not in self.fired:
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
        if now >= self.sleep_next.get(key, now) and "sleep" not in self.open:
            self._overlay("sleep", "Time for bed",
                          message(s.get("text"), SLEEP_TEXT, time=f"{now:%H:%M}", bedtime=f"{bed:%H:%M}",
                                  wake=f"{wake:%H:%M}"),
                          None, [("Going to bed", "bed"), ("5 more minutes", "more")])

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
                    self._fire(r, now)
                continue
            if r["kind"] == "interval":
                if using:
                    self.counters[r["id"]] = self.counters.get(r["id"], 0) + dt
                if self.counters.get(r["id"], 0) >= r["every"] * 60:
                    self.counters[r["id"]] = 0
                    self._fire(r, now)
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

    def _fire(self, r: dict, now: datetime):
        """Show a reminder; Snooze only while it has snoozes left (then it stays until Done).
        One already on screen takes this one in with it, so two things you could do in one go interrupt you
        once instead of twice."""
        open_key = next((k for k in self.open if k.startswith("custom:")), None)
        if open_key and open_key != f"custom:{r['id']}" and r["id"] not in self.grouped.get(open_key, []):
            self.grouped.setdefault(open_key, [open_key.split(":", 1)[1]]).append(r["id"])
            self._regroup(open_key, now)
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

    def answer(self, key: str, action: str):
        now = self.now
        self.open.discard(key)
        if key == "break":
            b = load(self.db, BREAK_KEY, DEFAULT_BREAK)
            if action == "start":
                if b.get("strict"):
                    self._start_break(now, b)          # strict: enforce it (minimise windows)
                else:                                   # gentle: trust you took it
                    self.continuous = 0
                    self.break_snoozes = 0
                    log(self.db, "break", "taken", now)
            elif action == "snooze":
                self.break_snoozes += 1
                self.snoozed["break"] = now + timedelta(minutes=b.get("snooze", 5))
                log(self.db, "break", "snoozed", now)
            elif action == "dismiss":
                self.continuous = 0     # skipped, not taken: it asks again after another full stretch of use
                self.break_snoozes = 0
                log(self.db, "break", "dismissed", now)
        elif key == "sleep":
            s = load(self.db, SLEEP_KEY, DEFAULT_SLEEP)
            night = self._night(s, now)
            if night:
                wait = 5 if action == "more" else s["repeat"]
                self.sleep_next[night[0].strftime(TIME_FMT)] = now + timedelta(minutes=wait)
            log(self.db, "sleep", action, now)
        elif key.startswith("custom:"):
            by_id = {x["id"]: x for x in custom_list(self.db)}
            for rid in self.grouped.pop(key, [key.split(":", 1)[1]]):   # one answer for everything it showed
                r = by_id.get(rid)
                if not r:
                    continue
                if action == "done":
                    self.snoozes_used.pop(key, None)
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
        elif key.startswith("check:"):
            rid = key.split(":", 1)[1]
            log(self.db, rid, "really done" if action == "yes" else "not done", now)
            if action == "no":
                r = next((x for x in custom_list(self.db) if x["id"] == rid), None)
                if r:
                    self._fire(r, now)
