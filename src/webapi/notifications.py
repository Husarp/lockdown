"""Api for the Notifications page (BLUEPRINT 3.9): the blocked-visit alerts, the warnings before a block starts and the
weekly summary.

Two kinds of setting live here, as in the old window:
- `notify.*` (the alerts and the warnings) are part of the DRAFT: they change with `draft_set_setting` (webapi/draft.py,
  which accepts only `notify.*` keys from alerts.DEFAULTS, checks every value and shows "Notification settings" in the
  save bar), and wait for Save while Auto-save is off. Nothing here writes them behind the draft's back. The one box
  that needs parsing - "remind me again every" (30s, 2 min, Off) - goes through `notify_repeat_set`, which parses the
  words in Python and hands the number on to that same method.
- the weekly summary (`digest.*`) is saved at once (`digest_set`), like the old window.

Nothing here can loosen a block: all of it is classified read / ui. "Show as" is picked up by the notice sink from the
next notice on (the draft's "saved" listener calls ctx.notices.refresh(), see webapi/draft.py).
"""
import alerts
import digest
import reminders
from rules import DAY_NAMES, parse_hhmm

from . import core, draft as drafts
from .core import ApiError

TEST_NOTE = "This is a test notice from Lockdown."


def _flag(value) -> bool:
    return value == "1"


def _options(options, current) -> list:
    """The quick picks, plus the saved value when it is none of them (a hand-edited or older setting is shown, not lost)."""
    return sorted({*options, current})


@core.read
def notifications_get(ctx):
    """Everything the page shows: {reasons: [{key, label, default}], enabled: {reason: bool}, messages: {reason: text},
    cooldown, cooldown_options, format, formats: [{key, label}], warn: {enabled, minutes, repeat_sec, repeat_text},
    started, digest: {enabled, day, time, days, times, text}, draft: {dirty, autosave, change_summary}}.
    The notification settings are the DRAFT's (unsaved edits included), the weekly summary is the saved one."""
    db = ctx.db
    d = drafts.draft_of(ctx)
    with drafts.lock:
        pending = d.dirty
        s = dict(d.settings) if pending else {k: alerts.get(db, k) for k in alerts.DEFAULTS}
        state = drafts.state_of(d)
    cooldown, minutes = int(s["notify.cooldown_min"]), int(s["notify.warn.minutes"])
    repeat = int(s["notify.warn.repeat_sec"])
    time_ = digest.get(db, "digest.time")
    hours = [f"{h:02d}:00" for h in range(24)]
    return {
        "reasons": [{"key": r, "label": label, "default": alerts.DEFAULT_MESSAGES[r]}
                    for r, (label, _text) in alerts.REASONS.items()],
        "enabled": {r: _flag(s[f"notify.enabled.{r}"]) for r in alerts.REASONS},
        "messages": {r: s[f"notify.msg.{r}"] for r in alerts.REASONS},
        "cooldown": cooldown, "cooldown_options": _options(alerts.COOLDOWN_OPTIONS, cooldown),
        "format": s["notify.format"], "formats": [{"key": k, "label": v} for k, v in alerts.FORMATS.items()],
        "warn": {"enabled": _flag(s["notify.warn.enabled"]), "minutes": minutes, "repeat_sec": repeat,
                 "repeat_text": reminders.seconds_text(repeat)},
        "started": _flag(s["notify.started.enabled"]),
        "digest": {"enabled": _flag(digest.get(db, "digest.enabled")), "day": int(digest.get(db, "digest.day")),
                   "time": time_, "days": DAY_NAMES, "times": sorted({*hours, time_}),
                   "text": digest.schedule_text(db)},
        "draft": state,
    }


@core.ui
def notify_repeat_set(ctx, text):
    """"While I'm using it, remind me again every ...": the words as typed ("30s", "2 min", "Off") parsed here, then saved
    as a draft setting exactly like the others. -> the draft state + {seconds, text} (what the box should say now)."""
    if not isinstance(text, str) or len(text) > 40:
        raise ApiError("Write a time like 30s or 2 min, or Off.")
    try:
        seconds = reminders.parse_interval(text, allow_off=True)
    except ValueError as e:
        raise ApiError(str(e)) from None
    reply = drafts.draft_set_setting(ctx, "notify.warn.repeat_sec", str(seconds))
    if reply.get("ok") and isinstance(reply.get("data"), dict):
        reply["data"]["seconds"] = seconds
        reply["data"]["text"] = reminders.seconds_text(seconds)
    return reply


@core.ui
def digest_set(ctx, enabled, day, time):
    """The weekly summary: on or off, the weekday (0 = Monday) and the time ("19:00"). Saved at once, no draft."""
    if type(enabled) is not bool:
        raise ApiError("Bad request: on or off expected.")
    if type(day) is not int or not 0 <= day <= 6:
        raise ApiError("Pick a day of the week.")
    if not isinstance(time, str) or len(time) > 5:
        raise ApiError("Pick a time like 19:00.")
    try:
        at = parse_hhmm(time)
    except ValueError:
        raise ApiError("Pick a time like 19:00.") from None
    db = ctx.db
    db.set_setting("digest.enabled", "1" if enabled else "0")
    db.set_setting("digest.day", str(day))
    db.set_setting("digest.time", f"{at:%H:%M}")
    return {"enabled": enabled, "day": day, "time": f"{at:%H:%M}", "text": digest.schedule_text(db)}


@core.ui
def notify_test(ctx):
    """"Send a test": one notice through the normal path (the "Show as" choice, the mute rules). -> {sent: false} when a
    muting mode or a silent pause held it."""
    hub = getattr(ctx, "hub", None)
    if hub is None:
        raise ApiError("Notices aren't available here.")
    return {"sent": bool(hub.notify(TEST_NOTE))}
