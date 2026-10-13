"""GuardService: the Anti-Bypass challenge, owned by Python.

Anything that loosens a block asks `ctx.guard.request(changes, action, cancel)`. If the challenge isn't needed the
action runs at once; otherwise the SERVER-BUILT action (a closure over validated data) is kept here under a one-time
ticket and the page only gets the ticket. The page then opens the challenge, which shows what Python issued
(`challenge_begin`) and hands back what was typed or tapped (`challenge_submit`). The challenge is a list of steps
(challenges.py: math, memory grid, sorting, typed phrase); a `challenges.Run` per open ticket holds the puzzle, the
answer and the progress, and Python judges every answer. When the last step is passed it unlocks (antibypass.unlock)
and only then runs the stored action. The page never says "confirmed" - there is no such argument anywhere.
The Anti-Bypass list may also hold an "Own password" / "Authenticator code" step: `secrets` (challenge_secrets.Checker:
wrong tries in a row are counted in the database, so across runs and processes) checks those. When a recovery code
exists, a real Anti-Bypass run (not a practice, not the bedtime screen) may be passed whole by it ({"recovery": code}
as the answer): it is used up, a new one is made at once and the reply carries it (`recovery_code`, shown once).
The uninstaller's challenge (`uninstall`, from `--challenge`) asks the list without the password / authenticator steps
and lets only the uninstall through: it never opens the unlock for other changes (antibypass.uninstall_pass).

One challenge is open at a time (a new request cancels the old one), a ticket works once and expires after 10
minutes. Grace (an exact revert of a tightening within 10 s needs no challenge) lives here too.

The bedtime screen's "Challenge before Dismiss" uses the same service with the PLAIN variant of `request` (steps=,
phrase_opts=, plain=True): it always asks (whatever Anti-Bypass says - there is no Anti-Bypass wait or hours question
for it), the list is the one the caller gives (reminders.challenge_steps_at), passing it opens NO unlock and starts no
wait (it only runs the action: the Dismiss, once), and the App log says "Bedtime challenge ..." instead of the
Anti-Bypass lines.
"""
import random
import secrets
import threading
import time

import antibypass
import applog
import challenge_secrets
import challenges
from rules import days_text

from . import core
from .core import Envelope, fail, ok

MIN_MS_PER_CHAR = challenges.PHRASE_MS_PER_CHAR   # typing the phrase faster than this per character isn't a human
TICKET_TTL_SEC = 600        # an open challenge is forgotten after 10 minutes
GRACE_SEC = 10              # after a tightening change, this long to undo it (revert only) without the challenge
SHOWN_CHANGES = 5           # lines listed in the challenge; the rest is "and N more"
MODES = ("phrase", "grid", "math", "memory", "sort", "password", "totp")      # the ones with something to answer
REFUSED_TEXT = {"password": "That isn't the password - try again.",
                "totp": "That code isn't right (or it was used already) - type the one your app shows now.",
                "recovery": "That isn't the recovery code - try again."}
REFUSED_LOG = {"password": "wrong password", "totp": "wrong authenticator code", "recovery": "wrong recovery code"}


class _Pending:
    """One request waiting for its challenge."""

    def __init__(self, changes, action, cancel, force, now, dry=False, plain=None, uninstall=False):
        self.changes, self.action, self.cancel, self.force = changes, action, cancel, force
        self.uninstall = uninstall  # the uninstaller's challenge: no password / authenticator steps, opens no unlock
        self.dry = dry              # a dry run ("Try the challenge"): a pass opens no unlock and starts no wait
        self.plain = plain          # the bedtime variant: {steps, complex, grid, title, note}, else None (Anti-Bypass)
        self.created = now
        self.mode = None            # set by begin(): phrase | grid | math | memory | sort | password | totp | waiting | closed
        self.run = None             # the challenges.Run (steps, puzzle, progress) while a puzzle is up

    @property
    def phrase(self):
        """What must be typed now (a phrase puzzle), else None."""
        return self.run.puzzle.view.get("phrase") if self.run else None

    @property
    def words(self):
        """The words to type one at a time now (a phrase puzzle in grid mode), else None."""
        return self.run.puzzle.view.get("grid_words") if self.run else None


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
        self.secrets = challenge_secrets.Checker(db_provider)

    def _now(self, db):
        if self._clock:
            return self._clock(db)
        from trusted_time import now_from_db
        return now_from_db(db)

    # ---------------- asking

    def request(self, changes: list[str], action, cancel=None, force: bool = False, dry: bool = False, steps=None,
                phrase_opts=None, plain: bool = False, title: str | None = None, note: str | None = None,
                uninstall: bool = False) -> Envelope:
        """Run `action` now if Anti-Bypass allows it (status "free"; with force - a locked mode - only while a passed
        challenge is still unlocked), else keep it under a ticket for the challenge. `cancel` undoes whatever the
        page shows if the challenge is cancelled or never passed. `dry`: always the challenge, and passing it only runs
        `action` - no unlock is opened and no wait is started (nothing is allowed by it).

        The bedtime variant (`plain=True`, with `steps`, and `phrase_opts` = {"complex", "grid"}; there is no own phrase):
        it ALWAYS asks, with exactly these steps - Anti-Bypass being free, closed (hours) or waiting changes nothing; a
        pass opens no unlock and starts no wait, it only runs `action`; `title` / `note` are what the dialog says
        (begin's data) and the App log says "Bedtime challenge ..." instead of the Anti-Bypass lines.

        `uninstall` (the uninstaller's `--challenge`): the list without the password / authenticator steps; passing it
        runs `action` and opens no unlock for other changes - with a wait set, the uninstaller's own wait starts, and
        after it uninstalling is free for a while (antibypass.uninstall_open)."""
        db = self._db()
        cfg, now = antibypass.settings(db), self._now(db)
        variant = None
        if plain:
            rows = challenges.read_steps(steps)
            if not rows:
                raise ValueError("A bedtime challenge needs at least one step.")
            opts = phrase_opts or {}
            variant = {"steps": rows, "complex": bool(opts.get("complex")), "grid": bool(opts.get("grid")),
                       "title": title, "note": note}
        elif not dry and antibypass.status(cfg, now) == "free" and not (force and not antibypass.unlocked_until(cfg, now)):
            return ok(action())
        elif uninstall and antibypass.status(cfg, now) == "phrase" and antibypass.uninstall_open(db, now):
            antibypass.uninstall_used(db)               # (its own wait ran out: one uninstall)
            return ok(action())
        changes = [str(c) for c in changes]
        pending = _Pending(changes, action, cancel, force, self._mono(), dry, variant, uninstall=uninstall and not plain)
        ticket = secrets.token_urlsafe(18)
        with self._lock:
            previous, self._ticket, self._open = self._open, ticket, pending
        self._run_cancel(previous)          # one challenge at a time: a new request cancels the one still open
        out = {"needs_challenge": True, "ticket": ticket, "changes": changes[:SHOWN_CHANGES],
               "more": max(0, len(changes) - SHOWN_CHANGES)}
        if variant:
            out.update(self._plain_text(variant))
        return ok(out)

    @staticmethod
    def _plain_text(variant: dict) -> dict:
        """What the bedtime variant tells the dialog: plain (never mention unlocking), and its title / note if any."""
        out = {"plain": True}
        out.update({k: variant[k] for k in ("title", "note") if variant.get(k)})
        return out

    def _run_cancel(self, pending):
        if pending is not None:
            pending.run = None          # (the run goes with the ticket: a new one starts at step 1 with new puzzles)
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
        """What the challenge shows: {mode: phrase | grid | math | memory | sort | waiting | closed, changes, more, ...}.
        A fresh run of the step list is made and kept here (new puzzles every call). The first puzzle's fields are at
        the top as before (phrase | grid_words + length, expr + find_x, size + lit + ordered, items) and `run` has the
        whole view (challenges.Run.view: the steps strip, the step, how many are done)."""
        pending = self._live(ticket)
        if pending is None:
            return fail("This challenge has expired. Start the change again.")
        db = self._db()
        cfg, now = antibypass.settings(db), self._now(db)
        data = {"changes": pending.changes[:SHOWN_CHANGES], "more": max(0, len(pending.changes) - SHOWN_CHANGES),
                "unlock_min": antibypass.UNLOCK_MIN, "now": now.isoformat(timespec="seconds")}
        waiting, run = antibypass.waiting_until(cfg, now), None
        if not waiting and pending.uninstall:
            waiting = antibypass.uninstall_waiting(db, now)
        if pending.plain:           # the bedtime screen's own list: no Anti-Bypass wait / hours, and nothing is unlocked
            plain = pending.plain
            run = challenges.Run(plain["steps"], grid=plain["grid"], complex=plain["complex"], rng=self._rng,
                                 clock=lambda: self._mono())
            mode = self._mode(run)
            data.update(self._shown(run))
            data.update(self._plain_text(plain))
        elif waiting:
            mode = "waiting"        # passed already: never show the challenge again (doing it again restarts the wait)
            data["waiting_until"] = waiting.isoformat(timespec="seconds")
        elif antibypass.status(cfg, now) == "closed":
            mode = "closed"
            nxt = antibypass.next_hours(cfg, now)
            data["next_open"] = nxt.isoformat(timespec="seconds") if nxt else None
            data["hours_text"] = ", ".join(f"{days_text(w['days'])} {w['start']}-{w['end']}" for w in cfg["windows"])
        else:
            steps = (antibypass.uninstall_steps(cfg) if pending.uninstall
                     else antibypass.asked_steps(cfg, self.secrets.ready()))
            run = challenges.Run(steps, grid=bool(cfg["grid"]), complex=bool(cfg.get("complex")),
                                 own=antibypass.own_phrase(cfg), rng=self._rng, clock=lambda: self._mono(),
                                 kinds=challenges.AB_KINDS, secrets=self.secrets, recovery=not pending.dry)
            mode = self._mode(run)
            data.update(self._shown(run))
        data["mode"] = mode
        with self._lock:
            if self._open is not pending:        # replaced while we were working
                return fail("This challenge has expired. Start the change again.")
            pending.mode = mode
            pending.run = run
        return ok(data)

    def _mode(self, run) -> str:
        """How the page shows the puzzle that is up: grid | phrase | math | memory | sort | password | totp."""
        kind = run.puzzle.kind
        return ("grid" if run.grid else "phrase") if kind == "phrase" else kind

    def _shown(self, run) -> dict:
        """The puzzle that is up, for the page: its own fields at the top and the whole run view under `run`."""
        view = run.view()
        out = {k: view[k] for k in run.puzzle.view}
        if run.puzzle.kind == "phrase":
            out["length"] = len(out.get("phrase") or " ".join(out["grid_words"]))
        out["mode"] = self._mode(run)
        out["run"] = view
        return out

    def submit(self, ticket, typed) -> Envelope:
        """The answer to the puzzle that is up: the phrase as typed (a string; in grid mode the words in order, a list),
        a number typed for a sum, the cells tapped in order (memory), the items tapped in order (sorting), the password
        or the 6-digit code typed, or {"recovery": code} instead of any answer (passes the whole run while a code
        exists - not in a practice run; the reply then carries the NEW code, `recovery_code`, shown once). Python
        judges it. A password / code that is
        not right is refused (the same step stays; after a few wrong tries in a row each next one has to wait). A
        phrase that doesn't match / came too fast is refused (fix it and send again). Any other puzzle that is
        wrong or too quick gives {wrong: true, next: <view>, message} (a new puzzle of that step); a right one with more
        to do gives {passed: false, next: <view>}. The last one right unlocks and runs the stored action - or, if a wait
        is set, cancels it and says when."""
        pending = self._live(ticket)
        if pending is None:
            return fail("This challenge has expired. Start the change again.")
        db = self._db()
        cfg, now = antibypass.settings(db), self._now(db)
        if pending.mode not in MODES or pending.run is None:
            return fail("There is nothing to type right now.")
        if not pending.plain and (antibypass.waiting_until(cfg, now) or antibypass.status(cfg, now) == "closed"
                                  or (pending.uninstall and antibypass.uninstall_waiting(db, now))):
            return fail("Changes like this aren't possible right now.")
        run = pending.run
        with self._lock:
            if self._open is not pending:
                return fail("This challenge has expired. Start the change again.")
            was = run.step
            result = run.answer(typed)
            if result == challenges.PASSED:
                self._ticket = self._open = None    # one use: whatever happens next, this ticket is gone
            else:
                pending.mode = self._mode(run)
            shown = None if result == challenges.PASSED else self._shown(run)
        if result == challenges.MISMATCH:
            applog.record("challenge_failed", self._failed(pending, "what was typed didn't match"), db)
            return fail("That doesn't match - try again.")
        if result == challenges.TOO_FAST:
            applog.record("challenge_failed", self._failed(pending, "typed too fast"), db)
            return fail("Too fast - type it yourself.")
        if result == challenges.WRONG:
            how = "answered too fast" if run.last_too_fast else "wrong answer"
            applog.record("challenge_failed", self._failed(pending, f"{how} in {challenges.step_label(was)}"), db)
            return ok({"wrong": True, "next": shown,
                       "message": "Too fast - take a proper look. Here is a new one." if run.last_too_fast
                       else "Not right - here is a new one."})
        if result == challenges.BUSY:
            wait = max(1, int(-(-self.secrets.wait_left() // 1)))
            return fail(f"Too many wrong tries - wait {wait} s, then try again.")
        if result == challenges.REFUSED:
            what = run.last_refused
            applog.record("challenge_failed", self._failed(pending, REFUSED_LOG.get(what, "wrong answer")), db)
            return fail(REFUSED_TEXT.get(what, "Not right - try again."))
        if result == challenges.RIGHT:
            return ok({"passed": False, "next": shown})
        new_code = {}
        if run.new_code:
            applog.record("antibypass_change", f"Anti-Bypass: recovery code used to pass the challenge "
                                               f"({self._for(pending)}) - it is used up", db)
            applog.record("antibypass_change", "Anti-Bypass: a new recovery code was made and shown once (the old one "
                                               "no longer works)", db)
            new_code = {"recovery_code": run.new_code}
        if pending.plain:           # the bedtime Dismiss: no unlock, no wait - just the action, once
            applog.record("challenge_passed", "Bedtime challenge passed (before Dismiss)", db)
            return ok({"passed": True, "result": pending.action()})
        if pending.dry:
            applog.record("challenge_passed", "Anti-Bypass challenge passed (a practice run, nothing was changed)", db)
            return ok({"passed": True, "dry": True, "result": pending.action()})
        waiting = antibypass.uninstall_pass(db, now) if pending.uninstall else antibypass.unlock(db, now)
        if waiting:
            applog.record("challenge_passed", f"Anti-Bypass challenge passed ({self._for(pending)}); the wait runs "
                                              f"until {waiting:%H:%M}, nothing was changed yet", db)
            self._run_cancel(pending)               # a cooling-off period: nothing is done now, come back later
            return ok({"passed": True, "waiting_until": waiting.isoformat(timespec="seconds"), **new_code})
        applog.record("challenge_passed", f"Anti-Bypass challenge passed ({self._for(pending)})"
                                          + (" - only this, nothing else unlocked" if pending.uninstall else ""), db)
        return ok({"passed": True, "result": pending.action(), **new_code})

    def _failed(self, pending, how: str) -> str:
        """The App log line for a failed try: "Anti-Bypass challenge failed: <how> (for: ...)"; the bedtime Dismiss's
        own list says "Bedtime challenge failed: <how>"."""
        if pending.plain:
            return f"Bedtime challenge failed: {how}"
        return f"Anti-Bypass challenge failed: {how} ({self._for(pending)})"

    @staticmethod
    def _for(pending) -> str:
        """What the challenge was for, for the App log: the first few lines it showed."""
        shown = ", ".join(pending.changes[:3]) + (f" and {len(pending.changes) - 3} more" if len(pending.changes) > 3 else "")
        return f"for: {shown}" if shown else "no change named"

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
    """The answer to the puzzle that is up (the phrase typed, the grid words in order, a number, the cells or items
    tapped in order). Python decides; after the last one the stored change is made."""
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
            "grid": cfg["grid"], "complex": cfg.get("complex", False), "custom_phrase": bool(antibypass.own_phrase(cfg)),
            "length": cfg["length"], "hours": cfg["hours"], "windows": cfg["windows"], "wait_min": cfg["wait_min"],
            "unlock_min": antibypass.UNLOCK_MIN, "unlocked_until": until, "waiting_until": waiting,
            "next_open": nxt, "off": antibypass.is_off(db), "now": now}
