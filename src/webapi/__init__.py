"""The Api the web page talks to (pywebview's `js_api`): see core.py (registry, envelope) and guard.py (challenge).

Adding a page: write `webapi/<page>.py` with `@read` / `@tighten` / `@loosen` / `@ui` methods (`def name(ctx, ...)`)
and append its module name to PAGE_MODULES below. Importing the package loads them all and refuses to start if a
public function in any of them is unclassified.
"""
import importlib

from . import core
from .core import api_methods, build_api

PAGE_MODULES = [
    "guard",
    "shell",
    "reminders",
    "corner",
    "shared",
    "rules",
    "about",
    "categories",
    "antibypass_page",
    "draft",
    "blocking",
    "dashboard",
    "calendar",
    "modes",
    "network",
    "overlay",
    "protection",
    "groups",
    "screentime",
]

for _name in PAGE_MODULES:
    core.registry.check_module(importlib.import_module(f".{_name}", __name__))


def make_context(db_path=None, clock=None) -> core.Context:
    """One Context for the agent (or a test): thread-local databases at `db_path` (None = the real one), the
    agent-wide GuardService. `clock(db)` replaces the trusted time (tests)."""
    from .guard import GuardService
    ctx = core.Context(db_path, clock)
    ctx.guard = GuardService(lambda: ctx.db, clock)
    return ctx
