"""R08: the full-screen cover page (ui/overlay.html + js/overlay.js), headless in Chrome against the real Python Api.

The Overlay (resident/overlay.py) is the real one with a recording host and a recording answer in place of the pywebview
window and the reminder worker; pushes that Python would make through the Publisher are made here with `env.sync()`
(`ld.push`, the same entry point). Time on the page is Playwright's fake clock where a test counts seconds.
What can only be proven on Windows: the window really filling every monitor on top of everything, the covers on the
other monitors, taking the keyboard, Alt+F4 doing nothing."""
import time
from datetime import datetime, timedelta, timezone

import pytest

import antibypass as ab
import emergency
from harness import assert_clean
from resident.overlay import Overlay

T0 = datetime(2026, 10, 11, 15, 42, 17, tzinfo=timezone.utc)
BEDTIME = [("Dismiss", "dismiss"), ("Disable alerts", "disable"), ("Emergency (3 left)", "emergency")]
BG = {"dark": "rgb(25, 24, 23)", "amoled": "rgb(0, 0, 0)", "light": "rgb(243, 242, 241)"}      # tokens.css --bg


class Host:
    def __init__(self):
        self.calls = []

    def show(self, background):
        self.calls.append(("show", background))

    def hide(self):
        self.calls.append(("hide",))

    def refresh(self):
        pass


class Publisher:
    def publish(self, event, payload=None, key=None):
        pass


class Env:
    def __init__(self, harness, page, push):
        self.harness, self.page, self._push = harness, page, push
        self.host, self.answers, self.t = Host(), [], 1000.0                    # self.t: Python's own steady clock
        self.wait, self.challenge, self.block = 25, None, False          # (the bedtime challenge: {steps, complex, grid} or None)
        self.overlay = Overlay(Publisher(), self.host, lambda key, action: self.answers.append((key, action)),
                               now=lambda: harness.now, wait_for=lambda key: self.wait if key == "sleep" else 0,
                               clock=lambda: self.t, background=lambda: "#191817",
                               block_for=lambda key: self.block, challenge_for=lambda key: self.challenge if key == "sleep" else None)
        harness.ctx.overlay = self.overlay

    def open(self, width=1280, height=800):
        self.page.set_viewport_size({"width": width, "height": height})
        self.page.goto(self.harness.url("/overlay.html"), wait_until="load")
        self.page.wait_for_function("document.documentElement.dataset.ready === '1'", timeout=10_000)
        return self.page

    def sync(self):
        """What the Publisher would push after the Overlay's change."""
        self._push("overlay", self.overlay.payload())

    def bedtime(self, buttons=BEDTIME, text="It's 23:05. Sleep well - the screen can wait until tomorrow."):
        self.overlay.show("sleep", "Time for bed", text, None, buttons)
        self.sync()
        self.page.wait_for_selector("#fs:not([hidden])")

    def strict_break(self, minutes=5, buttons=(("Emergency (3 left)", "emergency"),)):
        self.overlay.show("break-run", "Break time", "Look away.", self.harness.now + timedelta(minutes=minutes), list(buttons))
        self.sync()
        self.page.wait_for_selector("#fs:not([hidden])")

    def freeze(self):
        """Stop the fake clock where it is (call after open(), before the cover is pushed)."""
        now = self.page.evaluate("Date.now()")
        self.page.clock.pause_at(datetime.fromtimestamp(now / 1000 + 5, tz=timezone.utc))

    # -- what is on the page
    def text(self, selector):
        return self.page.inner_text(selector)

    def dismiss(self):
        return self.page.locator('#fs button[data-action="dismiss"]')

    def wait_gone(self):
        self.page.wait_for_selector("#fs", state="hidden", timeout=4000)

    def lock(self, **kw):
        ab.save(self.harness.db, {**ab.settings(self.harness.db), "phrase": True, "length": 30, **kw})


@pytest.fixture
def ov(harness, page, push):
    return Env(harness, page, push)


def say(capsys, text):
    with capsys.disabled():
        print("\n  " + text)


def phrase_of(page):
    return page.evaluate("document.querySelector('#dlg[open] .ch-phrase').textContent")


def type_challenge(page):
    page.wait_for_selector("#dlg[open] textarea.typed", timeout=5000)
    phrase = phrase_of(page)
    page.focus("#dlg textarea.typed")
    page.keyboard.type(phrase, delay=45)
    page.click("#dlg .dlg-f .btn-primary")
    return phrase


# ================================================================ the bedtime screen

def test_bedtime_shows_the_time_the_title_the_text_and_the_buttons(ov, page):
    ov.open()
    ov.bedtime()
    assert ov.text(".fs .hero") == "15:42"                       # the trusted time, not the PC's
    assert ov.text(".fs h1") == "Time for bed"
    assert "Sleep well" in ov.text(".fs .fs-text")
    labels = page.eval_on_selector_all("#fs button", "els => els.map(e => e.textContent.trim())")
    assert labels == ["Dismiss (25)", "Disable alerts", "Emergency (3 left)"]
    assert page.locator('#fs .halo use').get_attribute("href") == "#i-moon"
    assert page.evaluate("document.querySelector('#fs .fs').classList.contains('k-sched')")


def test_the_user_text_and_the_title_are_text_not_markup(ov, page):
    ov.open()
    ov.overlay.show("sleep", "<b>Time</b>", '<img src=x onerror="window.__x=1"> go', None, BEDTIME)
    ov.sync()
    page.wait_for_selector("#fs:not([hidden])")
    assert ov.text(".fs h1") == "<b>Time</b>" and page.evaluate("window.__x") is None
    assert page.locator("#fs img").count() == 0


def test_a_bedtime_screen_with_an_end_shows_a_countdown_instead_of_the_clock(ov, page):
    page.clock.install(time=T0)
    ov.open()
    ov.freeze()
    ov.wait = 0
    ov.overlay.show("sleep", "Time for bed", "x", ov.harness.now + timedelta(seconds=90), BEDTIME)
    ov.sync()
    page.wait_for_selector("#fs:not([hidden])")
    assert ov.text(".fs .hero") == "1:30"
    page.clock.run_for(1020)
    assert ov.text(".fs .hero") == "1:29"


def test_dismiss_counts_down_from_25_and_works_at_0(ov, page):
    page.clock.install(time=T0)
    ov.open()
    ov.freeze()
    ov.bedtime()
    assert ov.text('button[data-action="dismiss"]') == "Dismiss (25)" and ov.dismiss().is_disabled()
    page.clock.run_for(1020)                                                         # (it wakes 15 ms past the second)
    assert ov.text('button[data-action="dismiss"]') == "Dismiss (24)"
    page.clock.run_for(9000)
    assert ov.text('button[data-action="dismiss"]') == "Dismiss (15)" and ov.dismiss().is_disabled()
    page.clock.run_for(14_000)
    assert ov.text('button[data-action="dismiss"]') == "Dismiss (1)" and ov.dismiss().is_disabled()
    page.clock.run_for(1000)
    assert ov.text('button[data-action="dismiss"]') == "Dismiss" and ov.dismiss().is_enabled()
    ov.t += 26                                                                       # (Python's own clock: the wait is over too)
    ov.dismiss().click()
    deadline = time.time() + 3
    while not ov.answers and time.time() < deadline:
        time.sleep(0.02)
    assert ov.answers == [("sleep", "dismiss")]
    ov.sync()
    ov.wait_gone()
    assert ov.host.calls[-1] == ("hide",)


def test_pressing_dismiss_early_changes_nothing_and_python_decides(ov, page):
    """The page's clock says the wait is over, Python's says it is not: Python wins, the number comes back."""
    page.clock.install(time=T0)
    ov.open()
    ov.freeze()
    ov.bedtime()
    page.clock.run_for(26_000)
    assert ov.dismiss().is_enabled()                                                # (the page thinks it is over)
    ov.dismiss().click()
    page.wait_for_function("document.querySelector('button[data-action=dismiss]').textContent.startsWith('Dismiss (')")
    assert ov.dismiss().is_disabled() and ov.answers == []
    assert ov.text('button[data-action="dismiss"]') == "Dismiss (25)"             # Python said ~25 s are left
    page.clock.run_for(25_100)
    assert ov.text('button[data-action="dismiss"]') == "Dismiss"


def test_a_screen_without_a_wait_has_a_plain_dismiss(ov, page):
    ov.wait = 0
    ov.open()
    ov.bedtime()
    assert ov.text('button[data-action="dismiss"]') == "Dismiss" and ov.dismiss().is_enabled()


def test_the_wait_starts_over_each_time_the_screen_comes_up(ov, page):
    page.clock.install(time=T0)
    ov.open()
    ov.freeze()
    ov.bedtime()
    page.clock.run_for(20_020)
    assert ov.text('button[data-action="dismiss"]') == "Dismiss (5)"
    ov.overlay.show("sleep", "Time for bed", "again", None, BEDTIME)                 # dismissed and back on its interval
    ov.sync()
    page.wait_for_function("document.querySelector('.fs-text').textContent === 'again'")
    assert ov.text('button[data-action="dismiss"]') == "Dismiss (25)"


def test_esc_and_the_context_menu_do_nothing(ov, page):
    ov.open()
    ov.bedtime()
    before = ov.harness.rpc_count()
    page.keyboard.press("Escape")
    page.keyboard.press("Alt+F4")
    page.click(".fs .hero", button="right")
    time.sleep(0.2)
    assert page.is_visible("#fs") and ov.harness.rpc_count() == before and ov.answers == []
    assert page.evaluate("document.querySelector('dialog[open]') === null")


def test_a_late_old_push_is_ignored(ov, page, push):
    ov.open()
    ov.bedtime()
    old = ov.overlay.payload()
    ov.overlay.show("sleep", "Time for bed", "newer text", None, BEDTIME)
    ov.sync()
    page.wait_for_function("document.querySelector('.fs-text').textContent === 'newer text'")
    push("overlay", old)
    time.sleep(0.15)
    assert ov.text(".fs-text") == "newer text"


def test_the_window_taking_the_cover_down_empties_the_page(ov, page):
    ov.open()
    ov.bedtime()
    ov.overlay.close("sleep")
    ov.sync()
    ov.wait_gone()
    assert ov.host.calls[-1] == ("hide",)


def test_the_page_asks_for_the_cover_that_is_up_when_it_loads(ov, page):
    ov.overlay.show("sleep", "Time for bed", "already up", None, BEDTIME)
    ov.open()
    page.wait_for_selector("#fs:not([hidden])")
    assert ov.text(".fs-text") == "already up"


def test_the_saved_look_is_applied(ov, page, harness):
    harness.db.set_setting("ui.theme", "light")
    ov.open()
    ov.bedtime()
    page.wait_for_function("document.documentElement.dataset.theme === 'light'")


# ================================================================ Disable alerts: the challenge inside the overlay

def test_disable_alerts_opens_the_challenge_inside_the_overlay_and_then_offers_the_choice(ov, page):
    ov.lock()
    ov.open()
    ov.bedtime()
    page.click('button[data-action="disable"]')
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    inside = page.evaluate("""() => { const d = document.getElementById('dlg'); return d.matches(':modal') && document.body.contains(d) }""")
    assert inside                                                                    # the modal layer of THIS page: nothing can cover it
    assert ov.answers == []
    type_challenge(page)
    page.wait_for_selector(".fs-panel button[data-action='off_tonight']", timeout=5000)
    assert [b.text_content() for b in page.locator(".fs-panel .btn-row button").all()] == ["Off tonight", "Snooze 15 min", "Cancel"]
    assert ov.text(".fs-panel h2") == "Bedtime alerts off" and ov.answers == []     # only a choice so far
    page.click("button[data-action='off_tonight']")
    deadline = time.time() + 3
    while not ov.answers and time.time() < deadline:
        time.sleep(0.02)
    assert ov.answers == [("sleep", "off_tonight")]
    ov.sync()
    ov.wait_gone()


def test_the_snooze_choice_is_15_minutes(ov, page):
    ov.open()
    ov.bedtime()                                                                      # (Anti-Bypass is free: no challenge)
    page.click('button[data-action="disable"]')
    page.wait_for_selector("button[data-action='snooze:15']")
    page.click("button[data-action='snooze:15']")
    deadline = time.time() + 3
    while not ov.answers and time.time() < deadline:
        time.sleep(0.02)
    assert ov.answers == [("sleep", "snooze:15")]


def test_cancelling_the_challenge_brings_the_screen_back_and_the_wait_starts_over(ov, page, harness):
    ov.lock()
    ov.open()
    ov.bedtime()
    ov.t += 9                                                                         # (9 s of the wait are gone)
    page.click('button[data-action="disable"]')
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    page.keyboard.press("Escape")                                                     # Esc closes the challenge (not the cover)
    page.wait_for_selector("#dlg[open]", state="detached", timeout=3000)
    deadline = time.time() + 3
    while ov.overlay.payload()["wait_ms"] != 25_000 and time.time() < deadline:
        time.sleep(0.02)
    assert ov.overlay.payload()["wait_ms"] == 25_000 and ov.answers == []             # Python started the wait over
    ov.sync()
    page.wait_for_function("document.querySelector('button[data-action=dismiss]').textContent === 'Dismiss (25)'")
    assert page.is_visible("#fs") and ov.dismiss().is_disabled()


def test_cancel_in_the_choice_during_the_wait_is_no_dismiss(ov, page):
    ov.open()
    ov.bedtime()
    page.click('button[data-action="disable"]')
    page.wait_for_selector("button[data-action='cancel']")
    page.click("button[data-action='cancel']")
    page.wait_for_selector('#fs button[data-action="dismiss"]')
    assert ov.answers == [] and page.is_visible("#fs")
    assert page.locator(".fs-panel").is_hidden()


def test_cancel_in_the_choice_after_the_wait_dismisses(ov, page):
    ov.wait = 0
    ov.open()
    ov.bedtime()
    page.click('button[data-action="disable"]')
    page.click("button[data-action='cancel']")
    deadline = time.time() + 3
    while not ov.answers and time.time() < deadline:
        time.sleep(0.02)
    assert ov.answers == [("sleep", "dismiss")]


def test_a_challenge_the_tray_asks_for_opens_on_the_cover(ov, page, push):
    ov.lock()
    ov.open()
    ov.bedtime()
    ticket = ov.harness.ctx.guard.request(["Quit Lockdown"], lambda: None)["data"]["ticket"]
    push("challenge_open", {"needs_challenge": True, "ticket": ticket, "changes": ["Quit Lockdown"], "more": 0})
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    assert "Quit Lockdown" in page.inner_text("#dlg")
    page.keyboard.press("Escape")


# ================================================================ Challenge before Dismiss: inside the cover

PHRASE_STEP = [{"id": "p1", "kind": "phrase", "level": 30, "times": 1}]
MATH_STEP = [{"id": "m1", "kind": "math", "level": 0, "times": 2}]


def answers_soon(ov, count=1, timeout=3):
    deadline = time.time() + timeout
    while len(ov.answers) < count and time.time() < deadline:
        time.sleep(0.02)
    return ov.answers


def test_the_screen_says_what_dismiss_will_ask_and_nothing_without_a_challenge(ov, page):
    ov.open()
    ov.bedtime()
    assert page.locator(".fs-asks").is_hidden()
    ov.challenge = {"steps": MATH_STEP, "complex": False, "grid": False}
    ov.bedtime()
    assert ov.text(".fs-asks") == "After the wait, Dismiss asks: Math (Easy) ×2."
    ov.challenge = None
    ov.bedtime()
    assert page.locator(".fs-asks").is_hidden()


def test_dismiss_after_the_wait_opens_the_challenge_inside_the_cover_and_passing_dismisses_once(ov, page):
    ov.wait = 0
    ov.challenge = {"steps": PHRASE_STEP, "complex": False, "grid": False}
    ov.open()
    ov.bedtime()
    ov.dismiss().click()
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    inside = page.evaluate("""() => { const d = document.getElementById('dlg'); return d.matches(':modal') && document.body.contains(d) }""")
    assert inside and ov.answers == []                                                 # nothing is dismissed by asking
    assert "Solve this to dismiss" in page.inner_text("#dlg .dlg-h")
    body = page.inner_text("#dlg")
    assert "Passing dismisses the bedtime screen once." in body and "unlock" not in body.lower()
    type_challenge(page)
    assert answers_soon(ov) == [("sleep", "dismiss")]
    ov.sync()
    ov.wait_gone()


def test_the_bedtime_challenge_can_be_typed_while_the_cover_blocks_the_clicks_behind_it(ov, page):
    ov.wait, ov.block = 0, True
    ov.challenge = {"steps": PHRASE_STEP, "complex": False, "grid": False}
    ov.open()
    ov.bedtime()
    assert ov.overlay.blocking
    ov.dismiss().click()
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    type_challenge(page)                                                                # (the real window keeps the keyboard: Windows only)
    assert answers_soon(ov) == [("sleep", "dismiss")]


def test_a_math_step_opens_the_picture_and_the_right_number_dismisses(ov, page, harness):
    ov.wait = 0
    ov.challenge = {"steps": MATH_STEP, "complex": False, "grid": False}
    ov.open()
    ov.bedtime()
    ov.dismiss().click()
    page.wait_for_selector("#dlg[open] canvas.ch-pic", timeout=5000)
    assert page.locator("#dlg .ch-pic").count() == 1 and "Math" in page.inner_text("#dlg")
    for _ in range(2):
        page.wait_for_selector("#dlg[open] input.ch-ans", timeout=5000)
        time.sleep(1.4)                                                                # (a sum answered in under 1.2 s is refused)
        page.fill("#dlg input.ch-ans", str(harness.ctx.guard._open.run.puzzle.answer))
        page.press("#dlg input.ch-ans", "Enter")
        time.sleep(0.2)
    assert answers_soon(ov) == [("sleep", "dismiss")]


def test_cancelling_the_bedtime_challenge_leaves_the_screen_and_dismisses_nothing(ov, page):
    ov.wait = 0
    ov.challenge = {"steps": PHRASE_STEP, "complex": False, "grid": False}
    ov.open()
    ov.bedtime()
    ov.dismiss().click()
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    page.keyboard.press("Escape")
    page.wait_for_selector("#dlg[open]", state="detached", timeout=3000)
    assert page.is_visible("#fs") and ov.answers == []
    ov.dismiss().click()                                                               # pressing again asks again
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    page.keyboard.press("Escape")


def test_an_early_press_asks_no_challenge_and_the_wait_still_wins(ov, page):
    ov.challenge = {"steps": PHRASE_STEP, "complex": False, "grid": False}
    ov.open()
    ov.bedtime()
    ov.t += 5
    page.evaluate("document.querySelector('button[data-action=dismiss]').disabled = false")      # (a page forced past the wait)
    ov.dismiss().click()
    page.wait_for_function("document.querySelector('button[data-action=dismiss]').disabled === true", timeout=3000)
    assert page.locator("#dlg[open]").count() == 0 and ov.answers == []


def test_cancel_in_the_choice_after_the_wait_goes_back_when_the_screen_has_a_challenge(ov, page):
    ov.wait = 0
    ov.challenge = {"steps": PHRASE_STEP, "complex": False, "grid": False}
    ov.open()
    ov.bedtime()
    page.click('button[data-action="disable"]')
    page.click("button[data-action='cancel']")
    page.wait_for_selector('#fs button[data-action="dismiss"]')
    time.sleep(0.3)
    assert ov.answers == [] and page.is_visible("#fs") and page.locator(".fs-panel").is_hidden()


def test_cancel_in_the_emergency_list_after_the_wait_goes_back_when_the_screen_has_a_challenge(ov, page):
    ov.wait = 0
    ov.challenge = {"steps": PHRASE_STEP, "complex": False, "grid": False}
    ov.open()
    ov.bedtime()
    page.click('button[data-action="emergency"]')
    page.wait_for_selector(".fs .kinds .rcard, .fs-panel button[data-action='cancel']")
    page.click(".fs-panel button[data-action='cancel']")
    page.wait_for_selector('#fs button[data-action="dismiss"]')
    time.sleep(0.3)
    assert ov.answers == [] and page.is_visible("#fs")


@pytest.mark.parametrize("size", [(1280, 800), (800, 600), (640, 480)])
def test_the_bedtime_challenge_fits_inside_the_cover(ov, page, size):
    ov.wait = 0
    ov.challenge = {"steps": PHRASE_STEP, "complex": False, "grid": False}
    ov.open(*size)
    ov.bedtime()
    ov.dismiss().click()
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    box = page.evaluate("""() => { const r = document.getElementById('dlg').getBoundingClientRect();
        return {t: r.top, b: r.bottom, l: r.left, r: r.right, w: innerWidth, h: innerHeight} }""")
    assert box["t"] >= 0 and box["l"] >= 0 and box["b"] <= box["h"] and box["r"] <= box["w"], box
    page.keyboard.press("Escape")


# ================================================================ Emergency

TIERS = [{"id": "quick", "uses": 1, "per": "day", "minutes": 5}, {"id": "work", "uses": 2, "per": "week", "minutes": 30},
         {"id": "real", "uses": 1, "per": "month", "minutes": 60}]


def test_the_emergency_list_shows_uses_left_and_greys_out_the_spent_kinds(ov, page, harness):
    emergency.save_tiers(harness.db, TIERS)
    emergency.unlock(harness.db, [], harness.now - timedelta(hours=2), alerts=True, tier="quick")
    ov.open()
    ov.bedtime()
    page.click('button[data-action="emergency"]')
    page.wait_for_selector(".fs .kinds .rcard")
    cards = page.locator(".fs .kinds .rcard")
    assert cards.count() == 3
    assert [c.inner_text().replace("\n", " | ") for c in cards.all()] == [
        "5 min | none left · resets " + page.evaluate("document.querySelector('.rcard .small').textContent.split('resets ')[1]"),
        "30 min | 2 of 2 left this week", "60 min | 1 of 1 left this month"]
    inputs = page.locator(".fs .kinds input")
    assert [inputs.nth(i).is_disabled() for i in range(3)] == [True, False, False]
    assert [inputs.nth(i).is_checked() for i in range(3)] == [False, True, False]     # the first usable one is picked
    assert page.locator("button[data-action='use']").is_enabled()


def test_picking_a_kind_and_using_it_spends_it_in_python_and_takes_the_cover_down(ov, page, harness):
    emergency.save_tiers(harness.db, TIERS)
    ov.open()
    ov.bedtime()
    page.click('button[data-action="emergency"]')
    page.wait_for_selector(".fs .kinds .rcard")
    page.locator(".fs .kinds .rcard").nth(2).click()
    assert page.locator(".fs .kinds input").nth(2).is_checked()
    page.click("button[data-action='use']")
    deadline = time.time() + 3
    while not ov.answers and time.time() < deadline:
        time.sleep(0.02)
    assert ov.answers == [("sleep", "emergency:real")]                                # the kind the person picked, by id
    assert not ov.overlay.shown
    ov.sync()
    ov.wait_gone()


def test_with_every_kind_spent_nothing_can_be_used(ov, page, harness):
    emergency.save_tiers(harness.db, TIERS[:1])
    emergency.unlock(harness.db, [], harness.now - timedelta(hours=2), alerts=True, tier="quick")
    ov.open()
    ov.bedtime()
    page.click('button[data-action="emergency"]')
    page.wait_for_selector(".fs .kinds .rcard")
    assert page.locator("button[data-action='use']").is_disabled()
    assert page.locator(".fs .kinds input:checked").count() == 0


def test_a_kind_the_page_invents_is_refused_by_python_and_the_error_is_shown(ov, page, harness):
    ov.open()
    ov.bedtime()
    page.click('button[data-action="emergency"]')
    page.wait_for_selector(".fs .kinds .rcard")
    page.evaluate("document.querySelector('.fs .kinds input').value = 'nope'")
    page.click("button[data-action='use']")
    page.wait_for_selector(".fs-err")
    assert "gone" in ov.text(".fs-err") and ov.answers == []


def test_cancel_in_the_emergency_list_during_the_wait_returns_to_the_screen_with_a_fresh_wait(ov, page):
    ov.open()
    ov.bedtime()
    page.click('button[data-action="emergency"]')
    page.wait_for_selector("button[data-action='cancel']")
    page.click("button[data-action='cancel']")
    page.wait_for_selector('#fs button[data-action="dismiss"]')
    assert ov.answers == [] and page.is_visible("#fs")
    ov.sync()
    assert ov.dismiss().is_disabled()


# ================================================================ the strict break

def test_a_strict_break_shows_a_countdown_and_no_skip_or_snooze_or_dismiss(ov, page):
    page.clock.install(time=T0)
    ov.open()
    ov.freeze()
    ov.strict_break(5)
    assert ov.text(".fs h1") == "Break time" and ov.text(".fs .hero") == "5:00"
    assert page.locator('#fs button[data-action="dismiss"]').count() == 0
    labels = page.eval_on_selector_all("#fs button", "els => els.map(e => e.textContent.trim())")
    assert labels == ["Emergency (3 left)"]
    assert not [l for l in labels if "Skip" in l or "Snooze" in l]
    assert page.locator('#fs .halo use').get_attribute("href") == "#i-coffee"
    page.clock.run_for(1020)
    assert ov.text(".fs .hero") == "4:59"
    page.clock.run_for(298_000)
    assert ov.text(".fs .hero") == "0:01"
    page.clock.run_for(1000)
    assert ov.text(".fs .hero") == "0:00"
    page.clock.run_for(60_000)
    assert ov.text(".fs .hero") == "0:00"                                             # (Python takes the cover down; the page just stops)


def test_a_strict_break_without_emergency_has_no_buttons_at_all(ov, page):
    ov.open()
    ov.strict_break(5, buttons=())
    assert page.locator("#fs button").count() == 0


def test_emergency_on_the_break_cover_works_and_cancel_comes_back_to_the_countdown(ov, page, harness):
    ov.open()
    ov.strict_break(5)
    page.click('button[data-action="emergency"]')
    page.wait_for_selector("button[data-action='cancel']")
    page.click("button[data-action='cancel']")
    page.wait_for_selector('#fs button[data-action="emergency"]')
    assert ov.answers == [] and ov.text(".fs h1") == "Break time"
    page.click('button[data-action="emergency"]')
    page.wait_for_selector("button[data-action='use']")
    page.click("button[data-action='use']")
    deadline = time.time() + 3
    while not ov.answers and time.time() < deadline:
        time.sleep(0.02)
    assert ov.answers == [("sleep", "emergency:t1")]


def test_dismiss_on_the_break_cover_is_a_button_that_takes_it_down(ov, page):
    ov.open()
    ov.strict_break(5, buttons=(("Emergency (3 left)", "emergency"), ("Dismiss", "dismiss")))
    labels = page.eval_on_selector_all("#fs button", "els => els.map(e => e.textContent.trim())")
    assert labels == ["Dismiss", "Emergency (3 left)"] and ov.text(".fs h1") == "Break time"
    assert not page.locator('#fs button[data-action="dismiss"]').is_disabled()          # (a break cover has no Dismiss wait)
    page.click('#fs button[data-action="dismiss"]')
    deadline = time.time() + 3
    while not ov.answers and time.time() < deadline:
        time.sleep(0.02)
    assert ov.answers == [("break-run", "dismiss")]


# ================================================================ cheap: no timers while hidden, nothing animating

def test_nothing_is_animated_and_nothing_ticks_once_the_cover_is_down(ov, page, perf):
    ov.wait = 600
    ov.open()
    ov.bedtime()
    assert page.evaluate("document.getAnimations().length") == 0
    ov.overlay.close("sleep")                                                         # Python hides the window and says so
    ov.sync()
    ov.wait_gone()
    perf.reset_timer_calls()
    time.sleep(1.6)
    calls = perf.timer_calls()
    assert calls["timeout"] == 0 and calls["interval"] == 0 and calls["raf"] == 0, calls


def test_the_wait_keeps_counting_even_if_the_page_wrongly_thinks_it_is_hidden(ov, page, perf):
    """Python decides when the window is hidden (it says so with a push): a wrong visibilityState must never leave
    Dismiss off for good."""
    ov.wait = 3
    ov.open()
    perf.set_hidden(True)
    ov.bedtime()
    page.wait_for_function("document.querySelector('button[data-action=dismiss]').textContent === 'Dismiss'", timeout=6000)
    assert ov.dismiss().is_enabled()


def test_a_bedtime_screen_with_no_wait_wakes_once_a_minute_at_most(ov, page, perf):
    ov.wait = 0
    ov.open()
    ov.bedtime()
    perf.reset_timer_calls()
    time.sleep(2.0)
    assert perf.timer_calls()["timeout"] == 0                                         # the next change is a minute away


def test_the_bedtime_clock_moves_on_by_itself(ov, page):
    page.clock.install(time=T0)
    ov.open()
    ov.freeze()
    ov.wait = 0
    ov.bedtime()
    assert ov.text(".fs .hero") == "15:42"                                            # (15:42:17 when it came up)
    page.clock.run_for(43_000)
    assert ov.text(".fs .hero") == "15:42"                                            # 43 s later it is 15:43:00 ... just before the change
    page.clock.run_for(100)
    assert ov.text(".fs .hero") == "15:43"
    page.clock.run_for(60_000)
    assert ov.text(".fs .hero") == "15:44"


# ================================================================ looks

CASES = [(1280, 800), (1000, 650)]


def overflow(page):
    return page.evaluate("""() => { const d = document.documentElement, b = document.body.getBoundingClientRect();
        return {h: d.scrollHeight - d.clientHeight, w: d.scrollWidth - d.clientWidth, bodyH: document.body.scrollHeight - document.body.clientHeight,
                bg: getComputedStyle(document.body).backgroundColor} }""")


@pytest.mark.parametrize("size", CASES)
@pytest.mark.parametrize("theme", ["dark", "amoled", "light"])
def test_every_state_fits_the_screen_in_every_theme(ov, page, harness, size, theme):
    emergency.save_tiers(harness.db, TIERS)
    harness.db.set_setting("ui.theme", theme)                                         # (the saved look, as the agent has it)
    ov.open(*size)
    page.wait_for_function("(t) => document.documentElement.dataset.theme === t", arg=theme)
    ov.bedtime(text="It's 23:05. Sleep well - the screen can wait until tomorrow. " * 3)
    states = {"bedtime": lambda: None,
              "choice": lambda: (page.click('button[data-action="disable"]'), page.wait_for_selector("button[data-action='snooze:15']")),
              "emergency": lambda: (page.click("button[data-action='cancel']"), ov.sync(), page.wait_for_selector('button[data-action="emergency"]'),
                                    page.click('button[data-action="emergency"]'), page.wait_for_selector(".fs .kinds .rcard"))}
    for name, go in states.items():
        go()
        o = overflow(page)
        assert o["h"] <= 0 and o["w"] <= 0, (name, size, theme, o)
        assert o["bg"] == BG[theme], (name, theme, o)                                 # the cover colour is solid, no transparency
    ov.strict_break(5)
    o = overflow(page)
    assert o["h"] <= 0 and o["w"] <= 0 and o["bg"] == BG[theme], (size, theme, o)


@pytest.mark.parametrize("size", CASES)
def test_the_challenge_fits_inside_the_overlay_too(ov, page, harness, size):
    ov.lock(length=200)
    ov.open(*size)
    ov.bedtime()
    page.click('button[data-action="disable"]')
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    box = page.evaluate("""() => { const r = document.getElementById('dlg').getBoundingClientRect();
        return {t: r.top, b: r.bottom, l: r.left, r: r.right, w: innerWidth, h: innerHeight} }""")
    assert box["t"] >= 0 and box["l"] >= 0 and box["b"] <= box["h"] and box["r"] <= box["w"], box
    page.keyboard.press("Escape")


def test_a_cover_with_the_most_kinds_still_scrolls_instead_of_being_cut_off(ov, page, harness):
    emergency.save_tiers(harness.db, [{"id": f"k{i}", "uses": 2, "per": "week", "minutes": 5 + i * 5} for i in range(5)])
    ov.open(1000, 650)
    ov.bedtime()
    page.click('button[data-action="emergency"]')
    page.wait_for_selector(".fs .kinds .rcard")
    assert page.locator(".fs .kinds .rcard").count() == 5
    reach = page.evaluate("""() => { const b = document.querySelector("button[data-action='use']"); b.scrollIntoView();
        const r = b.getBoundingClientRect(); return r.bottom <= innerHeight && r.top >= 0 }""")
    assert reach


# ================================================================ speed

def test_the_cover_is_drawn_within_a_frame_or_two_of_the_push(ov, page, capsys):
    ov.open()
    times = []
    for _ in range(5):
        ov.overlay.close("sleep")
        ov.sync()
        ov.wait_gone()
        ov.overlay.show("sleep", "Time for bed", "x", None, BEDTIME)
        t = page.evaluate("""async (p) => {
            const t0 = performance.now(); window.ld.push([['overlay', p]]);
            await new Promise((r) => requestAnimationFrame(() => r()));
            await new Promise((r) => requestAnimationFrame(() => r()));
            return [performance.now() - t0, document.getElementById('fs').hidden] }""", ov.overlay.payload())
        assert t[1] is False
        times.append(t[0])
    say(capsys, f"overlay: push -> second frame after it: {['%.1f' % x for x in times]} ms (best {min(times):.1f}, worst {max(times):.1f})")
    assert max(times) < 120
    first = page.evaluate("performance.getEntriesByName('first-contentful-paint')[0]?.startTime ?? -1")
    say(capsys, f"overlay.html: first contentful paint {first:.0f} ms (hidden pre-created window: this is paid once at start)")
    assert_clean(page)


# ================================================================ the ready handshake (0.85.1's black screen)

def test_a_cover_due_before_the_page_loaded_is_shown_only_after_the_page_drew_it(harness, page):
    """The agent's Overlay waits for the page's page_ready (sent after it drew what overlay_ready gave it) before the
    window is shown - a cover shown earlier is a solid dark screen."""
    host = Host()
    cover = Overlay(Publisher(), host, lambda key, action: None, now=lambda: harness.now, wait_for=lambda key: 0,
                    background=lambda: "#191817", page_ready=False)
    harness.ctx.overlay = cover
    harness.ctx.on_page_ready = lambda name: name == "overlay" and cover.page_loaded()
    cover.show("sleep", "Time for bed", "Sleep well.", None, BEDTIME)          # due at start, before the page is there
    assert host.calls == []
    page.goto(harness.url("/overlay.html"), wait_until="load")
    page.wait_for_selector("#fs:not([hidden])")
    deadline = time.time() + 5
    while time.time() < deadline and not host.calls:
        time.sleep(0.02)
    assert host.calls == [("show", "#191817")]
    assert "Time for bed" in page.inner_text("#fs")                            # (drawn before the window came up)
    names = [n for _, n, _ in harness.calls]
    assert names.index("overlay_ready") < names.index("page_ready")


@pytest.mark.allow_console_errors
def test_a_script_that_fails_to_load_reaches_the_log(harness, page):
    """The early error hook in <head> reports what the page's own modules can't: here overlay.js itself never loads."""
    page.route("**/js/overlay.js", lambda route: route.fulfill(status=404, body="no"))
    page.goto(harness.url("/overlay.html"), wait_until="load")
    deadline = time.time() + 5
    while time.time() < deadline and not harness.rpc_count("log_client_error"):
        time.sleep(0.02)
    logged = [str(a) for _, n, a in harness.calls if n == "log_client_error"]
    assert any("/overlay.html: could not load" in m and "overlay.js" in m for m in logged), logged
    assert "page_ready" not in [n for _, n, _ in harness.calls]                # (so the window would never be shown)
