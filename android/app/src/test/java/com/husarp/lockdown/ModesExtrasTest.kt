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
}
