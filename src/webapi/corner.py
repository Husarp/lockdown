"""Api for the corner window (BLUEPRINT 3.8): the reminder popups and in-app notices.

The page only asks what is on screen and says what was pressed. Python decides: the card must really be up, the button
must be one of its buttons, and waving away an Important reminder goes through the GuardService (challenge) first -
the page never says "confirmed". `ctx.corner` is the agent's resident.corner.Corner."""
from . import core
from .core import ApiError
from .shell import get_theme

MAX_HEIGHT = 4000       # css px: more than any screen
MAX_KEY = 80


def _corner(ctx):
    corner = getattr(ctx, "corner", None)
    if corner is None:
        raise ApiError("There is no notice window here.")
    return corner


@core.ui
def corner_ready(ctx):
    """The page has loaded: the look and the cards that are up now (a push before this may have been missed)."""
    return {"look": get_theme(ctx), **_corner(ctx).ready()}


@core.ui
def corner_size(ctx, height, dpr=1):
    """How tall the stack of cards is (css px) and the page's pixel ratio: the window is resized to it."""
    ok = (isinstance(height, (int, float)) and not isinstance(height, bool) and 0 <= height <= MAX_HEIGHT
          and isinstance(dpr, (int, float)) and not isinstance(dpr, bool) and 0.5 <= dpr <= 5)
    if not ok:
        raise ApiError("Bad request: a size is expected.")
    _corner(ctx).page_size(float(height), float(dpr))


@core.loosen
def corner_answer(ctx, key, action):
    """A button on a card (or its ✕ / the notice closing). The card is taken down first, then the answer is acted on.
    Dismissing an Important reminder returns {needs_challenge, ticket} (the challenge opens in the main window) and
    answers only once Python has checked what was typed."""
    if not (isinstance(key, str) and isinstance(action, str) and 0 < len(key) <= MAX_KEY and 0 < len(action) <= MAX_KEY):
        raise ApiError("Bad request: text expected.")
    try:
        reply = _corner(ctx).answer(key, action, ctx.guard)
    except ValueError as error:
        raise ApiError(str(error))
    return reply if reply is not None else {"done": True}
