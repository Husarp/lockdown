package com.husarp.lockdown

import com.husarp.lockdown.engine.BreakCfg
import com.husarp.lockdown.engine.CustomCfg
import com.husarp.lockdown.engine.RemindersEngine
import com.husarp.lockdown.engine.SleepCfg
import com.husarp.lockdown.engine.bedtimeGrayscaleWanted
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

class RemindersEngineTest {
    private val t0 = LocalDateTime.of(2026, 9, 28, 12, 0)   // Monday noon
    private val noSleep = SleepCfg()
    private val noBreak = BreakCfg(on = false)

    @Test fun break_comes_up_after_the_interval_of_use() {
        val e = RemindersEngine()
        val r = e.tick(t0, idleSec = 0.0, noSleep, BreakCfg(on = true, every = 1), emptyList(), dt = 60.0)
        assertTrue(r.show.any { it.key == "break" })
    }

    @Test fun strict_break_starts_itself_when_snoozes_run_out() {
        val e = RemindersEngine()
        val b = BreakCfg(on = true, every = 1, strict = true, maxSnooze = 1, snooze = 5)
        e.tick(t0, 0.0, noSleep, b, emptyList(), dt = 60.0)               // break shown
        e.answer("break", "snooze", t0, noSleep, b, emptyList())          // one snooze used
        val res = e.tick(t0.plusMinutes(6), 0.0, noSleep, b, emptyList(), dt = 60.0)
        assertNotNull(res.startBreak)                                     // out of snoozes -> enforced
    }

    @Test fun twenty_twenty_twenty_toasts() {
        val e = RemindersEngine()
        val r = e.tick(t0, 0.0, noSleep, BreakCfg(on = true, every = 999, twenty = true), emptyList(), dt = 1200.0)
        assertTrue(r.toasts.any { it.contains("20-20-20") })
    }

    @Test fun bedtime_warns_then_shows_then_escalates() {
        val e = RemindersEngine()
        val s = SleepCfg(on = true, bedtime = "23:00", wake = "07:00", before = 30, mode = "dnd")
        val warn = e.tick(LocalDateTime.of(2026, 9, 28, 22, 40), 999.0, s, noBreak, emptyList())
        assertTrue(warn.show.any { it.key == "sleep-warn" })

        val bed = e.tick(LocalDateTime.of(2026, 9, 28, 23, 10), 999.0, s, noBreak, emptyList())
        assertTrue(bed.show.any { it.key == "sleep" && it.overlay })
        assertEquals("dnd", bed.startMode?.first)                         // bedtime starts the mode

        e.answer("sleep", "dismiss", LocalDateTime.of(2026, 9, 28, 23, 10), s, noBreak, emptyList())
        val early = e.tick(LocalDateTime.of(2026, 9, 28, 23, 20), 999.0, s, noBreak, emptyList())
        assertFalse(early.show.any { it.key == "sleep" })                 // 15-min tier: not yet
        val later = e.tick(LocalDateTime.of(2026, 9, 28, 23, 26), 999.0, s, noBreak, emptyList())
        assertTrue(later.show.any { it.key == "sleep" })                  // back after 15 min
    }

    @Test fun custom_interval_fires_after_use() {
        val e = RemindersEngine()
        val c = CustomCfg(id = "w", text = "Drink water", kind = "interval", every = 1)
        val r = e.tick(t0, 0.0, noSleep, noBreak, listOf(c), dt = 60.0)
        assertTrue(r.show.any { it.key == "custom:w" })
    }

    @Test fun custom_set_time_fires_once_a_day() {
        val e = RemindersEngine()
        val c = CustomCfg(id = "t", text = "Lunch", kind = "times", times = listOf("12:00"))
        val at12 = e.tick(LocalDateTime.of(2026, 9, 28, 12, 0), 999.0, noSleep, noBreak, listOf(c))
        assertTrue(at12.show.any { it.key == "custom:t" })
        val later = e.tick(LocalDateTime.of(2026, 9, 28, 12, 10), 999.0, noSleep, noBreak, listOf(c))
        assertFalse(later.show.any { it.key == "custom:t" })              // already fired today
    }

    @Test fun custom_respects_chosen_days() {
        val e = RemindersEngine()
        val tuesdayOnly = CustomCfg(id = "m", text = "X", kind = "times", times = listOf("12:00"), days = listOf(1))
        val monday = e.tick(LocalDateTime.of(2026, 9, 28, 12, 0), 999.0, noSleep, noBreak, listOf(tuesdayOnly))
        assertFalse(monday.show.any { it.key == "custom:m" })
    }

    @Test fun custom_respects_hours_window() {
        val e = RemindersEngine()
        val daytime = CustomCfg(id = "h", text = "X", kind = "interval", every = 1, hours = true,
            window = listOf("10:00", "18:00"))
        val night = e.tick(LocalDateTime.of(2026, 9, 28, 20, 0), 0.0, noSleep, noBreak, listOf(daytime), dt = 60.0)
        assertFalse(night.show.any { it.key == "custom:h" })
    }

    @Test fun custom_stops_after_the_per_day_cap() {
        val e = RemindersEngine()
        val c = CustomCfg(id = "p", text = "Pill", kind = "interval", every = 1, perDay = 1)
        val first = e.tick(t0, 0.0, noSleep, noBreak, listOf(c), dt = 60.0)
        assertTrue(first.show.any { it.key == "custom:p" })
        e.answer("custom:p", "done", t0, noSleep, noBreak, listOf(c))     // 1 of 1 done
        val again = e.tick(t0.plusMinutes(2), 0.0, noSleep, noBreak, listOf(c), dt = 60.0)
        assertFalse(again.show.any { it.key == "custom:p" })              // cap reached today
    }

    @Test fun check_reopens_the_reminder_on_no() {
        val e = RemindersEngine()
        val c = CustomCfg(id = "k", text = "Task", kind = "interval", every = 1, check = 5)
        e.tick(t0, 0.0, noSleep, noBreak, listOf(c), dt = 60.0)
        e.answer("custom:k", "done", t0, noSleep, noBreak, listOf(c))     // schedules the check
        val chk = e.tick(t0.plusMinutes(5), 999.0, noSleep, noBreak, listOf(c))
        assertTrue(chk.show.any { it.key == "check:k" })
        val refire = e.answer("check:k", "no", t0.plusMinutes(5), noSleep, noBreak, listOf(c))
        assertTrue(refire.show.any { it.key == "custom:k" })              // "no" -> ask again
    }

    @Test fun two_reminders_due_together_become_one_prompt() {
        val e = RemindersEngine()
        val a = CustomCfg(id = "a", text = "Alpha", kind = "interval", every = 1)
        val b = CustomCfg(id = "b", text = "Beta", kind = "interval", every = 1)
        val r = e.tick(t0, 0.0, noSleep, noBreak, listOf(a, b), dt = 60.0)
        val last = r.show.last { it.key == "custom:a" }
        assertTrue(last.text.contains("Alpha") && last.text.contains("Beta"))   // merged, one interruption
    }

    @Test fun waved_away_repeatedly_backs_off() {
        val e = RemindersEngine()
        val c = CustomCfg(id = "d", text = "Do it", kind = "interval", every = 1)
        var backedOff = false
        for (i in 0 until 3) {
            val at = t0.plusMinutes(i * 30L)                             // far apart so the pace never blocks it
            e.tick(at, 0.0, noSleep, noBreak, listOf(c), dt = 60.0)
            val res = e.answer("custom:d", "dismiss", at, noSleep, noBreak, listOf(c))
            if (res.toasts.any { it.contains("half as often") }) backedOff = true
        }
        assertTrue(backedOff)
    }

    @Test fun loosens_reminder_only_when_a_guarded_one_is_weakened() {
        assertTrue(RemindersEngine.loosensReminder(oldOn = true, oldGuarded = true, newOn = false, newGuarded = true))
        assertTrue(RemindersEngine.loosensReminder(oldOn = true, oldGuarded = true, newOn = true, newGuarded = false))
        assertFalse(RemindersEngine.loosensReminder(oldOn = true, oldGuarded = false, newOn = false, newGuarded = false))
        assertFalse(RemindersEngine.loosensReminder(oldOn = true, oldGuarded = true, newOn = true, newGuarded = true))
    }

    @Test fun bedtime_grayscale_only_when_toggle_and_bedtime_on_inside_the_window() {
        val s = SleepCfg(on = true, bedtime = "23:00", wake = "07:00")
        assertTrue(bedtimeGrayscaleWanted(true, s, 23 * 60 + 30))                 // inside, across midnight
        assertTrue(bedtimeGrayscaleWanted(true, s, 2 * 60))
        assertFalse(bedtimeGrayscaleWanted(true, s, 12 * 60))                     // daytime -> off
        assertFalse(bedtimeGrayscaleWanted(false, s, 23 * 60 + 30))               // toggle off at night -> off (the bug)
        assertFalse(bedtimeGrayscaleWanted(true, s.copy(on = false), 23 * 60 + 30)) // Bedtime off -> off
        val day = SleepCfg(on = true, bedtime = "13:00", wake = "15:00")          // same-day window
        assertTrue(bedtimeGrayscaleWanted(true, day, 14 * 60))
        assertFalse(bedtimeGrayscaleWanted(true, day, 15 * 60))
    }
}
