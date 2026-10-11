package com.husarp.lockdown

import com.husarp.lockdown.engine.Mode
import com.husarp.lockdown.engine.ModeActive
import com.husarp.lockdown.engine.Modes
import com.husarp.lockdown.engine.Pomodoro
import com.husarp.lockdown.engine.Schedule
import com.husarp.lockdown.engine.Window
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

class ModesExtrasTest {
    private val mon9 = LocalDateTime.of(2026, 9, 28, 9, 0)   // Monday
    private fun sched(vararg w: Window) = Schedule(windows = w.toList())
    private fun mode(s: Schedule? = null, p: Pomodoro? = null) = Mode("m", "M", categories = listOf("distracting"), schedule = s, pomodoro = p)

    @Test fun schedule_starts_and_stops_the_mode() {
        val m = mode(sched(Window((0..4).toList(), "09:00", "17:00")))
        assertNull(Modes.active(mon9.minusMinutes(1), listOf(m), null))
        val on = Modes.active(mon9, listOf(m), null)!!
        assertTrue(on.scheduled)
        assertEquals(mon9.withHour(17), on.until)
        assertNull(Modes.active(mon9.withHour(17), listOf(m), null))                    // end is exclusive
        assertNull(Modes.active(mon9.plusDays(5), listOf(m), null))                    // Saturday
    }

    @Test fun overnight_window_belongs_to_the_day_it_starts() {
        val m = mode(sched(Window(listOf(4), "22:00", "06:00")))                         // Friday night
        val fri23 = LocalDateTime.of(2026, 10, 2, 23, 0)
        assertNotNull(Modes.active(fri23, listOf(m), null))
        assertNotNull(Modes.active(LocalDateTime.of(2026, 10, 3, 5, 59), listOf(m), null))   // Saturday morning
        assertNull(Modes.active(LocalDateTime.of(2026, 10, 3, 6, 0), listOf(m), null))
        assertNull(Modes.active(LocalDateTime.of(2026, 10, 2, 5, 0), listOf(m), null))       // Friday early: Thursday's night, not on
        assertNull(Modes.active(LocalDateTime.of(2026, 10, 3, 23, 0), listOf(m), null))      // Saturday night
    }

    @Test fun next_start_and_text() {
        val m = mode(sched(Window((0..4).toList(), "09:00", "17:00")))
        assertEquals("Runs 09:00–17:00 Mon–Fri", Modes.scheduleText(m.schedule))
        assertNull(Modes.nextStart(m, mon9.plusHours(1)))                                // on now
        assertEquals(mon9.plusDays(1), Modes.nextStart(m, mon9.withHour(18)))
        assertEquals(mon9.plusDays(7), Modes.nextStart(m, mon9.plusDays(4).withHour(18)))   // Friday evening -> Monday
        assertNull(Modes.scheduleText(null))
    }

    @Test fun pomodoro_with_custom_lengths() {
        val p = Pomodoro(work = 50, brk = 10, rounds = 2, long = 20)
        assertEquals(50 + 10 + 50 + 20L, Modes.pomodoroLength(p))
        assertEquals("focus", Modes.pomodoroPhase(p, mon9, mon9.plusMinutes(49))!!.first)
        assertEquals("break", Modes.pomodoroPhase(p, mon9, mon9.plusMinutes(50))!!.first)
        assertEquals("long break", Modes.pomodoroPhase(p, mon9, mon9.plusMinutes(115))!!.first)
        assertNull(Modes.pomodoroPhase(p, mon9, mon9.plusMinutes(130)))
        val m = mode(p = p)
        val st = Modes.active(mon9.plusMinutes(55), listOf(m), ModeActive("m", mon9.toString()))!!
        assertEquals("break", st.phase!!.first)
        assertFalse(Modes.blocking(st))
    }

    @Test fun pomodoro_is_clamped() {
        assertEquals(Pomodoro(180, 1, 12, 120), Modes.clamp(Pomodoro(999, 0, 99, 500)))
        assertEquals(Pomodoro(25, 5, 4, 15), Modes.clamp(Pomodoro()))
    }

    @Test fun until_a_time_past_means_tomorrow() {
        val now = LocalDateTime.of(2026, 9, 28, 14, 30)
        assertEquals(LocalDateTime.of(2026, 9, 28, 18, 0), Modes.untilTime(now, "18:00"))
        assertEquals(LocalDateTime.of(2026, 9, 29, 9, 0), Modes.untilTime(now, "09:00"))
        assertEquals(LocalDateTime.of(2026, 9, 29, 14, 30), Modes.untilTime(now, "14:30"))   // exactly now: not after it
        assertEquals(LocalDateTime.of(2026, 9, 29, 0, 0), Modes.untilTime(now, "00:00"))
    }

    @Test fun schedule_edits_loosen_only_when_they_run_less() {
        val old = mode(sched(Window((0..4).toList(), "09:00", "17:00")))
        assertFalse(Modes.modeLooser(old, old.copy(schedule = sched(Window((0..4).toList(), "09:00", "18:00"))), false))   // longer
        assertFalse(Modes.modeLooser(old, old.copy(schedule = sched(Window((0..6).toList(), "09:00", "17:00"))), false))   // more days
        assertFalse(Modes.modeLooser(old, old.copy(schedule = sched(Window((0..4).toList(), "09:00", "17:00"), Window(listOf(5), "10:00", "12:00"))), false))
        assertTrue(Modes.modeLooser(old, old.copy(schedule = sched(Window((0..4).toList(), "09:00", "16:00"))), false))    // shorter
        assertTrue(Modes.modeLooser(old, old.copy(schedule = sched(Window((0..3).toList(), "09:00", "17:00"))), false))    // fewer days
        assertTrue(Modes.modeLooser(old, old.copy(schedule = null), false))                                                // removed
        assertFalse(Modes.modeLooser(mode(), old, false))                                                                   // adding one
        assertFalse(Modes.modeLooser(old, old.copy(schedule = sched(Window((0..4).toList(), "09:00", "17:00"))), true))     // unchanged
    }

    @Test fun overnight_schedule_extension_is_free_but_cutting_is_not() {
        val old = mode(sched(Window((0..6).toList(), "22:00", "06:00")))
        assertFalse(Modes.modeLooser(old, old.copy(schedule = sched(Window((0..6).toList(), "22:00", "07:00"))), false))
        assertTrue(Modes.modeLooser(old, old.copy(schedule = sched(Window((0..6).toList(), "22:00", "05:00"))), false))
    }

    @Test fun pomodoro_edits_loosen_only_while_locked() {
        val old = mode(p = Pomodoro(25, 5, 4, 15))
        val shorter = old.copy(pomodoro = Pomodoro(20, 5, 4, 15))
        assertFalse(Modes.modeLooser(old, shorter, false))
        assertTrue(Modes.modeLooser(old, shorter, true))
        assertTrue(Modes.modeLooser(old, old.copy(pomodoro = Pomodoro(25, 5, 3, 15)), true))     // fewer rounds
        assertTrue(Modes.modeLooser(old, old.copy(pomodoro = Pomodoro(25, 10, 4, 15)), true))    // longer break
        assertTrue(Modes.modeLooser(old, old.copy(pomodoro = null), true))
        assertFalse(Modes.modeLooser(old, old.copy(pomodoro = Pomodoro(30, 5, 5, 10)), true))    // longer work, more rounds, shorter long break
        assertTrue(Modes.modeLooser(old, old.copy(categories = emptyList()), true))              // blocks less
        assertFalse(Modes.modeLooser(old, old.copy(categories = listOf("distracting", "neutral")), true))
    }

    @Test fun starting_over_a_locked_mode() {
        val end = mon9.plusHours(3)
        val locked = Modes.active(mon9, listOf(mode()), ModeActive("m", mon9.toString(), end.toString(), locked = true))!!
        assertTrue(Modes.startLooser(locked, "m", end.minusHours(1), true))      // earlier until
        assertTrue(Modes.startLooser(locked, "m", end, false))                    // unlocked
        assertTrue(Modes.startLooser(locked, "other", end.plusHours(1), true))   // another mode
        assertFalse(Modes.startLooser(locked, "m", end.plusHours(1), true))      // later: free
        assertFalse(Modes.startLooser(locked, "m", end, true))
        val free = Modes.active(mon9, listOf(mode()), ModeActive("m", mon9.toString(), end.toString()))!!
        assertFalse(Modes.startLooser(free, "other", null, false))
        assertFalse(Modes.startLooser(null, "m", null, false))
    }

    private val week = sched(Window((0..4).toList(), "09:00", "17:00"))
    private fun needs(modes: List<Mode>, st: ModeActive?, saved: Mode?, draft: Mode, until: LocalDateTime? = null, locked: Boolean = false, at: LocalDateTime = mon9) =
        Modes.startNeedsChallenge(at, modes, st, saved, draft, until, locked)

    @Test fun start_now_with_a_looser_draft_needs_the_challenge() {
        val saved = mode(week)
        assertTrue(needs(listOf(saved), null, saved, saved.copy(schedule = null), at = mon9.minusHours(3)))   // scheduled -> by hand
        assertFalse(needs(listOf(saved), null, saved, saved, at = mon9.minusHours(3)))
        // running locked by hand until T, then blocking less and starting with the same lock
        val m = mode()
        val end = mon9.plusHours(3)
        val st = ModeActive("m", mon9.toString(), end.toString(), locked = true)
        assertTrue(needs(listOf(m), st, m, m.copy(categories = emptyList()), end, true))
        assertFalse(needs(listOf(m), st, m, m, end, true))
    }

    @Test fun starting_a_different_mode_in_a_scheduled_window_needs_the_challenge() {
        val s = Mode("s", "S", schedule = week)
        val other = Mode("o", "O")
        val modes = listOf(s, other)
        assertTrue(needs(modes, null, other, other))                                   // in window
        assertFalse(needs(modes, null, s, s.copy(schedule = null).let { s }))          // same mode: free
        assertFalse(needs(modes, null, other, other, at = mon9.minusHours(2)))        // outside window
        // a hand-started mode hides the scheduled one but a third start is still guarded
        val st = ModeActive("o", mon9.toString())
        assertTrue(needs(modes, st, other, Mode("x", "X")))
    }

    @Test fun bad_pomodoro_values_are_clamped_when_read() {
        val bad = mode(p = Pomodoro(-5, 0, 0, -1))
        val st = Modes.active(mon9, listOf(bad), ModeActive("m", mon9.toString()))!!
        assertTrue(Modes.pomodoroLength(Modes.clamp(bad.pomodoro!!)) > 0)
        assertNotNull(st.until)
        assertTrue(st.until!!.isAfter(mon9))
    }

    // ---- scheduled Pomodoro cycles inside its window ----
    private val pom = Pomodoro(25, 5, 2, 15)          // one cycle = 25+5+25+15 = 70 min
    private val pm = mode(sched(Window((0..4).toList(), "09:00", "17:00")), pom)

    @Test fun scheduled_pomodoro_cycles_from_the_window_start() {
        val a = Modes.active(mon9.plusMinutes(10), listOf(pm), null)!!
        assertEquals(mon9, a.started)
        assertEquals(Triple("focus", mon9.plusMinutes(25), 1), a.phase)
        assertTrue(Modes.blocking(a))
        val b = Modes.active(mon9.plusMinutes(27), listOf(pm), null)!!
        assertEquals("break", b.phase!!.first)
        assertFalse(Modes.blocking(b))                                   // no blocking in a break
        assertEquals("long break", Modes.active(mon9.plusMinutes(60), listOf(pm), null)!!.phase!!.first)
        assertTrue(b.scheduled && !b.locked)
    }

    @Test fun scheduled_pomodoro_repeats_after_the_last_round_and_stops_at_the_window_end() {
        val c = Modes.active(mon9.plusMinutes(70), listOf(pm), null)!!        // second cycle, round 1 again
        assertEquals(Triple("focus", mon9.plusMinutes(95), 1), c.phase)
        assertEquals("break", Modes.active(mon9.plusMinutes(70 + 26), listOf(pm), null)!!.phase!!.first)
        // the last phase is cut at the window end (16:57 is a long break that would run to 17:10)
        val late = Modes.active(mon9.withHour(16).withMinute(57), listOf(pm), null)!!
        assertEquals(mon9.withHour(17), late.phase!!.second)
        assertNull(Modes.active(mon9.withHour(17), listOf(pm), null))
    }

    @Test fun scheduled_pomodoro_in_an_overnight_window_counts_from_its_start() {
        val m = mode(sched(Window(listOf(4), "22:00", "06:00")), pom)
        val sat1 = LocalDateTime.of(2026, 10, 3, 1, 57)                     // 237 min after Fri 22:00 -> 27 into cycle 4 -> break
        val a = Modes.active(sat1, listOf(m), null)!!
        assertEquals(LocalDateTime.of(2026, 10, 2, 22, 0), a.started)
        assertEquals("break", a.phase!!.first)
    }

    @Test fun break_in_a_scheduled_pomodoro_is_no_way_round_the_guards() {
        val other = Mode("o", "O")
        val inBreak = mon9.plusMinutes(27)
        assertFalse(Modes.blocking(Modes.active(inBreak, listOf(pm, other), null)))
        // starting another mode during the break still needs the challenge; so does saving a shorter schedule
        assertTrue(Modes.startNeedsChallenge(inBreak, listOf(pm, other), null, other, other, null, false))
        val shorter = pm.copy(schedule = sched(Window((0..4).toList(), "09:00", "12:00")))
        assertTrue(Modes.startNeedsChallenge(inBreak, listOf(pm), null, pm, shorter, null, false))
        // a hand-started locked mode is not shortened by the scheduled one: still locked and guarded
        val st = ModeActive("o", mon9.toString(), mon9.plusHours(3).toString(), locked = true)
        assertTrue(Modes.startLooser(Modes.active(inBreak, listOf(pm, other), st), "o", null, true))
    }

    @Test fun a_scheduled_mode_without_pomodoro_has_no_phase() {
        assertNull(Modes.active(mon9, listOf(mode(sched(Window((0..4).toList(), "09:00", "17:00")))), null)!!.phase)
    }
}
