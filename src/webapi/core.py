"""The bridge between the web page and Python: a registry of Api methods and the one place that runs them.

The page is untrusted (a webview can be inspected and edited), so everything it may call is listed here, each method
classified by what it can do to the owner's blocks:
- read:    changes nothing.
- tighten: only makes blocks stricter or adds protection (free).
- loosen:  may weaken something. It recomputes old vs new IN PYTHON from the database and validated input and
           goes through ctx.guard.request(...) (Anti-Bypass challenge) - never trusts a flag from the page.
- ui:      cosmetic / ungated writes (sorting, which dashboard cards show ...).

A method is `def name(ctx, *args)`: ctx is the Context (ctx.db = this thread's own Database, ctx.guard, ctx.now()).
It returns plain data and raises ApiError("plain text") to refuse. Every call comes back as {ok, data} or
{ok: false, error}. A method that returns an Envelope (what ctx.guard methods return) is passed on as it is.
"""
import inspect
import math
import re
import threading
import time
import types
from datetime import date, datetime, time as dtime
from fnmatch import fnmatchcase

KINDS = ("read", "tighten", "loosen", "ui")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_DEPTH = 32                 # deepest nesting of an argument from the page
LOG_REPEAT_SEC = 60            # the same error in a row is only written to the log once per this long


class ApiError(Exception):
    """A refusal the page may show as it is (bad input, not allowed, expired ...). Not logged as a bug."""


class RegistrationError(Exception):
    """A coding mistake in the Api (unclassified public function, duplicate or bad name) - fails at start-up."""


class Envelope(dict):
    """An already-built reply {ok, data | error} (GuardService returns these): the dispatcher doesn't wrap it again."""


def ok(data=None) -> Envelope:
    return Envelope(ok=True, data=data)


def fail(error: str) -> Envelope:
    return Envelope(ok=False, error=str(error))


# ---------------------------------------------------------------- what the page may send and get back

def check_json(value, depth: int = 0):
    """Raise ApiError unless `value` is plain JSON: None, bool, int, float (finite - NaN would slip through every
    `new > old` loosening check), str, list, dict with str keys."""
    if depth > MAX_DEPTH:
        raise ApiError("Bad request: nested too deep.")
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ApiError("Bad request: not a number.")
        return
    if isinstance(value, (list, tuple)):
        for v in value:
            check_json(v, depth + 1)
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ApiError("Bad request: keys must be text.")
            check_json(v, depth + 1)
        return
    raise ApiError("Bad request: only plain values are accepted.")


def jsonable(value, depth: int = 0):
    """Turn a method's result into plain JSON: datetimes -> ISO text, tuples / sets -> lists, rows -> dicts."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if depth > MAX_DEPTH * 2:
        raise TypeError("result nested too deep")
    if isinstance(value, (datetime, date, dtime)):
        return value.isoformat()
    if isinstance(value, dict) or hasattr(value, "keys"):    # (sqlite3.Row too)
        out = {str(k): jsonable(value[k], depth + 1) for k in value.keys()}
        return Envelope(out) if isinstance(value, Envelope) else out
    if isinstance(value, (set, frozenset)):
        try:
            value = sorted(value)
        except TypeError:
            value = list(value)
    if isinstance(value, (list, tuple)):
        return [jsonable(v, depth + 1) for v in value]
    if hasattr(value, "__fspath__"):
        return str(value)
    raise TypeError(f"can't send a {type(value).__name__} to the page")


# ---------------------------------------------------------------- the error log (same file as the service)

_log_lock = threading.Lock()
_last_error = ["", 0.0]        # [last line written, when]


def log_error(text: str):
    """Append an error to the Lockdown log. The same error in a row is only written once a minute, so a call
    that fails on every frame can't fill the disk (as in gui/app.report_callback_exception)."""
    import paths
    text = text.rstrip()
    first = text.splitlines()[-1] if text else ""
    now = time.monotonic()
    with _log_lock:
        if first == _last_error[0] and now - _last_error[1] < LOG_REPEAT_SEC:
            return
        _last_error[0], _last_error[1] = first, now
        try:
            with open(paths.LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},000 ERROR Lockdown window: {text}\n")
        except OSError:
            pass


# ---------------------------------------------------------------- the context every method gets

class Context:
    """What an Api method works with. ctx.db is THIS thread's own Database (sqlite connections aren't shared between
    threads: one thread's transaction or write retry would interleave with another's); it opens on first use with
    the short UI busy timeout, so a lock held by the service never freezes a call for 10 s. Inside a pending action
    (guard) use ctx.db again rather than a Database captured earlier - the action may run on another thread."""

    def __init__(self, db_path=None, clock=None):
        self._path = db_path
        self._clock = clock         # clock(db) -> trusted "now" (tests stub it); default trusted_time.now_from_db
        self._local = threading.local()
        self.guard = None           # webapi.make_context sets the agent's GuardService

    @property
    def db(self):
        db = getattr(self._local, "db", None)
        if db is None:
            from db import Database
            db = self._local.db = Database(self._path, ui=True) if self._path else Database(ui=True)
        return db

    def now(self) -> datetime:
        db = self.db
        if self._clock:
            return self._clock(db)
        from trusted_time import now_from_db
        return now_from_db(db)


# ---------------------------------------------------------------- the registry

class Registry:
    """name -> (function, kind). The module-level `registry` is the real one; tests make their own."""

    def __init__(self):
        self._methods: dict[str, tuple] = {}

    def _register(self, kind: str, fn, name=None):
        name = name or fn.__name__
        if not NAME_RE.match(name):
            raise RegistrationError(f"Api method name {name!r}: lower-case letters, digits and _ only, no leading _")
        if name in self._methods:
            raise RegistrationError(f"Api method {name!r} is registered twice")
        params = list(inspect.signature(fn).parameters)
        if not params or params[0] != "ctx":
            raise RegistrationError(f"Api method {name!r}: the first argument must be ctx")
        self._methods[name] = (fn, kind)
        return fn

    def _decorator(self, kind: str):
        def decorate(fn=None, *, name=None):
            if fn is None:                       # @read(name="...")
                return lambda f: self._register(kind, f, name)
            return self._register(kind, fn, name)
        decorate.__name__ = kind
        decorate.__doc__ = f"Register an Api method that is classified {kind!r}."
        return decorate

    def methods(self) -> list[tuple[str, str]]:
        """[(name, kind)] sorted by name - for the tests and the anti-bypass audit (R28)."""
        return sorted((name, kind) for name, (_fn, kind) in self._methods.items())

    def get(self, name: str):
        return self._methods.get(name)

    def has_function(self, fn) -> bool:
        return any(f is fn for f, _kind in self._methods.values())

    def check_module(self, module):
        """Every public (no leading _) function defined in `module` must be classified (@read / @tighten / @loosen /
        @ui) or marked @internal - an unclassified function would be one nobody audited."""
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("_") or fn.__module__ != module.__name__:
                continue
            if not self.has_function(fn) and not getattr(fn, "_api_internal", False):
                raise RegistrationError(f"{module.__name__}.{name} is public but not classified "
                                        "(@read / @tighten / @loosen / @ui, or @internal, or rename it with a _)")


registry = Registry()
read, tighten, loosen, ui = (registry._decorator(k) for k in KINDS)


def internal(fn):
    """Mark a public helper in a webapi module that is NOT an Api method (so check_module lets it be)."""
    fn._api_internal = True
    return fn


def api_methods() -> list[tuple[str, str]]:
    return registry.methods()


# ---------------------------------------------------------------- running a call

def dispatch(ctx: Context, fn, args) -> dict:
    """Run one call from the page: validate the arguments, run the method, wrap the outcome."""
    try:
        check_json(args)
        data = fn(ctx, *args)
        if isinstance(data, Envelope):
            return jsonable(data)
        return {"ok": True, "data": jsonable(data)}
    except ApiError as e:
        return {"ok": False, "error": str(e) or "Not allowed."}
    except Exception as e:
        import traceback
        log_error(f"Api call {getattr(fn, '__name__', '?')} failed: {traceback.format_exc()}")
        return {"ok": False, "error": str(e) or type(e).__name__}


def _trampoline(self, *args):
    return _call(*args)             # (_call and the builtins come from the private globals given to each copy)


def _exposed_function(name: str, call):
    """The function the page can reach for one Api method. pywebview lets the page walk `getattr` chains from the
    exposed object (`a.b.__globals__.update` ...), so this function gets PRIVATE globals and builtins - it holds
    nothing but the one call, and nothing of this module can be reached through it."""
    fn = types.FunctionType(_trampoline.__code__, {"__builtins__": {}, "_call": call}, name)
    fn.__qualname__ = f"Api.{name}"
    return fn


def build_api(ctx: Context, reg: Registry = None):
    """The object pywebview gets as js_api: one method per registered name and NOTHING else - `dir()` lists only
    those, and any other attribute (db, window, _private, __class__ ...) raises AttributeError, because pywebview
    resolves the function name sent by the page with getattr on this object."""
    reg = reg or registry
    methods = {}
    for name, (fn, _kind) in reg._methods.items():
        methods[name] = _exposed_function(name, (lambda fn: lambda *args: dispatch(ctx, fn, args))(fn))
    names = frozenset(methods)

    def __getattribute__(self, attr):
        if attr in names:
            return object.__getattribute__(self, attr)
        raise AttributeError(attr)

    def __dir__(self):
        return sorted(names)

    cls = type("Api", (), {**methods, "__slots__": (), "__getattribute__": __getattribute__, "__dir__": __dir__})
    return cls()


# ---------------------------------------------------------------- ungated settings (the only way the page writes one)

UI_SETTING_PATTERNS = ["dash.*", "ui.blocking.sort"]     # fnmatch patterns; pages add theirs with allow_ui_settings
MAX_SETTING_KEY, MAX_SETTING_VALUE = 100, 4000
_KEY_RE = re.compile(r"^[a-z0-9_.]+$")


def allow_ui_settings(*patterns: str):
    """A page module lists the cosmetic settings keys it may write freely (e.g. "screentime.tab"). Never anything
    that can loosen a block - those go through the guard."""
    for p in patterns:
        if not _KEY_RE.match(p.replace("*", "x")):
            raise RegistrationError(f"bad ui setting pattern {p!r}")
        if p not in UI_SETTING_PATTERNS:
            UI_SETTING_PATTERNS.append(p)


def ui_setting_allowed(key) -> bool:
    return (isinstance(key, str) and 0 < len(key) <= MAX_SETTING_KEY and bool(_KEY_RE.match(key))
            and any(fnmatchcase(key, p) for p in UI_SETTING_PATTERNS))


@ui
def ui_setting(ctx, key, value):
    """Save a cosmetic setting. Only allow-listed keys; the value is text (anything else is stored as JSON text)."""
    if not ui_setting_allowed(key):
        raise ApiError("That setting can't be changed from here.")
    import json
    text = value if isinstance(value, str) else json.dumps(value)
    if len(text) > MAX_SETTING_VALUE:
        raise ApiError("That value is too long.")
    ctx.db.set_setting(key, text)
