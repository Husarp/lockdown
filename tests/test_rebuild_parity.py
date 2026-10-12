"""R29: the small things the parity audit fixed - "Check a site" tables loaded at start (webapi/protection.py warm), a
category in a mode's "+ Other site or app" (the old picker's Category... button), and the performance harness' warm-ups
and lists (tests/ui/perfkit.py)."""
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

import antibypass as ab
import modes
import webapi
from webapi import core
from webapi import modes as modes_api
from webapi import protection as prot_api
from webapi.guard import GuardService

NOW = datetime(2026, 10, 11, 15, 42, 17)


@pytest.fixture(autouse=True)
def _log(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")


@pytest.fixture
def ctx(tmp_path):
    c = webapi.make_context(tmp_path / "t.db", clock=lambda db: NOW)
    c.guard = GuardService(lambda: c.db, lambda db: NOW, rng=random.Random(3))
    hub = type("Hub", (), {})()
    hub.status_worker = type("W", (), {"poke": lambda self: None})()
    c.hub = hub
    return c


@pytest.fixture
def api(ctx):
    return core.build_api(ctx)


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


# ================================================================ "Check a site" tables are loaded at start

def make_lists(folder, keys=("scam", "phishing"), each=5000):
    folder.mkdir(parents=True, exist_ok=True)
    for n, key in enumerate(keys):
        (folder / f"{key}.txt").write_text("".join(f"w{n}x{i}.example\n" for i in range(each)) + "*.wild.example\n", encoding="utf-8")
    return folder


def test_warm_loads_the_lists_so_the_first_check_is_a_lookup(ctx, api, tmp_path):
    ctx.protection_dir = make_lists(tmp_path / "lists")
    index = prot_api._index(ctx)
    assert index.tables == {}
    prot_api.warm(ctx)
    assert {key for _folder, key in index.tables} == {"scam", "phishing"}      # (read once, ahead of the click)
    t = time.perf_counter()
    found = data(api.protection_check("w1x42.example"))
    assert time.perf_counter() - t < 0.05
    assert [x["key"] for x in found["lists"]] == ["phishing"]
    assert [x["key"] for x in data(api.protection_check("a.wild.example"))["lists"]] == ["scam", "phishing"]
    before = dict(index.tables)
    prot_api.warm(ctx)                                                           # again: nothing is read again
    assert index.tables == before and all(index.tables[k] is before[k] for k in before)


def test_warm_with_no_list_files_yet_does_nothing_and_does_not_fail(ctx, tmp_path):
    ctx.protection_dir = tmp_path / "nothing-here"
    prot_api.warm(ctx)
    assert prot_api._index(ctx).tables == {}


def test_the_warm_up_thread_runs_once_and_a_failure_is_only_logged(ctx, tmp_path, monkeypatch, caplog):
    ctx.protection_dir = make_lists(tmp_path / "lists")
    thread = prot_api.start_warmup(ctx, delay=0)
    thread.join(5)
    assert not thread.is_alive() and len(prot_api._index(ctx).tables) == 2
    calls = []
    monkeypatch.setattr(prot_api, "warm", lambda c: (calls.append(1), 1 / 0))
    with caplog.at_level("ERROR"):
        t2 = prot_api.start_warmup(ctx, delay=0)
        t2.join(5)
    assert calls == [1] and "warm-up failed" in caplog.text and not t2.is_alive()
    t3 = prot_api.start_warmup(ctx, delay=60)                                    # a window that is closed before it ran
    t3.stop_event.set()
    t3.join(5)
    assert not t3.is_alive()


def test_the_agent_starts_the_protection_warm_up():
    text = (Path(__file__).resolve().parents[1] / "src" / "agent.py").read_text(encoding="utf-8")
    assert "webapi.protection.start_warmup(ctx)" in text and "webapi.screentime.start_warmup(ctx)" in text


# ================================================================ a category in "+ Other site or app"

def check(api, **spec):
    return api.mode_extra_check({"kind": "category", "text": "games", "name": "", **spec})


def test_a_category_is_checked_against_your_categories(api):
    out = data(check(api))
    assert out == {"kind": "category", "name": "Games", "targets": ["games"], "block_type": "close", "app_path": None}
    assert data(check(api, name="Time wasters", ways=["minimize", "internet"]))["block_type"] == "minimize,internet"
    for spec, words in (({"text": "nonsense"}, "Pick one of your categories"), ({"ways": []}, "Tick at least one"),
                        ({"ways": ["close", "minimize"]}, "do the same thing"), ({"ways": ["dns"]}, "Bad request")):
        reply = check(api, **spec)
        assert not reply["ok"] and words in reply["error"], reply


def test_a_category_extra_is_saved_blocks_its_apps_and_is_checked_again_on_save(ctx, api):
    extra = data(check(api, ways=["close", "background"]))
    sent = {"id": "", "name": "Evening", "categories": [], "items": [], "groups": [], "extra": [extra], "mute": False,
            "pomodoro": None, "windows": None}
    saved = data(api.mode_save(sent))["saved_id"]
    mode = next(m for m in modes.load(ctx.db) if m["id"] == saved)
    assert mode["extra"] == [extra]
    made = modes.targets(mode, [], [], {("app", "game.exe"): "games"})
    assert [(t["item_type"], t["target"], t["block_type"]) for t in made if t["item_type"] == "category"] == [("category", "games", "close,background")]
    # a forged extra: an unknown category, or a way that is no way, never gets in
    for forged in ({**extra, "targets": ["nonsense"]}, {**extra, "targets": ["games"], "block_type": "dns"}):
        reply = api.mode_save({**sent, "id": "", "name": "Forged", "extra": [forged]})
        assert not reply["ok"], forged


# ================================================================ the performance harness' warm-ups and lists

def test_the_harness_builds_lists_and_mirrors_the_agents_warm_ups(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "ui"))
    import perfkit
    perfkit.setup_paths()
    lists = perfkit.build_lists(tmp_path / "lists", hosts=4000)
    assert sorted(p.stem for p in lists.glob("*.txt")) == sorted(perfkit.LIST_KEYS)
    assert sum(1 for _ in open(lists / "scam.txt", encoding="utf-8")) == 1000
    c = webapi.make_context(tmp_path / "t.db", clock=lambda db: NOW)
    c.protection_dir = lists
    took = perfkit.warm_up(c)
    assert set(took) == {"network", "screentime", "protection"} and len(prot_api._index(c).tables) == len(perfkit.LIST_KEYS)


# ---------------------------------------------------------------- designer extras that were left out on purpose
# GAPS.md "Designer extras - leave out": the mock draws them but the PC has no backend, so a control for them would be a
# switch that does nothing. One distinctive mock string per extra must not be in the shipped UI (comment lines excepted).

UI_SRC = Path(__file__).resolve().parents[1] / "src" / "ui"


def _ui_text() -> str:
    out = []
    for f in sorted(UI_SRC.rglob("*")):
        if f.suffix not in (".js", ".html", ".mjs") or not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.lstrip().startswith(("//", "*", "/*")):
                out.append(line)
    return "\n".join(out)


LEFT_OUT = {
    "island-and-phone-card": ["Island & phone", "On screen, top centre"],
    "mute-it-way": ["Mute it"],
    "pause-limit-longest": ["Longest pause"],
    "pause-limit-per-day": ["Pause time per day"],
    "word-option-ignore-accents": ["Ignore accents"],
    "word-option-wildcard-switch": ["as a wildcard"],
    "notifications-sound-and-position": ["Lockdown pop-up only", "Windows notification"],
    "notifications-quiet-hours": ["Hold notifications"],
    "settings-language": ["Polski", "'Language'", '"Language"'],
    "settings-start-in-tray": ["Start in the tray"],
    "settings-update-channel": ["'chan'", "Beta"],
    "settings-site-history": ["Suggest sites you blocked before"],
    "settings-service-restart": ["Keeps rules running in the background"],
    "block-notice-full-screen": ["notice-card", "is blocked</h2>"],
    "bedtime-weekdays": ["Every night"],
    "netlog-7-day-range": ["Kept for 7 days"],
}


@pytest.mark.parametrize("extra", sorted(LEFT_OUT))
def test_designer_extra_left_out_did_not_slip_into_the_ui(extra):
    text = _ui_text()
    found = [s for s in LEFT_OUT[extra] if s in text]
    assert not found, f"{extra}: {found} is in src/ui again"


def test_whole_words_is_only_prose_not_an_option():
    import re
    text = _ui_text()
    for m in re.finditer(r"Whole words only", text):
        assert text[m.end():m.end() + 3] == ' (\\', "Whole words only looks like an option label"


def test_uninstall_protection_and_trusted_time_are_info_chips_without_a_switch():
    lines = [l for l in (UI_SRC / "js" / "pages" / "antibypass.js").read_text(encoding="utf-8").splitlines()
             if "Uninstall protection" in l or "Trusted time" in l]
    assert len(lines) >= 2
    for l in lines:
        assert "Always on" in l and "chip" in l
        assert not any(w in l for w in ("sw(", "class=\"switch", "checkbox", "role=\"switch\""))
