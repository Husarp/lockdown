"""The new UI (design/redesign) running as a real window, next to the customtkinter app.

pywebview draws the HTML in Windows' built-in Edge engine (WebView2), so there is none of the widget-by-widget
redraw that made the old UI lag. The engine (rules, service, db, reminders, antibypass) is untouched: this is a
view. The page talks to Python through `window.pywebview.api.<method>()`.

Security: the page is untrusted (a webview can be inspected and edited), so no enforcement decision is left to
it. Anything that LOOSENS a block (deleting, disabling, turning Lockdown off, weakening a rule) is allowed only
when the Anti-Bypass challenge is currently satisfied (status "free"); otherwise Python refuses and says to use
the challenge. Adding or tightening a block is always allowed, as in the current app. The service enforces
independently of this window regardless.

Run it (alongside the running app, for now):  .venv\\Scripts\\python.exe src\\webview_app.py
"""
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # so `import db` etc. work when run directly

import webview

import antibypass
import emergency
import reminders
import stats
from blocker import apps, site_block
from db import Database
from gui import theme
from rules import BLOCK, make_schedule
from service import HEARTBEAT_KEY
from trusted_time import now_from_db

UI = (Path(getattr(sys, "_MEIPASS", "")) / "webui" / "index.html") if getattr(sys, "_MEIPASS", "") \
    else Path(__file__).resolve().parent / "webui" / "index.html"    # packaged (PyInstaller) vs run from source
DARK_BG = "#101418"
SERVICE_TIMEOUT_SEC = 15
ACCENT_NAMES = {v: k for k, v in theme.ACCENTS.items()}


# ---------------------------------------------------------------- reading: engine -> UI shape

def _block_action(item: dict) -> str:
    """The UI's "when blocked" value for an item, from its stored block_type."""
    bt = item.get("block_type")
    if item["item_type"] == "app":
        flags = apps.block_flags(bt)
        return "min" if "minimize" in flags else "cut" if "internet" in flags and "close" not in flags else "close"
    flags = site_block.site_flags(bt)
    return "close" if "close" in flags else "back" if "back" in flags else "block"


def _windows_for_ui(schedule_json: str) -> list[dict]:
    from rules import load_schedule
    try:
        s = load_schedule(schedule_json)
    except (ValueError, KeyError, TypeError):
        return []
    return [{"days": [i in w["days"] for i in range(7)], "from": w["start"], "to": w["end"]}
            for w in s.get("windows", [])]


def _rules_for_ui(db) -> list[dict]:
    from rules import (TIME_LIMIT_FIELDS, allowance_left, describe_rule, effective_rules, limits, load_schedule,
                       time_bucket)
    groups = db.list_groups()
    group_of = {i: g["name"] for g in groups for i in g["members"]}
    now = now_from_db(db)
    usage = db.usage_lookup(now)
    out = []
    for item in db.list_items():
        rules = effective_rules(item, groups)
        row = {"id": str(item["id"]), "n": item["display_name"],
               "kind": "app" if item["item_type"] == "app" else "site", "target": item["target"],
               "enabled": not item.get("disabled"), "group": group_of.get(item["id"]), "when": _block_action(item),
               "windows": [], "limitType": "off", "limit": 0, "used": 0,
               "allowOn": False, "allowMin": 0, "allowTimes": 0, "allowActive": False, "allowLeft": 0,
               "words": " · ".join(describe_rule(r, now).splitlines()[0] for r in rules) if rules else ""}
        mode, allowances = None, []
        for r in rules:
            if r["rule_type"] == "scheduled":
                # a member's own blocked hours add to its group's: show them all (of the same kind - blocked, or
                # allowed only - as the first; the list has one kind)
                try:
                    kind = load_schedule(r.get("schedule") or "")["mode"]
                except (ValueError, KeyError, TypeError):
                    kind = None
                if mode is None or kind == mode:
                    mode = mode or kind
                    row["windows"] += [w for w in _windows_for_ui(r.get("schedule") or "") if w not in row["windows"]]
                if r.get("allowance_min"):
                    spent = allowance_left(r, now, usage)
                    left = max(0, round((spent[1] - spent[0]) / 60)) if spent else r["allowance_min"]
                    allowances.append((not spent, left, r["allowance_min"]))
            elif r["rule_type"] == "time_limit":
                fields = limits(r, TIME_LIMIT_FIELDS)
                period = "day" if "day" in fields else "week" if "week" in fields else None
                if period:
                    used = round(usage(r["usage_owner"], time_bucket(period, now, usage.clock)) / 60)
                    # several limits (its own, a group's, its own extra in a group): the one with least left
                    if row["limitType"] == "off" or fields[period] - used < row["limit"] - row["used"]:
                        row["limitType"] = "daily" if period == "day" else "weekly"
                        row["limit"], row["used"] = fields[period], used
        if allowances:   # several (the group's, its own extra): the one in use with least left limits it
            idle, left, minutes = min(allowances)
            row["allowOn"], row["allowMin"], row["allowActive"], row["allowLeft"] = True, minutes, not idle, \
                (left if not idle else 0)
        out.append(row)
    return out


def _reminders_for_ui(db) -> list[dict]:
    return [{"id": r["id"], "n": r["text"], "msg": r["text"], "on": bool(r["on"]), "g": "",
             "mode": r["kind"], "interval": r["every"], "times": list(r.get("times") or []),
             "days": list(r.get("days") or [])} for r in reminders.custom_list(db)]


def _home_stats(db, now: datetime) -> dict:
    """The Home tiles/chart that aren't derived from rules: screen time today, blocked tries, last 7 days.
    The per-day totals are summed in SQL (one grouped query -> 7 rows), not by pulling every activity row into
    Python - that scan was almost all of get_state's time."""
    today = now.date()
    rows = db.conn.execute("SELECT substr(minute, 1, 10) d, SUM(active_seconds) FROM activity "
                           "WHERE minute >= ? AND minute < ? GROUP BY d",
                           (f"{today - timedelta(days=6)} 00:00", f"{today + timedelta(days=1)} 00:00"))
    by_day = {r[0]: r[1] or 0 for r in rows}
    week = [{"d": (today - timedelta(days=i)).strftime("%a")[:1],
             "v": round(by_day.get((today - timedelta(days=i)).isoformat(), 0) / 60)} for i in range(6, -1, -1)]
    return {"screenToday": stats.hm(by_day.get(today.isoformat(), 0)),
            "blockedToday": len(stats.blocked_events(db, today)), "last7": week}


# ---------------------------------------------------------------- writing: UI shape -> engine

def _rules_from_draft(d: dict) -> list[dict]:
    """The engine rules for a UI rule draft: its time windows (+ allowance), a daily/weekly limit, or - if it
    has neither - a plain permanent block."""
    rules = []
    windows = d.get("windows") or []
    if windows:
        wins = [([i for i, on in enumerate(w.get("days") or []) if on], w.get("from", "00:00"), w.get("to", "00:00"))
                for w in windows if any(w.get("days") or [])]
        if wins:
            rule = {"rule_type": "scheduled", "schedule": make_schedule(BLOCK, wins)}
            if d.get("allowOn") and d.get("allowMin"):
                rule["allowance_min"] = int(d["allowMin"])
            rules.append(rule)
    if d.get("limitType") == "daily" and d.get("limit"):
        rules.append({"rule_type": "time_limit", "daily_limit_min": int(d["limit"])})
    elif d.get("limitType") == "weekly" and d.get("limit"):
        rules.append({"rule_type": "time_limit", "weekly_limit_min": int(d["limit"])})
    if not rules:
        rules.append({"rule_type": "permanent"})
    return rules


def _block_type_from_draft(d: dict) -> str | None:
    when = d.get("when")
    if d.get("kind") == "app":
        return {"min": "minimize", "cut": "internet"}.get(when, "close")
    return {"close": "close", "back": "back"}.get(when, "dns")


class Api:
    """pywebview calls these from its own worker threads, several at once: each thread gets its own Database
    (a shared connection would interleave one thread's transaction / write retry with another's)."""

    def __init__(self, path=None):
        self.window = None
        self._db_path = path
        self._local = threading.local()

    @property
    def db(self) -> Database:
        local = self.__dict__.setdefault("_local", threading.local())
        db = getattr(local, "db", None)
        if db is None:
            path = self.__dict__.get("_db_path")
            db = local.db = Database(path) if path else Database()
        return db

    @db.setter
    def db(self, value: Database):   # (tests: use this database file - every thread opens its own connection to it)
        self._db_path = value.path
        self.__dict__.setdefault("_local", threading.local()).db = value

    def ui_ready(self):
        if self.window:
            self.window.show()

    def get_state(self) -> dict:
        db, now = self.db, now_from_db(self.db)
        state: dict = {"on": not antibypass.is_off(db)}
        try:
            heartbeat = float(db.get_setting(HEARTBEAT_KEY, "0"))
            state["service"] = "ok" if datetime.now().timestamp() - heartbeat < SERVICE_TIMEOUT_SEC else "down"
        except (TypeError, ValueError):
            state["service"] = "down"
        for key, fn in (("rules", lambda: _rules_for_ui(db)), ("rem", lambda: _reminders_for_ui(db)),
                        ("emergencyLeft", lambda: emergency.uses_left(db, now)[0]),
                        ("home", lambda: _home_stats(db, now))):
            try:
                state[key] = fn()
            except Exception:
                pass
        state["theme"] = (db.get_setting(theme.THEME_KEY, "") or "dark").lower()
        state["accent"] = ACCENT_NAMES.get(db.get_setting(theme.ACCENT_KEY, "") or theme.ACCENTS["Orange"], "Orange")
        size = db.get_setting(theme.SIZE_KEY, "auto")
        state["size"] = size if size and size != "auto" else "Auto"
        return state

    # ---- the challenge gate for anything that loosens a block ----
    def _may_loosen(self) -> bool:
        return antibypass.status(antibypass.settings(self.db), now_from_db(self.db)) == "free"

    _LOCKED = {"ok": False, "error": "Pass the Anti-Bypass challenge first (in the current app for now)."}

    # ---- rules ----
    def save_rule(self, rule: dict):
        db = self.db
        try:
            existing = {i["id"] for i in db.list_items()}
            rid = int(rule["id"]) if str(rule.get("id", "")).isdigit() else None
            is_edit = rid in existing
            if is_edit and not self._may_loosen():          # an edit can weaken a block - gate it
                return self._LOCKED
            item_type = "app" if rule.get("kind") == "app" else "site"
            targets = (rule.get("target") or "").split()
            engine_rules = _rules_from_draft(rule)
            block_type = _block_type_from_draft(rule)
            if is_edit:
                db.update_item(rid, rule.get("n") or "", targets, None, engine_rules,
                               block_type=block_type, disabled=not rule.get("enabled", True))
            else:
                db.add_item(rule.get("n") or "", targets, item_type, rules=engine_rules, block_type=block_type)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def delete_rule(self, rule_id):
        if not self._may_loosen():
            return self._LOCKED
        try:
            self.db.remove_item(int(rule_id))
            return {"ok": True}
        except (ValueError, TypeError) as e:
            return {"ok": False, "error": str(e)}

    def set_enabled(self, on, seconds=0):
        if on:
            antibypass.switch_on(self.db)               # coming back to your rules is never gated
            return {"ok": True}
        if not self._may_loosen():                       # switching off is the biggest loosening there is
            return self._LOCKED
        antibypass.switch_off(self.db, now_from_db(self.db))
        return {"ok": True}

    def emergency_unlock(self):
        try:
            now = now_from_db(self.db)
            items = [i for part in emergency.choices(self.db, now) for i in part]   # never the permanent ones
            until = emergency.unlock(self.db, items, now)
            return {"ok": True, "until": until.strftime("%H:%M")}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def restart_service(self):
        try:
            import subprocess
            from paths import APP_DIR
            subprocess.Popen([str(APP_DIR / "LockdownService.exe"), "run"])
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def save_reminder(self, reminder: dict):
        try:
            existing = reminders.custom_list(self.db)
            by_id = {r["id"]: r for r in existing}
            rid = reminder.get("id")
            merged = {**reminders.DEFAULT_CUSTOM, **by_id.get(rid, {}),
                      "id": rid or __import__("uuid").uuid4().hex[:8],
                      "text": reminder.get("msg") or reminder.get("n") or "",
                      "on": bool(reminder.get("on", True)), "kind": reminder.get("mode", "interval"),
                      "every": int(reminder.get("interval", 60) or 60),
                      "times": list(reminder.get("times") or ["12:00"])}
            out = [merged if r["id"] == merged["id"] else r for r in existing]
            if merged["id"] not in by_id:
                out.append(merged)
            reminders.save(self.db, reminders.CUSTOM_KEY, out)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def verify_challenge(self, typed):
        """Re-checked in Python. A custom phrase is checked directly; a random phrase can't be verified here yet,
        so it refuses (loosen via the current app for now) rather than trusting the page."""
        cfg = antibypass.settings(self.db)
        custom = (cfg.get("custom_phrase") or "").strip()
        if custom and str(typed).strip() == custom:
            return {"ok": True}
        return {"ok": False, "error": "Use the challenge in the current app for now."}

    def export_backup(self, data_json):
        path = self.window.create_file_dialog(webview.SAVE_DIALOG, save_filename="lockdown-backup.json")
        if path:
            Path(path if isinstance(path, str) else path[0]).write_text(data_json, encoding="utf-8")
        return bool(path)


def push(window, **state):
    import json
    window.evaluate_js(f"window.lockdown && window.lockdown.push({json.dumps(state)})")


def _diag(msg: str):
    try:
        import tempfile
        with open(Path(tempfile.gettempdir()) / "lockdown-newui.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%H:%M:%S} {msg}\n")
    except Exception:
        pass


def _serve(directory: Path) -> str:
    """Serve the UI over http on a loopback port. The page fetches sub-resources at runtime, which a browser
    blocks under file:// - so it must come from http, not the packaged file path (that was the dark screen)."""
    import http.server
    import socketserver
    import threading

    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(directory), **k)
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/index.html"


def main():
    _diag(f"start; frozen={bool(getattr(sys, '_MEIPASS', ''))}; UI={UI}; UI.exists={UI.exists()}")
    url = _serve(UI.parent)
    _diag(f"serving at {url}")
    api = Api()
    window = webview.create_window("Lockdown", url, js_api=api, fullscreen=True, resizable=False,
                                   background_color=DARK_BG, hidden=True)
    api.window = window
    try:
        webview.start(gui="edgechromium", debug=False)
    except Exception as e:
        _diag(f"webview.start failed: {type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
