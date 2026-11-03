"""Notification bursts keep their details and replace one notice on PC."""
from notification_batch import BlockingMessage, NotificationBatch


def test_merges_names_reasons_and_different_end_times():
    batch = NotificationBatch()
    assert batch.merge(["YouTube: limit reached until 18:00.", "Instagram: blocked until 20:00."], 0) == \
        "YouTube: limit reached until 18:00.\nInstagram: blocked until 20:00."
    assert batch.merge(["Steam: blocked until 21:00."], 3).endswith("Steam: blocked until 21:00.")


def test_duplicates_wait_ten_minutes_but_new_warning_is_kept():
    batch = NotificationBatch()
    assert batch.merge(["YouTube: 5 minutes left."], 0)
    assert batch.merge(["YouTube: 5 minutes left."], 30) is None
    assert batch.merge(["YouTube: 1 minute left."], 60) == "YouTube: 1 minute left."
    assert batch.merge(["YouTube: 5 minutes left."], 600)


def test_burst_expires_and_empty_messages_never_post():
    batch = NotificationBatch()
    assert batch.merge(["", "   "], 0) is None
    batch.merge(["YouTube blocked."], 0)
    assert batch.merge(["Instagram blocked."], 11) == "Instagram blocked."


def test_blocking_marker_keeps_string_compatibility():
    assert BlockingMessage("Blocked: YouTube") == "Blocked: YouTube"


def test_multiline_mode_details_deduplicate_against_rule_events():
    batch = NotificationBatch()
    assert batch.merge(["Work mode on\nYouTube blocked until 18:00.", "YouTube blocked until 18:00."], 0) == \
        "Work mode on\nYouTube blocked until 18:00."


def test_continuous_stream_does_not_keep_old_details_forever():
    batch = NotificationBatch()
    batch.merge(["A blocked."], 0)
    batch.merge(["B blocked."], 9)
    assert batch.merge(["C blocked."], 18) == "C blocked."


def test_rule_events_keep_different_reasons_and_end_times():
    from datetime import datetime
    from alerts import BlockWatcher, DEFAULTS
    from tests.test_alerts import app, no_usage
    now = datetime(2026, 11, 3, 20)
    w = BlockWatcher()
    items = [app(1, "YouTube"), app(2, "Instagram")]
    w.check(items, [], no_usage, now, set(), DEFAULTS, active_blocks=[])
    blocks = [{"item": i, "reason": reason, "until": end, "rule": {"rule_type": reason}}
              for i, reason, end in [(items[0], "limit", now.replace(hour=21)),
                                      (items[1], "temporary", now.replace(hour=22))]]
    messages = w.check(items, [], no_usage, now, set(), DEFAULTS, active_blocks=blocks)
    assert messages == ["YouTube is now blocked until 21:00 - time limit reached.",
                        "Instagram is now blocked until 22:00 (temporary block)."]


def _sink(tmp_path, monkeypatch):
    import sys
    from gui.toast import Toaster
    from resident.notices import Notices
    from tests.test_game_notices import fake_windows_toasts
    from tests.test_rebuild_reminder_ui import Env
    monkeypatch.setitem(sys.modules, "windows_toasts", fake_windows_toasts())
    env = Env(tmp_path)
    toaster = Toaster(lambda fn: fn(), lambda text: None)
    scheduled, times = [], [0]
    sink = Notices(env.hub, env.corner, toaster, read_format=lambda: "toast", clock=lambda: times[0],
                   schedule=lambda delay, fn: scheduled.append((delay, fn)) or object())
    env.hub.notify.show = sink.show
    return env, toaster, scheduled, times


def test_pc_sink_waits_for_details_and_posts_one_replaceable_notice(tmp_path, monkeypatch):
    env, toaster, scheduled, times = _sink(tmp_path, monkeypatch)
    env.hub.notify(BlockingMessage("YouTube: limit reached until 18:00."))
    env.hub.notify(BlockingMessage("Instagram: blocked until 20:00."))
    assert toaster._toaster is None and len(scheduled) == 1 and scheduled[0][0] == 0.75
    scheduled.pop()[1]()
    shown = toaster._toaster.shown
    assert len(shown) == 1 and shown[0].tag == "lockdown-blocks"
    assert "YouTube" in shown[0].text_fields[0] and "Instagram" in shown[0].text_fields[0]
    times[0] = 3
    env.hub.notify(BlockingMessage("Steam blocked until 21:00."))
    scheduled.pop()[1]()
    assert shown[1].tag == shown[0].tag and shown[1].suppress_popup
    assert "YouTube" in shown[1].text_fields[0] and "Steam" in shown[1].text_fields[0]
    env.hub.notify(BlockingMessage("Steam blocked until 21:00."))
    scheduled.pop()[1]()
    assert len(shown) == 2


def test_delayed_notices_respect_new_silent_pause(tmp_path, monkeypatch):
    from types import SimpleNamespace
    env, toaster, scheduled, _ = _sink(tmp_path, monkeypatch)
    env.hub.notify(BlockingMessage("YouTube blocked."))
    env.hub.snapshot = SimpleNamespace(pause={"silent": True}, mode=None)
    scheduled.pop()[1]()
    assert toaster._toaster is None
    env.hub.snapshot = None
    env.hub.notify(BlockingMessage("YouTube blocked."))
    scheduled.pop()[1]()
    assert len(toaster._toaster.shown) == 1


def test_warning_for_app_in_front_merges_without_windows_duplicate(tmp_path, monkeypatch):
    env, toaster, scheduled, _ = _sink(tmp_path, monkeypatch)
    env.hub.notify(BlockingMessage("YouTube: 5 min left."), front=True)
    env.hub.notify(BlockingMessage("Instagram: 5 min left."))
    scheduled.pop()[1]()
    assert toaster._toaster is None
    assert env.corner is not None
    # The real Corner publishes the merged card through its host.
    assert len(env.corner.cards()) == 1
    assert "YouTube" in env.corner.cards()[0]["text"] and "Instagram" in env.corner.cards()[0]["text"]
    env.hub.notify(BlockingMessage("Steam: 5 min left."), front=True)
    scheduled.pop()[1]()
    assert len(env.corner.cards()) == 1 and "Steam" in env.corner.cards()[0]["text"]


def test_later_burst_can_alert_again_while_reusing_windows_id(tmp_path, monkeypatch):
    from datetime import timedelta
    env, toaster, scheduled, times = _sink(tmp_path, monkeypatch)
    env.hub.notify(BlockingMessage("YouTube: 5 min left."))
    scheduled.pop()[1]()
    toaster._replacement_at["lockdown-blocks"] -= timedelta(seconds=20)
    times[0] = 20
    env.hub.notify(BlockingMessage("Instagram blocked."))
    scheduled.pop()[1]()
    assert not getattr(toaster._toaster.shown[-1], "suppress_popup", False)
    assert toaster._toaster.shown[-1].text_fields == ["Instagram blocked."]


def test_mode_start_first_notice_already_names_every_block(tmp_path, monkeypatch):
    from datetime import timedelta
    import modes
    from resident.tray_actions import start_mode
    from tests.test_rebuild_reminder_ui import NOW
    env, toaster, scheduled, _ = _sink(tmp_path, monkeypatch)
    env.db.add_item("YouTube", ["youtube.com"], "site", rules=[])
    env.db.add_item("Instagram", ["instagram.com"], "site", rules=[])
    mode = modes.load(env.db)[0]
    assert start_mode(env.hub, env.db, mode["id"], NOW + timedelta(hours=1))
    assert toaster._toaster is None
    scheduled.pop()[1]()
    shown = toaster._toaster.shown
    assert len(shown) == 1
    assert all(word in shown[0].text_fields[0] for word in ("YouTube", "Instagram", "10:00", mode["name"]))


def test_front_warning_replaces_prior_windows_block_notice(tmp_path, monkeypatch):
    env, toaster, scheduled, times = _sink(tmp_path, monkeypatch)
    env.hub.notify(BlockingMessage("YouTube blocked."))
    scheduled.pop()[1]()
    original = toaster._toaster.shown[0]
    times[0] = 2
    env.hub.notify(BlockingMessage("Instagram: 1 min left."), front=True)
    scheduled.pop()[1]()
    assert toaster._toaster.removed == [original]
    assert len(env.corner.cards()) == 1
    times[0] = 3
    env.hub.notify(BlockingMessage("Steam blocked."))
    scheduled.pop()[1]()
    assert env.corner.cards() == []
    assert toaster._toaster.shown[-1].tag == original.tag


def test_bedtime_cover_fallback_joins_the_same_blocking_burst(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from resident import overlay, windows
    env, toaster, scheduled, _ = _sink(tmp_path, monkeypatch)
    env.ui.toaster = toaster
    win = windows.Windows(overlay=SimpleNamespace(evaluate_js=lambda script: None))
    ctx = SimpleNamespace()
    publisher = overlay.attach(win, env.hub, ctx, env.ui)
    try:
        ctx.overlay._fallback("sleep", "Time for bed", "Sleep well.")
        env.hub.notify(BlockingMessage("YouTube blocked until 07:00."))
        assert toaster._toaster is None and len(scheduled) == 1
        scheduled.pop()[1]()
        assert len(toaster._toaster.shown) == 1
        text = toaster._toaster.shown[0].text_fields[0]
        assert "Time for bed: Sleep well." in text and "YouTube blocked until 07:00." in text
    finally:
        publisher.stop()
