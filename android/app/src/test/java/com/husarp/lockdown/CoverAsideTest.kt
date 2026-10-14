package com.husarp.lockdown

import com.husarp.lockdown.engine.CoverAside
import com.husarp.lockdown.engine.CoverAside.Do
import com.husarp.lockdown.engine.RemindersEngine
import org.junit.Assert.assertEquals
import org.junit.Test

/** The bedtime screen / strict break stepped aside for "Disable alerts" (the app) or "Phone" (the dialer): not a Dismiss. */
class CoverAsideTest {
    private val launcher = "com.android.launcher3"
    private val browser = "com.android.chrome"
    private val game = "com.example.game"

    /** Stepped aside at t=0 from [from], with 4 of 5 minutes of the Dismiss wait left. */
    private fun disable(from: String = browser, left: Long = 240_000) = CoverAside(CoverAside.DISABLE, left, 0, from)

    @Test fun it_keeps_the_wait_it_had_left() {
        // the wait ends at 300 s; Disable alerts at 60 s: 240 s are kept, and back at 500 s it still asks those 240 s
        val left = CoverAside.leftAt(readyAt = 300_000, nowMs = 60_000)
        assertEquals(240_000L, left)
        val a = disable(left = left)
        assertEquals(Do.BACK, a.front(10_000, inTarget = false, other = game))
        val readyAgain = 500_000 + a.leftMs
        assertEquals(240, RemindersEngine.waitLeft(readyAgain, 500_000))
        assertEquals(0, RemindersEngine.waitLeft(readyAgain, 740_000))
        // a wait already over (or none) stays over, never negative
        assertEquals(0L, CoverAside.leftAt(readyAt = 300_000, nowMs = 400_000))
    }

    @Test fun in_the_app_it_stays_aside() {
        val a = disable()
        assertEquals(Do.STAY, a.front(300, inTarget = true))
        assertEquals(Do.STAY, a.front(60_000, inTarget = true))
        assertEquals(Do.STAY, a.front(15 * 60_000L, inTarget = true))
    }

    @Test fun another_app_after_the_app_was_up_brings_it_back_at_once() {
        val a = disable()
        a.front(500, inTarget = true)
        assertEquals(Do.BACK, a.front(2_000, inTarget = false, other = game))
    }

    @Test fun home_or_back_to_the_app_under_it_brings_it_back_at_once() {
        // the cover was over the home screen: Home from the app is that same "from" app, still leaving
        val home = disable(from = launcher)
        home.front(500, inTarget = true)
        assertEquals(Do.BACK, home.front(1_000, inTarget = false, other = launcher))
        // backing out of the app lands on the app that was under the cover
        val back = disable(from = browser)
        back.front(500, inTarget = true)
        assertEquals(Do.BACK, back.front(1_000, inTarget = false, other = browser))
    }

    @Test fun while_the_app_comes_up_a_late_event_of_the_app_under_it_is_no_leaving() {
        val a = disable(from = browser)
        assertEquals(Do.STAY, a.front(200, inTarget = false, other = browser))
        assertEquals(Do.STAY, a.front(1_000, inTarget = false))
        assertEquals(Do.STAY, a.front(1_200, inTarget = true))
    }

    @Test fun a_new_app_before_the_app_came_up_brings_it_back() {
        // Disable alerts, then Home at once and another app, before Lockdown is even up
        val a = disable(from = browser)
        assertEquals(Do.BACK, a.front(300, inTarget = false, other = launcher))
        assertEquals(Do.BACK, disable(from = browser).front(300, inTarget = false, other = game))
    }

    @Test fun the_app_never_coming_up_brings_it_back() {
        val a = disable()
        assertEquals(Do.STAY, a.front(1_000, inTarget = false))
        assertEquals(Do.STAY, a.front(4_000, inTarget = false))
        assertEquals(Do.BACK, a.front(CoverAside.START_MS, inTarget = false))
    }

    @Test fun a_quick_pause_and_resume_is_let_go_but_staying_away_is_not() {
        val a = disable()
        a.front(500, inTarget = true)
        // Android pausing the app as it brings it to the front again (no other app in front)
        assertEquals(Do.STAY, a.front(10_000, inTarget = false))
        assertEquals(Do.STAY, a.front(10_400, inTarget = true))
        // the app gone (killed, closed) with nothing to name in front: back after a moment
        assertEquals(Do.STAY, a.front(20_000, inTarget = false))
        assertEquals(Do.STAY, a.front(20_000 + CoverAside.AWAY_MS - 1, inTarget = false))
        assertEquals(Do.BACK, a.front(20_000 + CoverAside.AWAY_MS, inTarget = false))
    }

    @Test fun the_screen_off_brings_it_back() {
        val a = disable()
        a.front(500, inTarget = true)
        assertEquals(Do.BACK, a.screenOff())
        assertEquals(240_000L, a.leftMs)
    }

    @Test fun cancelling_during_the_wait_brings_it_back_with_the_wait() {
        assertEquals(Do.BACK, disable(left = 240_000).cancelled(challenge = false))
        assertEquals(Do.BACK, disable(left = 1).cancelled(challenge = true))
    }

    @Test fun cancelling_after_the_wait_is_a_dismiss_unless_the_screen_has_a_challenge() {
        // 0.91.0, as on the PC: the Dismiss that could have been tapped anyway
        assertEquals(Do.DISMISS, disable(left = 0).cancelled(challenge = false))
        // "Challenge before Dismiss": cancelling is no way round it - back to the screen, whose Dismiss asks it
        assertEquals(Do.BACK, disable(left = 0).cancelled(challenge = true))
    }

    @Test fun only_finishing_in_the_app_closes_it() {
        assertEquals(Do.CLOSE, disable().finished())
    }

    @Test fun never_aside_for_ever_even_in_the_app() {
        val a = disable()
        assertEquals(Do.STAY, a.front(CoverAside.MAX_MS - 1, inTarget = true))
        assertEquals(Do.BACK, a.front(CoverAside.MAX_MS, inTarget = true))
    }

    @Test fun a_strict_breaks_phone_steps_aside_only_for_the_dialer() {
        val dialGrace = 10_000L
        fun phone() = CoverAside(CoverAside.PHONE, 0, 0, from = game, startMs = dialGrace)
        // the dialer is slow to start: the grace holds (the app that was under the cover is not "another" app)
        val slow = phone()
        assertEquals(Do.STAY, slow.front(3_000, inTarget = false, other = game))
        assertEquals(Do.STAY, slow.front(6_000, inTarget = true))
        // Home (or another app) right after Phone, before the dialer: back at once - no 10 s free with any app
        assertEquals(Do.BACK, phone().front(500, inTarget = false, other = launcher))
        // out of the dialer to anything else: back at once, also to the app that was under the cover
        val left = phone()
        left.front(800, inTarget = true)
        assertEquals(Do.BACK, left.front(5_000, inTarget = false, other = game))
        // the dialer never came up
        assertEquals(Do.BACK, phone().front(dialGrace, inTarget = false))
    }
}
