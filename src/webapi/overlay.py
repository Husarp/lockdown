"""Api for the full-screen cover (BLUEPRINT 3.8): the bedtime screen and a running strict break.

The page only asks what is on screen and says which button was pressed. Python decides, with `ctx.overlay` (the agent's
resident.overlay.Overlay): the button must be on the screen, Dismiss waits for its countdown, and everything that turns
the bedtime alerts off needs the Anti-Bypass challenge when Anti-Bypass is on - `ctx.guard.request(...)`, the page never
says "confirmed". Disable alerts asks for the challenge FIRST (inside the overlay), then offers Off tonight / Snooze
15 min; both are asked of the guard again when they are pressed, so calling them without the challenge changes nothing.

Emergency is the sanctioned way out (no challenge, it costs one use of the kind picked): the kinds and the uses left
come from `emergency`, the page never sends minutes.

"Challenge before Dismiss" (reminders.sleep `challenge`): once the Dismiss wait is over, `dismiss` is not a dismissal -
the screen gives the list it fixed when it came up, the guard's PLAIN challenge asks exactly that (inside the cover,
always, whatever Anti-Bypass says) and only a passed one dismisses the screen, once. `cancel` never dismisses such a
screen."""
import emergency
from resident.overlay import SNOOZE_MIN

from . import core
from .core import ApiError
from .shell import get_theme

MAX_ID = 40
OFF_TEXT = "Turn off tonight's bedtime alerts"
SNOOZE_TEXT = f"Snooze tonight's bedtime alerts for {SNOOZE_MIN} min"
SNOOZE_ACTION = f"snooze:{SNOOZE_MIN}"
DISMISS_TITLE = "Solve this to dismiss"
DISMISS_NOTE = "Passing dismisses the bedtime screen once."
ACTIONS = ("dismiss", "cancel", "off_tonight", SNOOZE_ACTION)


def _overlay(ctx):
    overlay = getattr(ctx, "overlay", None)
    if overlay is None:
        raise ApiError("There is no cover window here.")
    return overlay


def _short_when(when) -> str:
    """"Mon 12 Oct 00:00" (when a kind's count starts again)."""
    return f"{when:%a} {when.day} {when:%b %H:%M}"


def _refuse(error: ValueError):
    raise ApiError(str(error)) from None


@core.ui
def overlay_ready(ctx):
    """The page has loaded: the look and the cover that is up now (a push before this may have been missed)."""
    return {"look": get_theme(ctx), **_overlay(ctx).payload()}


def _dismiss_challenge(ctx, overlay, ask: dict):
    """The bedtime challenge before Dismiss: the guard's plain variant with the list the screen fixed when it came up.
    Passing runs the Dismiss (once); cancelling it only drops the run - the screen stays."""
    token = ask["token"]

    def apply():
        try:
            return overlay.dismiss_passed(token)
        except ValueError as error:      # (the screen went away while the challenge was open)
            _refuse(error)
    return ctx.guard.request([], apply, cancel=lambda: overlay.challenge_cancelled(token), steps=ask["steps"],
                             phrase_opts={"complex": ask["complex"], "grid": ask["grid"]}, plain=True,
                             title=DISMISS_TITLE, note=DISMISS_NOTE)


@core.loosen
def overlay_answer(ctx, action):
    """A button on the cover. `dismiss` is ungated but Python refuses it while the wait runs ({early: true, wait_ms});
    with "Challenge before Dismiss" on it then asks that challenge first (below); `cancel` backs out of the choice /
    emergency list (the wait starts over if it still runs, else it is a Dismiss - but never when the screen has a bedtime
    challenge: it only goes back); `off_tonight` and `snooze:15` turn the bedtime alerts off for tonight / 15 min -
    behind the challenge whenever Anti-Bypass is not free.
    -> {done: true} | {early: true, wait_ms} | {back: true} | {needs_challenge, ticket}."""
    if not isinstance(action, str) or action not in ACTIONS:
        raise ApiError("Bad request: that is not a button.")
    overlay = _overlay(ctx)
    try:
        if action == "dismiss":
            reply = overlay.dismiss()
            return _dismiss_challenge(ctx, overlay, reply["challenge"]) if "challenge" in reply else reply
        if action == "cancel":
            return overlay.cancel()
        token = overlay.allows(action)
    except ValueError as error:
        _refuse(error)

    def apply():
        try:
            overlay.accept(action, token)
        except ValueError as error:      # (the screen went away while the challenge was open)
            _refuse(error)
        return {"done": True}
    return ctx.guard.request([OFF_TEXT if action == "off_tonight" else SNOOZE_TEXT], apply,
                             cancel=lambda: overlay.challenge_cancelled(token))


@core.loosen
def overlay_disable(ctx):
    """"Disable alerts": the challenge first (inside the overlay, when Anti-Bypass asks for one), then {choose: true} and
    the page offers Off tonight / Snooze 15 min / Cancel. Nothing is changed by this call itself."""
    overlay = _overlay(ctx)
    try:
        token = overlay.allows("off_tonight")
    except ValueError as error:
        _refuse(error)
    return ctx.guard.request([OFF_TEXT], lambda: {"choose": True},
                             cancel=lambda: overlay.challenge_cancelled(token))


@core.read
def overlay_emergency_kinds(ctx):
    """The kinds of emergency unlock, for the list on the cover: {usable, kinds: [{id, minutes, per_text, left, allowed,
    resets_text}]}. A kind with none left is listed (greyed out) with when it comes back."""
    db, now = ctx.db, ctx.now()
    if not emergency.usable(db):
        return {"usable": False, "kinds": []}
    return {"usable": True,
            "kinds": [{"id": k["id"], "minutes": k["minutes"], "per_text": emergency.PERIODS[k["per"]], "left": left,
                       "allowed": allowed, "resets_text": _short_when(reset)}
                      for k, left, allowed, reset in emergency.left_by_tier(db, now)]}


@core.loosen
def overlay_emergency(ctx, kind):
    """Spend ONE use of emergency kind `kind` and pause the bedtime and break alerts for its length (the engine does it:
    `reminders.Engine.answer`). Python checks first: the screen offers Emergency, the kind exists and has a use left;
    nothing is spent otherwise. This is not a challenge - the quota is the limit (like the Blocking page's unlock)."""
    if not isinstance(kind, str) or not 0 < len(kind) <= MAX_ID:
        raise ApiError("Pick how long.")
    overlay = _overlay(ctx)
    try:
        token = overlay.allows("emergency")
    except ValueError as error:
        _refuse(error)
    db, now = ctx.db, ctx.now()
    if not emergency.usable(db):
        raise ApiError("Emergency unlock is turned off (Settings).")
    found = next(((k, left) for k, left, _a, _r in emergency.left_by_tier(db, now) if k["id"] == kind), None)
    if found is None:
        raise ApiError("That kind is gone - pick another.")
    chosen, left = found
    if left <= 0:
        raise ApiError(f"No {chosen['minutes']}-min emergency unlocks left {emergency.PERIODS[chosen['per']]}.")
    try:
        overlay.accept(f"emergency:{kind}", token)
    except ValueError as error:
        _refuse(error)
    return {"done": True, "minutes": chosen["minutes"],
            "text": f"Bedtime and break alerts paused for {chosen['minutes']} min."}
