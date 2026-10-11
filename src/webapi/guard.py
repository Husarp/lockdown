"""GuardService: the Anti-Bypass challenge, owned by Python.

Anything that loosens a block asks `ctx.guard.request(changes, action, cancel)`. If the challenge isn't needed the
action runs at once; otherwise the SERVER-BUILT action (a closure over validated data) is kept here under a one-time
ticket and the page only gets the ticket. The page then opens the challenge, which shows what Python issued
(`challenge_begin`) and hands back what was typed (`challenge_submit`). Python compares it, checks it took a
plausible time to type, unlocks (antibypass.unlock) and only then runs the stored action. The page never says
"confirmed" - there is no such argument anywhere.

One challenge is open at a time (a new request cancels the old one), a ticket works once and expires after 10
minutes. Grace (an exact revert of a tightening within 10 s needs no challenge) lives here too.
"""
import random
import secrets
import threading
import time

import antibypass
from rules import days_text

from . import core
from .core import Envelope, fail, ok

MIN_MS_PER_CHAR = 35        # typing the phrase faster than this per character isn't a human ("Too fast")
TICKET_TTL_SEC = 600        # an open challenge is forgotten after 10 minutes
GRACE_SEC = 10              # after a tightening change, this long to undo it (revert only) without the challenge
SHOWN_CHANGES = 5           # lines listed in the challenge; the rest is "and N more"


class _Pending:
    """One request waiting for its challenge."""

    def __init__(self, changes, action, cancel, force, now):
        self.changes, self.action, self.cancel, self.force = changes, action, cancel, force
        self.created = now
        self.mode = None            # set by begin(): phrase | grid | waiting | closed
        self.phrase = None          # what must be typed (phrase mode) ...
        self.words = None           # ... or the words, one at a time (grid mode)
        self.started = 0.0          # when begin() showed it (the typing-time check counts from here)


class GuardService:
    """One per agent, thread-safe. `db_provider()` gives the calling thread's own Database; `clock(db)` the trusted
    time; `monotonic` times tickets and typing (all three are swapped by the tests)."""

    def __init__(self, db_provider, clock=None, monotonic=time.monotonic, rng=random):
        self._db = db_provider
        self._clock = clock
        self._mono = monotonic
        self._rng = rng
        self._lock = threading.Lock()
        self._ticket: str | None = None
        self._open: _Pending | None = None
        self._grace: dict[str, tuple] = {}      # domain -> (state to revert to, expiry)

    def _now(self, db):
        if self._clock:
            return self._clock(db)
        from trusted_time import now_from_db
        return now_from_db(db)

    # ---------------- asking

    def request(self, changes: list[str], action, cancel=None, force: bool = False) -> Envelope:
        """Run `action` now if Anti-Bypass allows it (status "free"; with force - a locked mode - only while a passed
        challenge is still unlocked), else keep it under a ticket for the challenge. `cancel` undoes whatever the
        page shows if the challenge is cancelled or never passed."""
        db = self._db()
        cfg, now = antibypass.settings(db), self._now(db)
        if antibypass.status(cfg, now) == "free" and not (force and not antibypass.unlocked_until(cfg, now)):
            return ok(action())
        changes = [str(c) for c in changes]
        pending = _Pending(changes, action, cancel, force, self._mono())
        ticket = secrets.token_urlsafe(18)
        with self._lock:
            previous, self._ticket, self._open = self._open, ticket, pending
        self._run_cancel(previous)          # one challenge at a time: a new request cancels the one still open
        return ok({"needs_challenge": True, "ticket": ticket, "changes": changes[:SHOWN_CHANGES],
                   "more": max(0, len(changes) - SHOWN_CHANGES)})

    def _run_cancel(self, pending):
        if pending is not None and pending.cancel is not None:
            try:
                pending.cancel()
            except Exception:
                import traceback
                core.log_error(f"Challenge cancel failed: {traceback.format_exc()}")

    def _live(self, ticket):
        """The open request for `ticket`, or None (unknown, used, replaced or expired - an expired one is cancelled)."""
        expired = None
        with self._lock:
            if not isinstance(ticket, str) or ticket != self._ticket or self._open is None:
                return None
            if self._mono() - self._open.created > TICKET_TTL_SEC:
                expired, self._ticket, self._open = self._open, None, None
            else:
                return self._open
        self._run_cancel(expired)
        return None

    # ---------------- the challenge

    def begin(self, ticket) -> Envelope:
        """What the challenge shows: {mode: phrase | grid | waiting | closed, changes, more, ...}. Phrase and grid words
        are made and kept here, fresh on every call for a random phrase."""
        pending = self._live(ticket)
        if pending is None:
            return fail("This challenge has expired. Start the change again.")
        db = self._db()
        cfg, now = antibypass.settings(db), self._now(db)
        data = {"changes": pending.changes[:SHOWN_CHANGES], "more": max(0, len(pending.changes) - SHOWN_CHANGES),
                "unlock_min": antibypass.UNLOCK_MIN, "now": now.isoformat(timespec="seconds")}
        waiting, phrase = antibypass.waiting_until(cfg, now), None
        if waiting:
            mode = "waiting"        # passed already: never show the phrase again (typing it again restarts the wait)
            data["waiting_until"] = waiting.isoformat(timespec="seconds")
        elif antibypass.status(cfg, now) == "closed":
            mode = "closed"
            nxt = antibypass.next_hours(cfg, now)
            data["next_open"] = nxt.isoformat(timespec="seconds") if nxt else None
            data["hours_text"] = ", ".join(f"{days_text(w['days'])} {w['start']}-{w['end']}" for w in cfg["windows"])
        else:
            phrase = antibypass.phrase_for(cfg, self._rng)
            if not phrase.strip():       # (a blank own phrase would pass by typing nothing)
                phrase = antibypass.new_phrase(cfg["length"], cfg.get("complex", False), self._rng)
            mode = "grid" if cfg["grid"] else "phrase"
            data["length"] = len(phrase)
            if mode == "grid":
                data["grid_words"] = phrase.split()
            else:
                data["phrase"] = phrase
        data["mode"] = mode
        with self._lock:
            if self._open is not pending:        # replaced while we were working
                return fail("This challenge has expired. Start the change again.")
            pending.mode = mode
            pending.phrase = phrase
            pending.words = phrase.split() if mode == "grid" else None
            pending.started = self._mono()
        return ok(data)

    def submit(self, ticket, typed) -> Envelope:
        """The phrase as typed (a string), or in grid mode the words in order (a list). Exact compare, plausible
        typing time, then unlock and run the stored action - or, if a wait is set, cancel it and say when."""
        pending = self._live(ticket)
        if pending is None:
            return fail("This challenge has expired. Start the change again.")
        db = self._db()
        cfg, now = antibypass.settings(db), self._now(db)
        if pending.mode not in ("phrase", "grid"):
            return fail("There is nothing to type right now.")
        if antibypass.waiting_until(cfg, now) or antibypass.status(cfg, now) == "closed":
            return fail("Changes like this aren't possible right now.")
        if pending.mode == "grid":
            right = (isinstance(typed, list) and all(isinstance(w, str) for w in typed) and typed == pending.words)
            chars = sum(len(w) for w in pending.words)
        else:
            right = isinstance(typed, str) and typed == pending.phrase
            chars = len(pending.phrase)
        if not right:
            return fail("That doesn't match - try again.")
        with self._lock:
            if self._open is not pending:
                return fail("This challenge has expired. Start the change again.")
            took = self._mono() - pending.started
            if took < chars * MIN_MS_PER_CHAR / 1000:
                pending.started = self._mono()      # (the clock starts over: a macro has to wait the full time again)
                return fail("Too fast - type it yourself.")
            self._ticket = self._open = None        # one use: whatever happens next, this ticket is gone
        waiting = antibypass.unlock(db, now)
        if waiting:
            self._run_cancel(pending)               # a cooling-off period: nothing is done now, come back later
            return ok({"passed": True, "waiting_until": waiting.isoformat(timespec="seconds")})
        return ok({"passed": True, "result": pending.action()})

    def cancel(self, ticket) -> Envelope:
        """The challenge was closed (Cancel, Esc, OK on a waiting / closed note): run the stored cancel."""
        with self._lock:
            if not isinstance(ticket, str) or ticket != self._ticket:
                return ok()
            pending, self._ticket, self._open = self._open, None, None
        self._run_cancel(pending)
        return ok()

    # ---------------- grace

    def grace_note(self, domain: str, revert_to):
        """Remember the state to revert to after a tightening change, so an accidental toggle can be undone within
        a few seconds without the challenge (see grace_ok)."""
        with self._lock:
            self._grace[domain] = (revert_to, self._mono() + GRACE_SEC)

    def grace_ok(self, domain: str, new_state) -> bool:
        """True if `new_state` exactly reverts a tightening made in this domain in the last few seconds - then the
        loosening is allowed without the challenge (and the grace is spent)."""
        with self._lock:
            entry = self._grace.get(domain)
            if entry is None:
                return False
            state, until = entry
            if self._mono() >= until:
                del self._grace[domain]
                return False
            if state == new_state:
                del self._grace[domain]
                return True
            return False


# ---------------------------------------------------------------- Api methods

@core.ui
def challenge_begin(ctx, ticket):
    """What the challenge shows for this ticket (see GuardService.begin)."""
    return ctx.guard.begin(ticket)


@core.ui
def challenge_submit(ctx, ticket, typed):
    """The phrase typed (or the grid words, in order). Python decides; on a pass the stored change is made."""
    return ctx.guard.submit(ticket, typed)


@core.ui
def challenge_cancel(ctx, ticket):
    return ctx.guard.cancel(ticket)


@core.read
def antibypass_status(ctx):
    """Where Anti-Bypass stands right now, for the "Locked" strip and banners. Never the custom phrase itself."""
    db = ctx.db
    cfg, now = antibypass.settings(db), ctx.now()
    waiting = antibypass.waiting_until(cfg, now)
    until = antibypass.unlocked_until(cfg, now)
    nxt = antibypass.next_hours(cfg, now) if cfg["hours"] else None
    return {"status": antibypass.status(cfg, now), "active": antibypass.active(cfg), "phrase": cfg["phrase"],
            "grid": cfg["grid"], "complex": cfg.get("complex", False), "custom_phrase": bool(cfg.get("custom_phrase")),
            "length": cfg["length"], "hours": cfg["hours"], "windows": cfg["windows"], "wait_min": cfg["wait_min"],
            "unlock_min": antibypass.UNLOCK_MIN, "unlocked_until": until, "waiting_until": waiting,
            "next_open": nxt, "off": antibypass.is_off(db), "now": now}
