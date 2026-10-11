package com.husarp.lockdown

import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.Guardrails
import com.husarp.lockdown.data.GuardrailsEdit
import com.husarp.lockdown.engine.Cadence
import com.husarp.lockdown.engine.HelperKeep
import com.husarp.lockdown.engine.HelperWatch
import com.husarp.lockdown.engine.HelperWatch.Act
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** B2: the Island helper can be stopped - main's watch on it, the helper's own way back, and the two B1 follow-ups. */
class HelperStopTest {
    private val min = 60_000L

    /** Drives [HelperWatch.look] like the service's minute tick: a look every [every] ms for [looks] looks. */
    private class Run(var s: HelperWatch.State = HelperWatch.State(), var now: Long = 1_000_000L, var heard: Long = 0L) {
        val acts = ArrayList<Pair<Long, Act>>()
        fun look(screenOn: Boolean = true, watching: Boolean = true): Act {
            val out = HelperWatch.look(s, now, heard, screenOn, watching)
            s = out.state
            if (out.act != Act.NONE) acts.add(now to out.act)
            return out.act
        }
        fun advance(ms: Long) { now += ms }
    }

    // ---------- main: a running helper is never "silent" ----------

    @Test fun a_helper_that_keeps_checking_in_never_raises_anything() {
        val r = Run()
        repeat(200) { r.heard = r.now - 20_000; r.look(); r.advance(min) }
        assertTrue(r.acts.isEmpty())
    }

    @Test fun a_stopped_helper_is_silent_after_two_minutes_of_screen_time() {
        val r = Run()
        r.heard = 5
        assertEquals(Act.NONE, r.look()); r.advance(min)       // 0 min quiet
        assertEquals(Act.NONE, r.look()); r.advance(min)       // 1
        assertEquals(Act.RAISE, r.look())                      // 2
        assertTrue(r.s.silent)
    }

    @Test fun raised_once_and_repeated_every_five_minutes_while_the_screen_is_on() {
        val r = Run(); r.heard = 5
        repeat(3) { r.look(); r.advance(min) }                 // raised at the third look
        val raisedAt = r.acts.single().first
        val repeats = ArrayList<Long>()
        repeat(12) { if (r.look() == Act.REPEAT) repeats.add(r.now); r.advance(min) }
        assertEquals(listOf(raisedAt + 5 * min, raisedAt + 10 * min), repeats)
    }

    @Test fun the_alert_is_not_repeated_while_the_screen_is_off() {
        val r = Run(); r.heard = 5
        repeat(3) { r.look(); r.advance(min) }
        r.advance(30 * min)
        assertEquals(Act.NONE, r.look(screenOn = false))
        assertTrue(r.s.silent)                                  // still an open episode
    }

    @Test fun screen_off_time_is_never_quiet_time() {
        val r = Run(); r.heard = 5
        r.look(); r.advance(30_000); r.look()                   // 30 s of quiet, screen on
        r.advance(10_000); r.look(screenOn = false)             // screen goes off: the interval ending here counts (40 s in all)
        r.advance(9 * 60 * min)                                 // the night (Doze: nothing runs)
        assertEquals(Act.NONE, r.look(screenOn = false))
        r.advance(1); assertEquals(Act.NONE, r.look())          // morning, screen on: 40 s in the bank, not 9 hours
        assertFalse(r.s.silent)
        assertEquals(40_000L, r.s.quietOnMs)
        r.advance(80_000); assertEquals(Act.RAISE, r.look())    // 80 s more of screen time: 2 min in all
    }

    @Test fun short_bursts_of_screen_time_add_up_to_silence() {
        val r = Run(); r.heard = 5
        var raised = false
        repeat(4) {                                             // four 40 s sessions with long screen-off gaps
            r.look(screenOn = true)
            r.advance(40_000)
            if (r.look(screenOn = false) == Act.RAISE) raised = true
            r.advance(30 * min)
        }
        assertTrue(raised)
        assertEquals(1, r.acts.count { it.second == Act.RAISE })
        assertEquals(1_000_000L + 2 * (30 * min + 40_000L) + 40_000L, r.acts.first().first)   // the third burst: 3 x 40 s = 2 min
    }

    @Test fun a_screen_session_of_under_a_minute_counts_and_a_look_after_a_long_gap_does_not() {
        val r = Run(); r.heard = 5
        r.look(); r.advance(60_000); r.look(screenOn = false)
        assertEquals(60_000L, r.s.quietOnMs)
        r.advance(10 * min); r.look(screenOn = true)            // screen was off at the last look: nothing from the sleep
        assertEquals(60_000L, r.s.quietOnMs)
        r.advance(2 * min); r.look(screenOn = false)            // on at the last look, but the gap is over the limit
        assertEquals(60_000L, r.s.quietOnMs)
    }

    @Test fun a_look_after_a_long_gap_with_the_screen_on_at_both_ends_adds_nothing() {
        val r = Run(); r.heard = 5
        r.look(); r.advance(min); r.look()                      // 1 min quiet
        r.advance(8 * 60 * min)                                 // the phone slept, and no screen broadcast was seen
        assertEquals(Act.NONE, r.look())
        assertFalse(r.s.silent)
        assertEquals(min, r.s.quietOnMs)
    }

    @Test fun switching_the_screen_off_and_on_does_not_reset_the_quiet_time() {
        val r = Run(); r.heard = 5
        r.look(); r.advance(min); r.look()                      // 1 min
        r.advance(10_000); r.look(screenOn = false)             // off (the 10 s before it count: 70 s)
        r.advance(10_000); r.look()                             // on again (no time added for the off interval)
        r.advance(min); r.look()                                // over 2 min of screen time in total
        assertTrue(r.s.silent)
    }

    @Test fun the_helper_checking_in_ends_the_episode_and_the_next_silence_raises_again() {
        val r = Run(); r.heard = 5
        repeat(3) { r.look(); r.advance(min) }
        assertEquals(Act.RAISE, r.acts.single().second)
        r.heard = r.now - 1000                                  // it is back
        assertEquals(Act.CLEAR, r.look())
        assertFalse(r.s.silent)
        r.advance(min)
        repeat(3) { r.look(); r.advance(min) }                  // stopped again
        assertEquals(listOf(Act.RAISE, Act.CLEAR, Act.RAISE), r.acts.map { it.second })
    }

    @Test fun an_episode_open_when_the_process_died_is_not_counted_again() {
        val r = Run(s = HelperWatch.resumed(0)); r.heard = 0
        assertEquals(Act.REPEAT, r.look())                      // the alert comes back, nothing is raised again
        r.advance(min)
        assertEquals(Act.NONE, r.look())
        r.heard = 42                                            // the helper is heard
        assertEquals(Act.CLEAR, r.look())
    }

    @Test fun nothing_is_watched_unless_linked_enabled_and_not_paused() {
        val r = Run(); r.heard = 5
        repeat(5) { r.look(watching = false); r.advance(min) }
        assertTrue(r.acts.isEmpty())
        assertEquals(0L, r.s.quietOnMs)
        repeat(3) { r.look(); r.advance(min) }
        assertEquals(Act.RAISE, r.acts.single().second)
        assertEquals(Act.CLEAR, r.look(watching = false))       // the setting went off / Island was paused / unlinked
    }

    @Test fun a_new_silence_waits_out_the_start_up_grace_but_an_open_episode_does_not() {
        val grace = HelperWatch.GRACE_MS
        assertFalse(HelperWatch.watchedNow(true, episodeOpen = false, now = 1_000, graceUntil = 1_000 + grace))
        assertTrue(HelperWatch.watchedNow(true, episodeOpen = false, now = 1_000 + grace, graceUntil = 1_000 + grace))
        assertTrue(HelperWatch.watchedNow(true, episodeOpen = true, now = 1_000, graceUntil = 1_000 + grace))
        assertFalse(HelperWatch.watchedNow(false, episodeOpen = true, now = 5_000_000, graceUntil = 0))
        // In a run: nothing counts during the grace, then two minutes of screen time raise it - a helper that never
        // comes up is still caught, only later.
        val r = Run(); r.heard = 5
        val until = r.now + grace
        var raisedAt = -1L
        repeat(10) {
            val w = HelperWatch.watchedNow(true, r.s.silent, r.now, until)
            if (r.look(watching = w) == Act.RAISE) raisedAt = r.now
            r.advance(min)
        }
        assertTrue(raisedAt >= until + 2 * min)
    }

    @Test fun times_reads_naturally() {
        assertEquals("once", HelperWatch.times(1))
        assertEquals("twice", HelperWatch.times(2))
        assertEquals("5 times", HelperWatch.times(5))
    }

    // ---------- helper: when something tries to bring the service back ----------

    @Test fun the_watchdog_starts_a_linked_helpers_stopped_service_only() {
        assertTrue(HelperKeep.shouldStart(isHelper = true, serviceAlive = false))
        assertFalse(HelperKeep.shouldStart(isHelper = true, serviceAlive = true))
        assertFalse(HelperKeep.shouldStart(isHelper = false, serviceAlive = false))   // main / unlinked: nothing to start
    }

    @Test fun the_watchdog_runs_every_fifteen_minutes() {
        assertEquals(15L, Cadence.HELPER_WATCHDOG_MIN)           // WorkManager's shortest period: no tight loop
    }

    // ---------- the setting: turning it off is a loosening ----------

    @Test fun the_island_alert_is_on_by_default_and_old_files_get_it_on() {
        assertTrue(Guardrails().helperAlert)
        val old = kotlinx.serialization.json.Json { ignoreUnknownKeys = true }
            .decodeFromString<Guardrails>("""{"uninstallProtection":false,"persist":true,"trustedTime":true}""")
        assertTrue(old.helperAlert)
    }

    @Test fun turning_the_island_alert_off_needs_the_challenge_and_turning_it_on_does_not() {
        val on = GuardrailsEdit.of(Config())
        val off = on.copy(guardrails = on.guardrails.copy(helperAlert = false))
        assertTrue(off.looser(on))
        assertFalse(on.looser(off))
        assertFalse(on.looser(on))
    }

    @Test fun the_island_alert_switch_shows_as_an_unsaved_change_in_switches() {
        val on = GuardrailsEdit.of(Config())
        assertEquals(setOf("switches"), on.copy(guardrails = on.guardrails.copy(helperAlert = false)).changedSections(on))
    }

    // ---------- B1 follow-ups ----------

    @Test fun only_screen_on_and_off_close_the_usage_gap_not_the_unlock() {
        assertTrue(Cadence.closesUsageGap("android.intent.action.SCREEN_ON"))
        assertTrue(Cadence.closesUsageGap("android.intent.action.SCREEN_OFF"))
        assertFalse(Cadence.closesUsageGap("android.intent.action.USER_PRESENT"))
        assertFalse(Cadence.closesUsageGap(null))
    }

    @Test fun the_step_loop_is_brought_back_to_the_quick_pace_when_the_screen_is_on() {
        assertTrue(Cadence.rearmSteps(Cadence.STEP_OFF_MS, screenOn = true))
        assertFalse(Cadence.rearmSteps(Cadence.STEP_MS, screenOn = true))        // already quick
        assertFalse(Cadence.rearmSteps(Cadence.STEP_OFF_MS, screenOn = false))   // screen off: the slow pace is right
        assertFalse(Cadence.rearmSteps(Cadence.STEP_MS, screenOn = false))
    }
}
