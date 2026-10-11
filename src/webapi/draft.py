"""The draft (unsaved changes) over the bridge: BLUEPRINT 3.0 "Draft" and the "Blocklist / Draft" rows of section 4.

ONE `gui.draft.Draft` lives for the whole agent (made on first use, kept on the Context). It is the same class the old
window uses - it has no Tk - so every check on what a change loosens is the existing one: `Draft.save()` asks
`antibypass.draft_changes(saved, draft)` and, when something loosens, calls its `guard`. Here the guard is the agent's
GuardService (`ctx.guard.request`): the SERVER-BUILT save waits under a ticket for the challenge, the page only gets the
ticket. Nothing in this file (or in the page) decides what loosens, and no argument says "confirmed".

- Autosave on: every edit saves at once through that guard; a challenge that is cancelled (or never passed) throws
  the edit away (`Draft.discard`), as today. Autosave off: edits stay pending until Save (also through the guard).
- Every change of the draft pushes `draft` ({dirty, autosave, change_summary, ...}); the shell's save bar and the
  Blocking page listen to it. A finished save also pushes `blocks_changed` and pokes the status worker.
- Other tickets (Add / Edit, Groups, Notifications ...) change the draft with `run_edit(ctx, lambda d: ...)`, which
  holds the draft's lock, collects a challenge ticket if the guard asked for one and returns it as the Api reply.

The Api calls come from many threads; `lock` serialises every use of the draft. The Draft's `db` is a proxy to the
calling thread's own connection (see core.Context).
"""
import itertools
import threading
import time

import alerts
from reminders import MIN_INTERVAL_SEC
from gui.draft import Draft

from . import core
from .core import ApiError, ok

lock = threading.RLock()          # every use of the draft (the Api runs on many threads; GuardService actions too)
_create = threading.Lock()
_local = threading.local()        # per call: the guard's reply, whether the push should make the Blocking page reload
_rev = itertools.count(1)         # makes every `draft` push different (the publisher drops a repeat of the same payload)

REFRESH_SEC = 30                  # a clean draft is read again from the database at least this often (the service
#                                   removes expired temporary blocks) - or at once when the blocked items changed
MAX_LINES = 20                    # what draft_get_state lists
SUMMARY_NAMES = 3                 # names in the save bar's "what changed" text, then "and N more"
MAX_SETTING = 2000                # characters of a notification text setting


class _ThreadDb:
    """The Draft's `db`: always the connection of the thread that is calling (a sqlite connection belongs to one thread
    here, see core.Context)."""

    def __init__(self, ctx):
        self._ctx = ctx

    def __getattr__(self, name):
        return getattr(self._ctx.db, name)


class WebDraft(Draft):
    """gui.draft.Draft that remembers when it was read, so a clean draft is read again only when it can have changed."""

    def __init__(self, db):
        self.loaded_at = 0.0
        self.loaded_version = None
        super().__init__(db)

    def reload(self):
        super().reload()
        self.loaded_at = time.monotonic()
        self.loaded_version = self.db.items_version()

    def stale(self) -> bool:
        return self.db.items_version() != self.loaded_version or time.monotonic() - self.loaded_at > REFRESH_SEC

    def refresh_if_clean(self):
        if self.stale() and not self.dirty:
            self.reload()


# ---------------------------------------------------------------- the one Draft

@core.internal
def draft_of(ctx) -> WebDraft:
    """The agent's Draft (made on the first call). Also hands the shell its "what changed" function."""
    d = getattr(ctx, "draft", None)
    if d is None:
        with _create:
            d = getattr(ctx, "draft", None)
            if d is None:
                d = WebDraft(_ThreadDb(ctx))
                d.guard = _guard_for(ctx)
                d.listeners.append(lambda kind: _changed(ctx, d, kind))
                ctx.draft = d
                ctx.draft_state = lambda: _shell_state(d)
    return d


def _guard_for(ctx):
    """Draft.guard(changes, proceed, cancel): ask the GuardService; keep its reply for run_edit."""
    def locked(fn):
        def run():
            with lock:
                return fn()
        return run

    def guard(changes, proceed, cancel):
        _local.reply = ctx.guard.request(changes, locked(proceed), locked(cancel))
    return guard


def _asks(reply) -> bool:
    data = reply.get("data") if isinstance(reply, dict) and reply.get("ok") else None
    return isinstance(data, dict) and bool(data.get("needs_challenge"))


# ---------------------------------------------------------------- what the save bar says

@core.internal
def change_lines(d: Draft) -> list[str]:
    """What is waiting to be saved, one short line each ("Removed YouTube"), in a steady order."""
    lines = []
    for item_id, item in sorted(d.items.items(), key=lambda kv: kv[1]["display_name"].lower()):
        saved = d.saved_items.get(item_id)
        if saved is None:
            lines.append(f"Added {item['display_name']}")
        elif d.is_unsaved(item_id):
            if bool(saved.get("disabled")) != bool(item.get("disabled")):
                lines.append(f"{'Disabled' if item.get('disabled') else 'Enabled'} {item['display_name']}")
            else:
                lines.append(f"Changed {item['display_name']}")
    for item_id in sorted(set(d.saved_items) - set(d.items), key=lambda i: d.saved_items[i]["display_name"].lower()):
        lines.append(f"Removed {d.saved_items[item_id]['display_name']}")
    for group_id, group in sorted(d.groups.items(), key=lambda kv: kv[1]["name"].lower()):
        saved = d.saved_groups.get(group_id)
        if saved is None:
            lines.append(f"Added group {group['name']}")
        elif d.is_group_unsaved(group_id):
            if bool(saved.get("disabled")) != bool(group.get("disabled")):
                lines.append(f"{'Disabled' if group.get('disabled') else 'Enabled'} group {group['name']}")
            else:
                lines.append(f"Changed group {group['name']}")
    for group_id in sorted(set(d.saved_groups) - set(d.groups), key=lambda g: d.saved_groups[g]["name"].lower()):
        lines.append(f"Removed group {d.saved_groups[group_id]['name']}")
    if d.settings != d.saved_settings:
        lines.append("Notification settings")
    return lines


@core.internal
def summary_of(lines: list[str]) -> str:
    """The short text beside "Unsaved changes": the first few lines, then "and N more"."""
    if len(lines) <= SUMMARY_NAMES:
        return ", ".join(lines)
    return ", ".join(lines[:SUMMARY_NAMES]) + f" and {len(lines) - SUMMARY_NAMES} more"


@core.internal
def state_of(d: Draft) -> dict:
    """{dirty, autosave, change_summary}: what the save bar shows (get_status carries the same three)."""
    with lock:
        dirty = d.dirty
        return {"dirty": dirty, "autosave": d.autosave, "change_summary": summary_of(change_lines(d)) if dirty else ""}


def _shell_state(d: Draft):
    """ctx.draft_state: (dirty, summary) for webapi/shell.py."""
    with lock:
        dirty = d.dirty
        return dirty, summary_of(change_lines(d)) if dirty else ""


# ---------------------------------------------------------------- telling the page

def _publish(ctx, event, payload):
    fn = getattr(ctx, "publish", None)
    if fn is not None:
        try:
            fn(event, payload)
        except Exception:
            import traceback
            core.log_error(f"Publishing {event} failed: {traceback.format_exc()}")


def _poke(ctx):
    """Tell the status worker the blocks changed (tray, mute rule and the page's counts follow at once)."""
    worker = getattr(getattr(ctx, "hub", None), "status_worker", None)
    if worker is not None:
        worker.poke()


def _changed(ctx, d, kind):
    """Draft listener: kind is "changed" / "saved" / "discarded". Runs with the lock held, on the thread that edited."""
    blocks = getattr(_local, "blocks", True)
    _publish(ctx, "draft", {**state_of(d), "what": kind, "rev": next(_rev), "blocks": blocks})
    if kind == "saved" and blocks:
        _publish(ctx, "blocks_changed", {"rev": next(_rev)})
        _poke(ctx)


# ---------------------------------------------------------------- the way other Api modules edit the draft

@core.internal
def run_edit(ctx, mutate, blocks: bool = True):
    """Run `mutate(draft)` under the draft's lock. With autosave on the edit is saved right then, through the guard: if
    that needs the challenge the reply is {needs_challenge, ticket, changes} (the edit waits under the ticket; a cancel
    throws it away). Otherwise -> {dirty, autosave, change_summary, result}. `blocks=False` for an edit that does not
    touch the blocklist (the page then does not reload the blocks)."""
    d = draft_of(ctx)
    with lock:
        _local.reply, _local.blocks = None, blocks
        try:
            result = mutate(d)
            reply = _local.reply
        finally:
            _local.reply, _local.blocks = None, True
        state = state_of(d)
    if reply is not None and _asks(reply):
        return reply
    return ok({**state, "result": result} if result is not None else state)


# Number settings: whole digits only, within these limits (minutes: the old page offered 1-60 and 1-60; repeat seconds:
# 0 = off, else MIN_INTERVAL_SEC to a day). Other keys: "1"/"0", a known format, or free text (the messages).
_NUMBER_RANGES = {"notify.cooldown_min": (1, 1440), "notify.warn.minutes": (1, 1440), "notify.warn.repeat_sec": (MIN_INTERVAL_SEC, 86400)}


def _setting_ok(key: str, value: str) -> bool:
    if key in _NUMBER_RANGES:
        if not (value.isascii() and value.isdigit()):
            return False
        low, high = _NUMBER_RANGES[key]
        number = int(value)
        return low <= number <= high or (key == "notify.warn.repeat_sec" and number == 0)
    if key == "notify.format":
        return value in alerts.FORMATS
    if key.startswith("notify.msg."):
        return True
    return value in ("1", "0")


# ---------------------------------------------------------------- Api

@core.read
def draft_get_state(ctx):
    """{dirty, autosave, change_summary, changes: [line]}: whether anything waits to be saved, and what."""
    d = draft_of(ctx)
    with lock:
        lines = change_lines(d)
        return {"dirty": d.dirty, "autosave": d.autosave, "change_summary": summary_of(lines) if d.dirty else "",
                "changes": lines[:MAX_LINES] if d.dirty else []}


@core.loosen
def draft_set_autosave(ctx, on):
    """Auto-save on or off. Turning it ON with unsaved edits saves them now - through the guard, so edits that loosen
    something need the challenge (and are thrown away if it is cancelled)."""
    if type(on) is not bool:
        raise ApiError("Bad request: on or off expected.")
    return run_edit(ctx, lambda d: setattr(d, "autosave", on))


@core.loosen
def draft_save(ctx):
    """Save the unsaved changes. What loosens a block needs the challenge: the reply is then {needs_challenge, ticket,
    changes} and nothing is saved until Python has checked the challenge."""
    return run_edit(ctx, lambda d: d.save())


@core.ui
def draft_discard(ctx):
    """Throw the unsaved changes away (the saved blocks are not touched)."""
    return run_edit(ctx, lambda d: d.discard())


@core.ui
def draft_set_setting(ctx, key, value):
    """A notification setting (alerts.DEFAULTS, every key is `notify.*`) as part of the draft - for the Notifications
    page. Nothing else can be set from here. The page debounces typing (js/draft.js)."""
    if not isinstance(key, str) or not key.startswith("notify.") or key not in alerts.DEFAULTS:
        raise ApiError("That setting can't be changed from here.")
    if not isinstance(value, str) or len(value) > MAX_SETTING or not _setting_ok(key, value):
        raise ApiError("That value isn't valid.")
    return run_edit(ctx, lambda d: d.set_setting(key, value), blocks=False)
