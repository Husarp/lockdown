"""R07: the corner window page (ui/corner.html + js/corner.js), headless in Chrome against the real Python Api.

The Corner (resident/corner.py) is the real one, with a recording host in place of the pywebview window; pushes that
Python would make through the Publisher are made here with the `push` fixture (`ld.push`, the same entry point).
What can only be proven on Windows: the window in the work area, never taking the focus, toasts in the Action Center."""
import time
from types import SimpleNamespace

import pytest

from harness import assert_clean
from resident.corner import Corner


class Host:
    def __init__(self):
        self.calls = []

    def show(self):
        self.calls.append(("show",))

    def hide(self):
        self.calls.append(("hide",))

    def place(self, height, dpr=1.0):
        self.calls.append(("place", height, dpr))

    @property
    def placed(self):
        return [c for c in self.calls if c[0] == "place"]


class Env:
    def __init__(self, harness, page, push):
        self.harness, self.page, self._push = harness, page, push
        self.host = Host()
        self.answers = []
        self.corner = Corner(None, self.host, spawn=lambda fn: fn())
        self.corner.popup_sink = lambda key, action: self.answers.append((key, action))
        harness.ctx.corner = self.corner

    def open(self, width=360, height=700):
        self.page.set_viewport_size({"width": width, "height": height})
        self.page.goto(self.harness.url("/corner.html"), wait_until="load")
        self.page.wait_for_function("document.documentElement.dataset.ready === '1'", timeout=10_000)
        return self.page

    def sync(self):
        """What the Publisher would push after the Corner's change."""
        self._push("corner", self.corner.payload())

    def keys(self):
        return self.page.evaluate("[...document.querySelectorAll('#stack > .cc:not(.leaving)')].map(e => e.dataset.key)")

    def wait_keys(self, keys):
        self.page.wait_for_function("(k) => JSON.stringify([...document.querySelectorAll('#stack > .cc:not(.leaving)')]"
                                    ".map(e => e.dataset.key)) === JSON.stringify(k)", arg=keys, timeout=4000)


@pytest.fixture
def corner(harness, page, push):
    return Env(harness, page, push)


def popup(corner, key, text="Drink some water.", buttons=(("Done", "done"), ("Snooze 5 min", "snooze"), ("✕", "dismiss")),
          title="Reminder"):
    corner.corner.popup(key, title, text, list(buttons))


def say(capsys, text):
    with capsys.disabled():
        print("\n  " + text)


def test_three_popups_stack_with_the_newest_lowest(corner):
    page = corner.open()
    popup(corner, "custom:a", "first")
    popup(corner, "custom:b", "second")
    popup(corner, "break", "third", (("Start break", "start"), ("Snooze 5 min", "snooze"), ("✕", "dismiss")), "Time for a break")
    corner.sync()
    corner.wait_keys(["custom:a", "custom:b", "break"])
    tops = page.evaluate("[...document.querySelectorAll('#stack > .cc')].map(e => e.getBoundingClientRect().top)")
    assert tops == sorted(tops) and len(set(tops)) == 3
    assert page.locator("#stack > .cc").last.inner_text().startswith("Time for a break")


def test_a_break_popup_looks_like_one(corner):
    page = corner.open()
    popup(corner, "break", "You've been at the PC for 45 min. Take 5 min away from the screen.\n\nStrict break: 1 snooze left, then it starts on its own.\n\nWhile you're up:\n• Rest your eyes\n• Drink water",
          (("Start break", "start"), ("Snooze 5 min", "snooze"), ("✕", "dismiss")), "Time for a break")
    corner.sync()
    corner.wait_keys(["break"])
    card = page.locator('[data-key="break"]')
    assert card.locator(".cc-h b").inner_text() == "Time for a break"
    assert card.locator(".cc-body li").all_inner_texts() == ["Rest your eyes", "Drink water"]
    assert card.locator(".cc-body p.lead").inner_text() == "While you're up:"
    buttons = card.locator(".cc-btns button")
    assert buttons.all_inner_texts()[:2] == ["Start break", "Snooze 5 min"]
    assert "btn-primary" in buttons.nth(0).get_attribute("class")          # the first button is filled
    assert "btn-primary" not in buttons.nth(1).get_attribute("class")
    dismiss = buttons.nth(2)
    assert dismiss.get_attribute("aria-label") == "Dismiss" and dismiss.bounding_box()["width"] < 50   # small and dark


def test_a_strict_break_card_has_no_x(corner):
    page = corner.open()
    popup(corner, "break", "Strict", (("Start break", "start"),), "Time for a break")
    corner.sync()
    corner.wait_keys(["break"])
    assert page.locator('[data-key="break"] .cc-btns button').count() == 1


def test_clicking_done_sends_one_corner_answer(corner, harness):
    page = corner.open()
    popup(corner, "custom:water")
    corner.sync()
    corner.wait_keys(["custom:water"])
    before = harness.rpc_count("corner_answer")
    page.evaluate("""() => { const b = document.querySelector('[data-key="custom:water"] button[data-action="done"]');
                              b.click(); b.click(); }""")                       # a nervous double click
    page.wait_for_function("document.querySelectorAll('#stack > .cc').length === 0", timeout=4000)
    page.wait_for_timeout(100)
    assert harness.rpc_count("corner_answer") - before == 1
    assert corner.answers == [("custom:water", "done")]                  # Python removed the card, then answered
    assert corner.corner.cards() == [] and corner.host.calls[-1] == ("hide",)


def test_an_error_in_the_corner_page_reaches_the_log(corner, harness):
    page = corner.open()
    before = harness.rpc_count("log_client_error")
    page.evaluate("""() => window.dispatchEvent(new ErrorEvent('error', { message: 'corner broke', filename: 'corner.js', lineno: 3 }))""")
    page.wait_for_timeout(200)
    assert harness.rpc_count("log_client_error") - before == 1                    # (the bridge reports it, as on every page)
    assert any(c[1] == "log_client_error" and "corner broke" in str(c[2]) for c in harness.calls)


def test_the_x_dismisses_through_python(corner, harness):
    page = corner.open()
    popup(corner, "custom:water")
    corner.sync()
    corner.wait_keys(["custom:water"])
    page.locator('[data-key="custom:water"] button[data-action="dismiss"]').click()
    page.wait_for_function("document.querySelectorAll('#stack > .cc').length === 0", timeout=4000)
    assert corner.answers == [("custom:water", "dismiss")]


def test_a_stale_push_does_not_bring_back_a_card_that_was_pressed(corner):
    page = corner.open()
    popup(corner, "custom:water")
    corner.sync()
    corner.wait_keys(["custom:water"])
    stale = corner.corner.payload()
    page.locator('[data-key="custom:water"] button[data-action="done"]').click()
    page.evaluate("(p) => window.ld.push([['corner', p]])", stale)       # Python's list from before the press
    page.wait_for_function("document.querySelectorAll('#stack > .cc').length === 0", timeout=4000)
    assert corner.keys() == []


def test_a_popup_that_comes_back_after_being_dismissed_is_shown_again(corner):
    page = corner.open()
    popup(corner, "custom:water")
    corner.sync()
    corner.wait_keys(["custom:water"])
    page.locator('[data-key="custom:water"] button[data-action="dismiss"]').click()
    page.wait_for_function("document.querySelectorAll('#stack > .cc').length === 0", timeout=4000)
    popup(corner, "custom:water")                                        # (Python re-adds it: a new card id)
    corner.sync()
    corner.wait_keys(["custom:water"])


def test_notices_close_by_themselves_after_8_seconds_and_hold_while_hovered(corner):
    page = corner.page
    page.clock.install()
    corner.open()
    corner.corner.notice("Blocked: YouTube is blocked right now.")
    corner.sync()
    corner.wait_keys([corner.corner.cards()[0]["key"]])
    card = page.locator("#stack > .cc")
    assert card.locator(".cc-h b").inner_text() == "Lockdown" and card.locator(".cc-when").inner_text() == "now"
    page.clock.run_for(7500)
    assert card.count() == 1                                             # not yet
    card.hover()                                                         # the mouse over it: held
    page.clock.run_for(20_000)
    assert card.count() == 1
    page.mouse.move(2, 2)                                                # the mouse leaves
    page.clock.run_for(7500)
    assert card.count() == 1                                             # the 8 s start again after the mouse left
    page.clock.run_for(1000)
    page.clock.run_for(500)
    assert page.locator("#stack > .cc").count() == 0
    assert corner.corner.cards() == [] and corner.host.calls[-1] == ("hide",)   # Python knew, and hid the window


def test_clicking_a_notice_closes_it(corner):
    page = corner.open()
    corner.corner.notice("Passing by")
    corner.sync()
    corner.wait_keys([corner.corner.cards()[0]["key"]])
    page.locator("#stack > .cc .cc-msg").click()
    page.wait_for_function("document.querySelectorAll('#stack > .cc').length === 0", timeout=4000)
    assert corner.corner.cards() == []


def test_a_notice_action_runs_once_and_closes_it(corner):
    ran = []
    page = corner.open()
    corner.corner.notice("Your week is ready", [("See the week", lambda: ran.append(1))])
    corner.sync()
    corner.wait_keys([corner.corner.cards()[0]["key"]])
    page.locator("#stack > .cc button", has_text="See the week").click()
    page.wait_for_function("document.querySelectorAll('#stack > .cc').length === 0", timeout=4000)
    assert ran == [1]


def test_a_sticky_notice_stays_until_its_button_or_x(corner):
    page = corner.open()
    corner.corner.notice("Lockdown 1.2 is out", [("Install", lambda: None), ("Remind me later", lambda: None)], sticky=True)
    corner.sync()
    key = corner.corner.cards()[0]["key"]
    corner.wait_keys([key])
    page.locator("#stack > .cc .cc-msg").click()                          # a click on the text does not close it
    page.wait_for_timeout(400)
    assert corner.keys() == [key]
    page.locator("#stack > .cc .cc-close").click()
    page.wait_for_function("document.querySelectorAll('#stack > .cc').length === 0", timeout=4000)


def test_the_layout_fits_360_px_without_a_sideways_scroll(corner):
    page = corner.open(width=360)
    long_text = "Take a break. " * 30 + "x" * 200
    popup(corner, "custom:a", long_text)
    popup(corner, "custom:b", "• one\n• two\n• three", title="Reminders")
    corner.corner.notice("A notice with a very long word " + "y" * 150, [("Install", lambda: None), ("Remind me later", lambda: None)])
    corner.sync()
    page.wait_for_function("document.querySelectorAll('#stack > .cc').length === 3", timeout=4000)
    sizes = page.evaluate("""() => ({doc: document.documentElement.scrollWidth, view: innerWidth,
        cards: [...document.querySelectorAll('#stack > .cc')].map(e => [e.getBoundingClientRect().right, e.scrollWidth - e.clientWidth])})""")
    assert sizes["doc"] <= 360 and sizes["view"] == 360
    assert all(right <= 360 for right, _over in sizes["cards"]) and all(over <= 0 for _r, over in sizes["cards"])
    assert_clean(page)


def test_the_page_reports_its_height_so_the_window_can_be_cut_to_fit(corner):
    page = corner.open()
    popup(corner, "custom:a")
    corner.sync()
    corner.wait_keys(["custom:a"])
    page.wait_for_function("window.__ldCalls.raf >= 1")
    deadline = time.time() + 3
    while not corner.host.placed and time.time() < deadline:
        time.sleep(0.02)
    height, dpr = corner.host.placed[-1][1:]
    assert dpr == 1 and abs(height - page.evaluate("document.getElementById('stack').getBoundingClientRect().height")) <= 1
    popup(corner, "custom:b", "A longer one. " * 8)
    corner.sync()
    corner.wait_keys(["custom:a", "custom:b"])
    time.sleep(0.2)
    assert corner.host.placed[-1][1] > height


def test_a_card_is_on_screen_within_50_ms_of_the_push(corner, capsys):
    page = corner.open()
    popup(corner, "custom:a")
    payload = corner.corner.payload()
    times = []
    for trial in range(7):          # (a frame is 16.7 ms and the machine has other things to do: the median counts)
        ms = page.evaluate("""async ([payload, trial]) => {
            window.ld.push([['corner', {seq: 100 * trial, cards: []}]]);                  // nothing up
            await new Promise((r) => setTimeout(r, 260));                               // (its leave animation done)
            const cards = payload.cards.map((c) => ({...c, id: c.id + 1000 * trial}));
            const t0 = performance.now();
            window.ld.push([['corner', {seq: 100 * trial + 1, cards}]]);
            const present = !!document.querySelector('#stack > .cc');
            await new Promise((r) => requestAnimationFrame(() => r()));                 // the frame that paints it
            const box = document.querySelector('#stack > .cc').getBoundingClientRect();
            if (!present || box.height <= 0) return -1;
            return performance.now() - t0; }""", [payload, trial])
        times.append(ms)
    times.sort()
    say(capsys, f"card appears {times[len(times) // 2]:.1f} ms after the push (median of 7; best {times[0]:.1f}, worst {times[-1]:.1f})")
    assert times[0] >= 0 and times[len(times) // 2] <= 50


def test_the_whole_push_to_paint_path_with_three_cards(corner, capsys):
    page = corner.open()
    for i in range(3):
        popup(corner, f"custom:{i}", f"reminder {i}")
    payload = corner.corner.payload()
    ms = page.evaluate("""async (payload) => {
        const t0 = performance.now();
        window.ld.push([['corner', payload]]);
        await new Promise((r) => requestAnimationFrame(() => r()));
        await new Promise((r) => requestAnimationFrame(() => r()));
        return performance.now() - t0; }""", payload)
    say(capsys, f"three cards: push to second frame {ms:.1f} ms")
    assert ms < 100


def test_cards_that_were_up_before_the_page_loaded_are_shown(corner):
    popup(corner, "custom:early")
    corner.open()
    corner.wait_keys(["custom:early"])


def test_the_window_follows_the_saved_look(corner, harness):
    import ui_theme
    harness.db.set_setting(ui_theme.THEME_KEY, "light")
    harness.db.set_setting(ui_theme.ACCENT_KEY, "#2F6FEB")
    page = corner.open()
    popup(corner, "custom:a")
    corner.sync()
    corner.wait_keys(["custom:a"])
    assert page.evaluate("document.documentElement.dataset.theme") == "light"
    assert page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--brand').trim().toUpperCase()") == "#2F6FEB"


def test_nothing_is_scheduled_while_no_card_is_up(corner):
    page = corner.open()
    page.wait_for_timeout(300)
    page.evaluate("window.__ldCalls.timeout = window.__ldCalls.interval = window.__ldCalls.raf = 0")
    page.wait_for_timeout(1500)
    calls = page.evaluate("({...window.__ldCalls})")
    assert calls["interval"] == 0 and calls["timeout"] <= 1 and calls["raf"] <= 1


def test_updating_a_card_in_place_keeps_the_others(corner):
    page = corner.open()
    popup(corner, "custom:a", "first")
    popup(corner, "break", "x", (("Start break", "start"),), "Time for a break")
    corner.sync()
    corner.wait_keys(["custom:a", "break"])
    page.evaluate("document.querySelector('[data-key=\"break\"]').dataset.mark = 'kept'")
    popup(corner, "custom:a", "• first\n• second", title="Reminders")        # re-shown whole: now the newest
    corner.sync()
    corner.wait_keys(["break", "custom:a"])
    assert page.evaluate("document.querySelector('[data-key=\"break\"]').dataset.mark") == "kept"   # not rebuilt
    assert page.locator('[data-key="custom:a"] li').count() == 2


def test_an_older_list_arriving_late_is_ignored(corner):
    """corner_ready may answer after a newer push: its (older) list must not take the cards away again."""
    page = corner.open()
    popup(corner, "custom:a")
    corner.sync()
    corner.wait_keys(["custom:a"])
    newest = corner.corner.payload()
    page.evaluate("(p) => window.ld.push([['corner', p]])", {"seq": newest["seq"] - 1, "cards": []})
    page.wait_for_timeout(300)
    assert corner.keys() == ["custom:a"]


def test_the_corner_page_says_ready_after_it_drew_the_cards(corner, harness):
    seen = []
    harness.ctx.on_page_ready = seen.append
    popup(corner, "custom:water")
    page = corner.open()
    page.wait_for_selector("#stack .cc")
    deadline = time.time() + 5
    while time.time() < deadline and not seen:
        time.sleep(0.02)
    assert seen == ["corner"]
    names = [n for _, n, _ in harness.calls]
    assert names.index("corner_ready") < names.index("page_ready")
