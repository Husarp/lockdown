"""R05: the shell's Api (webapi/shell.py) - status for the top line / sidebar / banners, the look settings, and the
free actions (start service, switch Lockdown back on, end a pause)."""
import time
from datetime import date, datetime, timedelta

import pytest

import antibypass as ab
import modes
import pause
import ui_theme
import webapi
from webapi import core, shell

NOW = datetime(2026, 9, 20, 19, 0)          # a Sunday evening


@pytest.fixture
def ctx(tmp_path):
    return webapi.make_context(tmp_path / "t.db", clock=lambda db: NOW)


@pytest.fixture
def api(ctx):
    return core.build_api(ctx)


@pytest.fixture
def log(tmp_path, monkeypatch):
    import paths
    path = tmp_path / "lockdown.log"
    monkeypatch.setattr(paths, "LOG_PATH", path)
    monkeypatch.setattr(core, "_last_error", ["", 0.0])
    return path


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


# ---------------------------------------------------------------- classification

def test_shell_methods_are_registered_with_the_right_kind():
    kinds = dict(webapi.api_methods())
    for name in ("get_status", "get_theme"):
        assert kinds[name] == "read"
    for name in ("set_appearance", "set_accent", "set_scale", "ui_ready", "log_client_error"):
        assert kinds[name] == "ui"
    # these only make blocking stricter, so they are free - and none of them is a "loosen"
    for name in ("service_start", "antibypass_switch_on", "pause_resume"):
        assert kinds[name] == "tighten"


def test_the_heartbeat_key_is_the_services():
    import service
    assert shell.HEARTBEAT_KEY == service.HEARTBEAT_KEY


# ---------------------------------------------------------------- get_status

STATUS_KEYS = {"service_running", "off_since", "mode", "pause", "clock_now", "draft", "antibypass", "tray_text"}


def test_get_status_has_the_blueprint_keys(api):
    st = data(api.get_status())
    assert set(st) == STATUS_KEYS
    assert set(st["draft"]) == {"dirty", "autosave", "change_summary"}
    assert set(st["antibypass"]) == {"status", "unlocked_until", "waiting_until", "next_open"}
    assert st["clock_now"] == "2026-09-20T19:00:00"
    assert st["mode"] is None and st["pause"] is None and st["off_since"] is None
    assert st["draft"] == {"dirty": False, "autosave": True, "change_summary": ""}
    assert st["antibypass"]["status"] == "free"
    assert st["tray_text"] == "0 sites/apps blocked"


def test_service_running_follows_the_heartbeat(ctx, api):
    assert data(api.get_status())["service_running"] is False
    ctx.db.set_setting(shell.HEARTBEAT_KEY, str(time.time()))
    assert data(api.get_status())["service_running"] is True
    ctx.db.set_setting(shell.HEARTBEAT_KEY, str(time.time() - 60))
    assert data(api.get_status())["service_running"] is False
    ctx.db.set_setting(shell.HEARTBEAT_KEY, "garbage")
    assert data(api.get_status())["service_running"] is False


def test_off_state_and_tray_text(ctx, api):
    ab.switch_off(ctx.db, NOW)
    st = data(api.get_status())
    assert st["off_since"] == "2026-09-20T19:00:00"
    assert st["tray_text"] == "OFF - nothing is enforced"


def test_active_mode_is_reported(ctx, api):
    modes.start(ctx.db, "work", NOW - timedelta(minutes=5), NOW + timedelta(hours=1), locked=True)
    st = data(api.get_status())
    assert st["mode"] == {"id": "work", "name": "Work", "phase": None, "ends_at": "2026-09-20T20:00:00",
                          "locked": True, "by_schedule": False}
    assert st["tray_text"].endswith(" · Work mode")


def test_mode_until_stopped_has_no_end(ctx, api):
    modes.start(ctx.db, "relax", NOW - timedelta(minutes=5), None)
    st = data(api.get_status())
    assert st["mode"]["ends_at"] is None and st["mode"]["locked"] is False


def test_pomodoro_phase_is_reported(ctx, api):
    modes.start(ctx.db, "focus", NOW - timedelta(minutes=26), None)      # 25 min focus, then a break
    assert data(api.get_status())["mode"]["phase"] == "break"


def test_pause_is_reported_and_changes_tray_text(ctx, api):
    end = pause.start(ctx.db, NOW, 30, silent=True)
    st = data(api.get_status())
    assert st["pause"] == {"started": "2026-09-20T19:00:00", "until": end.isoformat(timespec="seconds"),
                           "silent": True}
    assert st["tray_text"] == "Blocking paused until 19:30"


def test_antibypass_block_follows_the_settings(ctx, api):
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True})
    assert data(api.get_status())["antibypass"]["status"] == "phrase"
    ab.unlock(ctx.db, NOW)
    st = data(api.get_status())["antibypass"]
    assert st["status"] == "free" and st["unlocked_until"] == "2026-09-20T19:05:00"
    ab.save(ctx.db, {**ab.settings(ctx.db), "hours": True, "windows": [{"days": [0], "start": "09:00", "end": "10:00"}]})
    st = data(api.get_status())["antibypass"]
    assert st["status"] == "closed" and st["next_open"] == "2026-09-21T09:00:00"


def test_autosave_setting_and_draft_state_seam(ctx, api):
    ctx.db.set_setting(shell.AUTOSAVE_KEY, "0")
    assert data(api.get_status())["draft"]["autosave"] is False
    ctx.draft_state = lambda: (True, "2 changes")             # what R18 will set
    assert data(api.get_status())["draft"] == {"dirty": True, "autosave": False, "change_summary": "2 changes"}


def test_get_status_is_plain_json(api):
    import json
    json.dumps(api.get_status())


def test_get_status_speed(tmp_path, capsys):
    """Budget: <= 50 ms. Measured on the synthetic database (60 days, 300 items)."""
    from db import Database
    from synthdata import build
    path = tmp_path / "big.db"
    Database(path)
    build(path, date(2026, 9, 20), days=60, extra_items=300)
    c = webapi.make_context(path, clock=lambda db: NOW)
    shell.get_status(c)         # (first call opens this thread's connection)
    times = []
    for _ in range(40):
        t = time.perf_counter()
        shell.get_status(c)
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    p95 = times[int(len(times) * 0.95) - 1]
    with capsys.disabled():
        print(f"\nget_status: median {times[len(times) // 2]:.2f} ms, p95 {p95:.2f} ms (budget 50 ms)")
    assert p95 < 50


# ---------------------------------------------------------------- the look

def test_get_theme_defaults(api):
    th = data(api.get_theme())
    assert th["theme"] == "dark" and th["accent"] == ui_theme.ACCENTS["Orange"] and th["scale"] == "auto"
    assert th["accents"]["Blue"] == ui_theme.ACCENTS["Blue"]


def test_get_theme_reads_the_old_appearance_key(ctx, api):
    ctx.db.set_setting("ui.appearance", "light")
    assert data(api.get_theme())["theme"] == "light"
    ctx.db.set_setting(ui_theme.THEME_KEY, "amoled")
    assert data(api.get_theme())["theme"] == "amoled"


@pytest.mark.parametrize("theme", ["dark", "amoled", "light", "system"])
def test_set_appearance_accepts_the_four_themes(ctx, api, theme):
    assert api.set_appearance(theme)["ok"]
    assert ctx.db.get_setting(ui_theme.THEME_KEY) == theme
    assert data(api.get_theme())["theme"] == theme


@pytest.mark.parametrize("bad", ["Dark", "", "neon", None, 3, ["dark"], {"a": 1}])
def test_set_appearance_rejects_anything_else(ctx, api, bad):
    reply = api.set_appearance(bad)
    assert not reply["ok"] and "Pick" in reply["error"]
    assert ctx.db.get_setting(ui_theme.THEME_KEY) is None


def test_set_accent_stores_upper_case_hex(ctx, api):
    assert api.set_accent("#12ab9f")["ok"]
    assert ctx.db.get_setting(ui_theme.ACCENT_KEY) == "#12AB9F"
    assert data(api.get_theme())["accent"] == "#12AB9F"


@pytest.mark.parametrize("bad", ["red", "12AB9F", "#12AB9", "#12AB9FF", "#12AB9G", "#12 B9F", "", None, 5, ["#112233"],
                                 "#112233; x", "#112233\n"])
def test_set_accent_rejects_non_hex(ctx, api, bad):
    reply = api.set_accent(bad)
    assert not reply["ok"]
    assert ctx.db.get_setting(ui_theme.ACCENT_KEY) is None


@pytest.mark.parametrize("value, saved", [("auto", "auto"), ("Auto", "auto"), ("100", "100"), (90, "90"), ("80", "80"),
                                          (70, "70")])
def test_set_scale_accepts_only_the_allowed_sizes(ctx, api, value, saved):
    assert api.set_scale(value)["ok"]
    assert data(api.get_theme())["scale"] == saved


@pytest.mark.parametrize("bad", ["50", 110, "60", "1", "", None, True, 1.0, "100%", "90 ", [90], "autoo"])
def test_set_scale_rejects_other_values(ctx, api, bad):
    assert not api.set_scale(bad)["ok"]
    assert ctx.db.get_setting(ui_theme.SIZE_KEY) is None


# ---------------------------------------------------------------- ui_ready and client errors

def test_ui_ready_notes_when(ctx, api):
    assert api.ui_ready()["ok"]
    assert abs(ctx.ui_ready_at - time.time()) < 5


def test_log_client_error_writes_one_cleaned_line(api, log):
    assert api.log_client_error("TypeError: x is null\n    at fn (a.js:1)\n\x1b[31mred\x1b[0m")["ok"]
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and "page error: TypeError: x is null | at fn (a.js:1)" in lines[0]
    assert "\x1b" not in lines[0]


def test_log_client_error_cannot_fake_log_lines_or_flood(api, log):
    api.log_client_error("ok\n2026-01-01 00:00:00,000 ERROR Lockdown service: fake")
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1
    api.log_client_error("x" * 100_000)
    text = log.read_text(encoding="utf-8")
    assert len(text) < 2000


def test_log_client_error_ignores_blank_and_refuses_non_text(api, log):
    assert api.log_client_error("   \n  ")["ok"]
    assert not log.exists()
    assert not api.log_client_error({"a": 1})["ok"]
    assert not api.log_client_error(None)["ok"]


# ---------------------------------------------------------------- free actions

def test_antibypass_switch_on_is_never_gated(ctx, api):
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 250})      # the challenge is on and not passed
    ab.switch_off(ctx.db, NOW)
    assert ab.status(ab.settings(ctx.db), NOW) == "phrase"
    st = data(api.antibypass_switch_on())
    assert st["off_since"] is None and not ab.is_off(ctx.db)


def test_antibypass_switch_on_also_works_outside_the_allowed_hours(ctx, api):
    ab.save(ctx.db, {**ab.settings(ctx.db), "hours": True, "windows": [{"days": [0], "start": "09:00", "end": "10:00"}]})
    ab.switch_off(ctx.db, NOW)
    assert data(api.antibypass_switch_on())["off_since"] is None


def test_pause_resume_is_free(ctx, api):
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True})
    pause.start(ctx.db, NOW, 60, silent=False)
    assert data(api.get_status())["pause"] is not None
    st = data(api.pause_resume())
    assert st["pause"] is None and pause.state(ctx.db, NOW) is None


def test_pause_resume_when_not_paused_is_harmless(api):
    assert data(api.pause_resume())["pause"] is None


def test_service_start_runs_the_task_from_source(api, monkeypatch):
    calls = []
    monkeypatch.setattr(shell, "_run_elevated", lambda program, params: calls.append((program, params)))
    ab_reply = api.service_start()
    assert ab_reply == {"ok": True, "data": {"starting": True}}
    assert calls == [("cmd.exe", f'/c schtasks /End /TN "Lockdown Enforcer" & schtasks /Run /TN "Lockdown Enforcer"')]


def test_service_start_restarts_the_installed_service(api, monkeypatch):
    import paths
    calls = []
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setattr(shell, "_run_elevated", lambda program, params: calls.append((program, params)))
    assert api.service_start()["ok"]
    assert calls[0][0].endswith("powershell.exe")
    assert f"Restart-Service -Name {paths.SERVICE_NAME} -Force" in calls[0][1]


def test_service_start_is_ungated_by_antibypass(ctx, api, monkeypatch):
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "hours": True,
                     "windows": [{"days": [0], "start": "09:00", "end": "10:00"}]})
    monkeypatch.setattr(shell, "_run_elevated", lambda program, params: None)
    assert api.service_start()["ok"]


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="Windows can really start it")
def test_service_start_says_so_off_windows(api):
    reply = api.service_start()
    assert not reply["ok"] and "only works on Windows" in reply["error"]


def test_no_shell_method_accepts_a_confirmed_flag():
    """Nothing here trusts the page for a decision: no parameter named like a permission."""
    import inspect
    for name, kind in webapi.api_methods():
        fn = core.registry.get(name)[0]
        if fn.__module__ != "webapi.shell":
            continue
        params = list(inspect.signature(fn).parameters)[1:]
        assert not [p for p in params if "confirm" in p.lower() or "force" in p.lower() or "allow" in p.lower()], name
