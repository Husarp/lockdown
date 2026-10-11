"""R02: the Api registry - only registered names reachable, uniform envelope, per-thread databases, settings allowlist."""
import inspect
import json
import sqlite3
import threading
import types
from datetime import date, datetime
from pathlib import Path

import pytest

import paths
import webapi
from webapi import core

NOW = datetime(2026, 9, 20, 19, 0)


@pytest.fixture
def ctx(tmp_path):
    return webapi.make_context(tmp_path / "t.db", clock=lambda db: NOW)


@pytest.fixture
def log(tmp_path, monkeypatch):
    path = tmp_path / "lockdown.log"
    monkeypatch.setattr(paths, "LOG_PATH", path)
    monkeypatch.setattr(core, "_last_error", ["", 0.0])
    return path


def fresh():
    """A private registry with a few methods (the real one stays untouched)."""
    reg = core.Registry()

    @reg._decorator("read")
    def echo(ctx, x=None):
        return x

    @reg._decorator("read")
    def boom(ctx):
        raise RuntimeError("it broke")

    @reg._decorator("read")
    def refuse(ctx):
        raise core.ApiError("Not allowed here.")

    return reg, echo, boom, refuse


# ---------------------------------------------------------------- what the page can reach

def pywebview_functions(obj):
    """What pywebview 6 exposes (webview/util.py get_functions, copied): dir() names without a leading _, methods and
    functions as they are, other objects searched for more functions."""
    found, seen = {}, set()

    def walk(o, base=""):
        if id(o) in seen:
            return
        seen.add(id(o))
        for name in dir(o):
            if name.startswith("_"):
                continue
            attr = getattr(o, name)
            full = f"{base}.{name}" if base else name
            if inspect.ismethod(attr) or inspect.isfunction(attr):
                found[full] = attr
            elif inspect.isclass(attr) or (not callable(attr) and hasattr(attr, "__module__")):
                walk(attr, full)
    walk(obj)
    return found


def pywebview_lookup(obj, path):
    """webview/util.py get_nested_attribute: the page names a function; this resolves it with getattr, dots and all."""
    for part in path.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def test_only_registered_names_are_reachable(ctx):
    api = core.build_api(ctx)
    names = {n for n, _kind in core.api_methods()}
    assert names and set(dir(api)) == names
    assert set(pywebview_functions(api)) == names
    for hidden in ("db", "_db", "_x", "window", "_ctx", "ctx", "guard", "__class__", "__dict__", "__init__",
                   "__reduce__", "__getattribute__"):
        assert pywebview_lookup(api, hidden) is None, hidden
        assert not hasattr(api, hidden)


def test_page_cannot_walk_to_module_internals(ctx):
    """The page names the function to call and pywebview resolves it with getattr - so a name like
    `challenge_begin.__globals__.update` must lead nowhere useful: the exposed function has private, empty globals."""
    api = core.build_api(ctx)
    fn = api.challenge_begin
    assert set(fn.__globals__) == {"__builtins__", "_call"}
    assert fn.__builtins__ == {} and fn.__closure__ is None and not hasattr(fn, "__wrapped__")
    assert pywebview_lookup(api, "challenge_begin.__globals__") is not core.__dict__
    assert fn.__module__ is None
    for path in ("__class__.__init__.__globals__", "__init__.__globals__.update", "__dir__.__globals__",
                 "challenge_begin.__self__.__class__", "challenge_begin.__func__.__globals__"):
        got = pywebview_lookup(api, path)
        assert got is None or got is not core.__dict__


def test_poisoning_the_exposed_function_cannot_reach_the_module(ctx):
    api = core.build_api(ctx)
    pywebview_lookup(api, "challenge_begin.__globals__.update")({"MIN_MS": 0, "ApiError": 1})
    assert not hasattr(core, "MIN_MS") and issubclass(core.ApiError, Exception)
    assert api.antibypass_status()["ok"]           # (a different method is untouched)


def test_api_methods_lists_name_and_kind():
    methods = dict(core.api_methods())
    assert methods["challenge_begin"] == methods["challenge_submit"] == methods["challenge_cancel"] == "ui"
    assert methods["antibypass_status"] == "read" and methods["ui_setting"] == "ui"
    assert set(methods.values()) <= set(core.KINDS)


def test_every_real_module_is_checked():
    for name in webapi.PAGE_MODULES:
        core.registry.check_module(__import__(f"webapi.{name}", fromlist=["x"]))


# ---------------------------------------------------------------- registration mistakes

def test_unclassified_public_function_is_a_registration_error():
    reg = core.Registry()
    module = types.ModuleType("webapi_fake")

    def classified(ctx):
        return 1

    def forgotten(ctx):
        return 2

    def _helper(x):
        return x

    @core.internal
    def tool(x):
        return x
    for fn in (classified, forgotten, _helper, tool):
        fn.__module__ = module.__name__
        setattr(module, fn.__name__, fn)
    reg._decorator("read")(classified)
    with pytest.raises(core.RegistrationError, match="forgotten"):
        reg.check_module(module)
    reg._decorator("tighten")(forgotten)
    reg.check_module(module)                       # classified, a _helper and an @internal helper: fine


def test_registration_rules():
    reg = core.Registry()
    def no_ctx(x):
        return x

    def with_ctx(ctx):
        return 1
    with pytest.raises(core.RegistrationError, match="ctx"):
        reg._decorator("read")(no_ctx)
    with pytest.raises(core.RegistrationError, match="name"):
        reg._decorator("read")(with_ctx, name="_private")
    with pytest.raises(core.RegistrationError, match="name"):
        reg._decorator("read")(with_ctx, name="Camel")

    @reg._decorator("read")
    def one(ctx):
        return 1
    with pytest.raises(core.RegistrationError, match="twice"):
        reg._decorator("loosen")(one)
    assert reg.methods() == [("one", "read")]


def test_decorators_return_the_function_and_take_a_name():
    reg = core.Registry()

    @reg._decorator("loosen")(name="renamed")
    def original(ctx):
        return 5
    assert original(None) == 5 and reg.methods() == [("renamed", "loosen")]


# ---------------------------------------------------------------- the envelope

def test_success_wraps_data_and_dates_become_text(ctx):
    reg, echo, *_ = fresh()
    api = core.build_api(ctx, reg)
    assert api.echo() == {"ok": True, "data": None}
    got = core.dispatch(ctx, lambda c: {"when": NOW, "day": date(2026, 9, 20), "t": (1, 2), "s": {3, 1, 2},
                                        "p": Path("/x"), "inf": float("inf")}, [])
    assert got == {"ok": True, "data": {"when": "2026-09-20T19:00:00", "day": "2026-09-20", "t": [1, 2],
                                        "s": [1, 2, 3], "p": str(Path("/x")), "inf": None}}
    json.dumps(got)


def test_rows_become_dicts(ctx):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT 1 AS a, 'x' AS b").fetchone()
    assert core.dispatch(ctx, lambda c: [row], []) == {"ok": True, "data": [{"a": 1, "b": "x"}]}


def test_an_unsendable_result_is_an_error_not_a_crash(ctx, log):
    got = core.dispatch(ctx, lambda c: object(), [])
    assert got["ok"] is False and "object" in got["error"] and log.exists()


def test_exception_gives_ok_false_and_is_logged(ctx, log):
    reg, echo, boom, refuse = fresh()
    api = core.build_api(ctx, reg)
    got = api.boom()
    assert got == {"ok": False, "error": "it broke"}
    text = log.read_text(encoding="utf-8")
    assert "RuntimeError: it broke" in text and "boom" in text


def test_the_same_error_in_a_row_is_logged_once(ctx, log):
    reg, echo, boom, refuse = fresh()
    api = core.build_api(ctx, reg)
    for _ in range(5):
        assert api.boom()["ok"] is False
    assert log.read_text(encoding="utf-8").count("RuntimeError: it broke") == 1


def test_a_plain_refusal_is_not_a_bug(ctx, log):
    reg, echo, boom, refuse = fresh()
    api = core.build_api(ctx, reg)
    assert api.refuse() == {"ok": False, "error": "Not allowed here."}
    assert not log.exists()


def test_an_unwritable_log_never_breaks_a_call(ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "no" / "such" / "dir" / "x.log")
    monkeypatch.setattr(core, "_last_error", ["", 0.0])
    reg, echo, boom, refuse = fresh()
    assert core.build_api(ctx, reg).boom() == {"ok": False, "error": "it broke"}


# ---------------------------------------------------------------- what the page may send

@pytest.mark.parametrize("bad", [object(), {1, 2}, float("nan"), float("inf"), {1: "x"}, b"bytes", NOW,
                                 [{"a": object()}], {"a": [float("-inf")]}])
def test_only_plain_json_arguments_run(ctx, bad):
    ran = []
    reg = core.Registry()

    @reg._decorator("read")
    def target(ctx, x):
        ran.append(x)
        return 1
    got = core.build_api(ctx, reg).target(bad)
    assert got["ok"] is False and not ran


def test_deeply_nested_arguments_are_refused(ctx):
    deep = []
    for _ in range(core.MAX_DEPTH + 5):
        deep = [deep]
    reg, echo, *_ = fresh()
    assert core.build_api(ctx, reg).echo(deep)["ok"] is False
    assert core.build_api(ctx, reg).echo([[1, {"a": [True, None, 2.5, "s"]}]]) == {
        "ok": True, "data": [[1, {"a": [True, None, 2.5, "s"]}]]}


def test_wrong_number_of_arguments_is_an_error_not_a_crash(ctx, log):
    reg, echo, *_ = fresh()
    assert core.build_api(ctx, reg).echo(1, 2, 3)["ok"] is False


def test_wrong_arguments_give_a_plain_error_not_python_text(ctx, log):
    reg, echo, *_ = fresh()
    reply = core.build_api(ctx, reg).echo(1, 2, 3)
    assert reply == {"ok": False, "error": "Bad arguments for echo"}
    assert "positional" in log.read_text(encoding="utf-8")


# ---------------------------------------------------------------- the database per thread

def test_each_thread_has_its_own_database(ctx):
    here = ctx.db
    assert ctx.db is here
    got, ready, done = {}, threading.Barrier(2), threading.Event()

    def work(name):
        got[name] = ctx.db
        ready.wait()               # both alive at once: no object can be reused
        assert ctx.db is got[name]
        done.wait(5)

    threads = [threading.Thread(target=work, args=(n,)) for n in "ab"]
    for t in threads:
        t.start()
    while len(got) < 2:
        pass
    done.set()
    for t in threads:
        t.join()
    assert len({id(here), id(got["a"]), id(got["b"])}) == 3
    assert here.ui and got["a"].ui


def test_the_ui_connection_waits_only_briefly_for_a_lock(ctx):
    assert ctx.db.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1500


# ---------------------------------------------------------------- ungated settings

def test_ui_setting_allowlist(ctx):
    api = core.build_api(ctx)
    assert api.ui_setting("dash.hidden", ["a", "b"]) == {"ok": True, "data": None}
    assert json.loads(ctx.db.get_setting("dash.hidden", "")) == ["a", "b"]
    assert api.ui_setting("ui.blocking.sort", "name")["ok"]
    assert ctx.db.get_setting("ui.blocking.sort", "") == "name"
    for key in ("antibypass", "app.off", "ui.engine", "notify.enabled", "dash", "", "dash.x y", "DASH.x", None,
                ["dash.x"], "dash." + "x" * 200):
        got = api.ui_setting(key, "1")
        assert got["ok"] is False, key
    assert ctx.db.get_setting("antibypass", "") == ""
    assert api.ui_setting("dash.big", "x" * (core.MAX_SETTING_VALUE + 1))["ok"] is False


def test_pages_can_extend_the_allowlist(ctx, monkeypatch):
    monkeypatch.setattr(core, "UI_SETTING_PATTERNS", list(core.UI_SETTING_PATTERNS))
    api = core.build_api(ctx)
    assert api.ui_setting("screentime.tab", "apps")["ok"] is False
    core.allow_ui_settings("screentime.tab")
    assert api.ui_setting("screentime.tab", "apps")["ok"]
    assert api.ui_setting("screentime.range", "7")["ok"] is False
    with pytest.raises(core.RegistrationError):
        core.allow_ui_settings("Bad Pattern!")


def test_there_is_no_generic_setting_writer():
    names = {n for n, _ in core.api_methods()}
    assert not {"set_setting", "get_setting", "db", "execute"} & names
