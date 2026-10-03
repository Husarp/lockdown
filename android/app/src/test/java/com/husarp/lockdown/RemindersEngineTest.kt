package com.husarp.lockdown

import com.husarp.lockdown.engine.BreakCfg
import com.husarp.lockdown.engine.CustomCfg
import com.husarp.lockdown.engine.ReminderTier
import com.husarp.lockdown.engine.RemindersEngine
import com.husarp.lockdown.engine.SleepCfg
import com.husarp.lockdown.engine.bedtimeGrayscaleWanted
import com.husarp.lockdown.engine.grayscaleStep
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

    private fun at(day: Int, h: Int, m: Int) = LocalDateTime.of(2026, 9, day, h, m)

    /** Dismiss the bedtime screen at [dismissAt]; true if it is back [after] minutes later (and not a minute before). */
    private fun backAfter(s: SleepCfg, dismissAt: LocalDateTime, after: Long): Boolean {
        val e = RemindersEngine()
        assertTrue(e.tick(dismissAt, 999.0, s, noBreak, emptyList()).show.any { it.key == "sleep" })
        e.answer("sleep", "dismiss", dismissAt, s, noBreak, emptyList())
        val early = e.tick(dismissAt.plusMinutes(after - 1), 999.0, s, noBreak, emptyList())
        val due = e.tick(dismissAt.plusMinutes(after), 999.0, s, noBreak, emptyList())
        return early.show.none { it.key == "sleep" } && due.show.any { it.key == "sleep" }
    }

    @Test fun bedtime_at_22_follows_your_own_steps() {
        val s = SleepCfg(on = true, bedtime = "22:00", wake = "07:00",
            tiers = listOf(ReminderTier("22:00", 20), ReminderTier("23:30", 10), ReminderTier("01:00", 2)))
        assertTrue(backAfter(s, at(28, 22, 5), 20))
        assertTrue(backAfter(s, at(28, 23, 40), 10))
        assertTrue(backAfter(s, at(29, 1, 30), 2))                        // after midnight: the next morning's step
    }

    @Test fun a_step_before_bedtime_counts_from_bedtime() {
        // (Android used to drop it and fall back to a fixed 15)
        val s = SleepCfg(on = true, bedtime = "22:00", wake = "07:00", tiers = listOf(ReminderTier("21:00", 30)))
        assertTrue(backAfter(s, at(28, 22, 1), 30))
        assertTrue(RemindersEngine.stepFromBedtime(s, "21:00"))
        assertFalse(RemindersEngine.stepFromBedtime(s, "23:00"))
        assertFalse(RemindersEngine.stepFromBedtime(s, "02:00"))          // the small hours are their own time
    }

    @Test fun steps_follow_a_bedtime_after_midnight() {
        // 00:00 is before a 00:30 bedtime: it counts from bedtime (it used to be dated after wake and never apply)
        val s = SleepCfg(on = true, bedtime = "00:30", wake = "07:00", tiers = listOf(ReminderTier("00:00", 5), ReminderTier("03:00", 1)))
        assertTrue(backAfter(s, at(29, 0, 40), 5))
        assertTrue(backAfter(s, at(29, 3, 10), 1))
        assertTrue(RemindersEngine.stepFromBedtime(s, "00:00"))
        assertFalse(RemindersEngine.stepFromBedtime(s, "03:00"))
        val late = SleepCfg(on = true, bedtime = "01:00", wake = "07:00", repeat = 9, tiers = listOf(ReminderTier("23:00", 20)))
        assertTrue(backAfter(late, at(29, 1, 10), 20))
        assertTrue(RemindersEngine.stepFromBedtime(late, "23:00"))
    }

    @Test fun two_steps_at_the_same_time_use_the_more_frequent() {
        val s = SleepCfg(on = true, bedtime = "23:00", wake = "07:00", tiers = listOf(ReminderTier("23:00", 15), ReminderTier("23:00", 1)))
        assertTrue(backAfter(s, at(28, 23, 10), 1))
    }

    @Test fun a_bad_time_never_stops_the_rest() {
        // a half-typed time saved by an older editor: that reminder is skipped, bedtime still comes
        val bad = listOf(CustomCfg("a", text = "Water", kind = "times", times = listOf("9", "25:00")),
            CustomCfg("b", text = "Walk", kind = "random", window = listOf("9am", "")))
        val s = SleepCfg(on = true, bedtime = "22:00", wake = "07:00")
        val e = RemindersEngine()
        assertTrue(e.tick(at(28, 22, 5), 999.0, s, noBreak, bad).show.any { it.key == "sleep" })
        // a bad bedtime: bedtime is off, a break still comes
        val r = RemindersEngine().tick(t0, 0.0, SleepCfg(on = true, bedtime = "2"), BreakCfg(on = true, every = 1), bad, dt = 60.0)
        assertTrue(r.show.any { it.key == "break" })
    }

    @Test fun a_swiped_strict_break_counts_as_a_snooze() {
        val b = BreakCfg(on = true, every = 1, strict = true, maxSnooze = 1, snooze = 5)
        val e = RemindersEngine()
        assertTrue(e.tick(t0, 0.0, noSleep, b, emptyList(), dt = 60.0).show.any { it.key == "break" })
        e.answer("break", "swipe", t0, noSleep, b, emptyList())
        assertNotNull(e.tick(t0.plusMinutes(5), 0.0, noSleep, b, emptyList(), dt = 5.0).startBreak)   // out of snoozes
        // not strict: swiped is waved away, use starts counting again
        val loose = BreakCfg(on = true, every = 1)
        val f = RemindersEngine()
        f.tick(t0, 0.0, noSleep, loose, emptyList(), dt = 60.0)
        f.answer("break", "swipe", t0, noSleep, loose, emptyList())
        assertFalse(f.tick(t0.plusSeconds(5), 0.0, noSleep, loose, emptyList(), dt = 5.0).show.any { it.key == "break" })
    }

    @Test fun no_steps_or_before_the_first_one_uses_the_flat_repeat() {
        val none = SleepCfg(on = true, bedtime = "22:00", wake = "07:00", repeat = 5, tiers = emptyList())
        assertTrue(backAfter(none, at(28, 22, 10), 5))
        val later = SleepCfg(on = true, bedtime = "20:00", wake = "07:00", repeat = 5, tiers = listOf(ReminderTier("21:00", 15)))
        assertTrue(backAfter(later, at(28, 20, 10), 5))
        assertTrue(backAfter(later, at(28, 21, 10), 15))
    }

    @Test fun default_steps_start_at_bedtime() {
        val s = SleepCfg(on = true)                                       // 23:00 → 15, 00:00 → 5, 03:00 → 1
        assertEquals("23:00", s.tiers.first().from)
        assertTrue(backAfter(s, at(28, 23, 0), 15))
        assertTrue(backAfter(s, at(29, 0, 30), 5))
        assertTrue(backAfter(s, at(29, 3, 30), 1))
    }

    @Test fun heads_up_off_sends_none_and_custom_texts_are_used() {
        val off = SleepCfg(on = true, bedtime = "22:00", before = 0)
        assertFalse(RemindersEngine().tick(at(28, 21, 50), 999.0, off, noBreak, emptyList()).show.any { it.key == "sleep-warn" })
        val s = SleepCfg(on = true, bedtime = "22:00", wake = "06:30", before = 15, warnText = "Wrap up by {bedtime}",
            text = "Bed! Up at {wake}")
        val e = RemindersEngine()
        assertEquals("Wrap up by 22:00", e.tick(at(28, 21, 50), 999.0, s, noBreak, emptyList()).show.single { it.key == "sleep-warn" }.text)
        assertEquals("Bed! Up at 06:30", e.tick(at(28, 22, 0), 999.0, s, noBreak, emptyList()).show.single { it.key == "sleep" }.text)
    }

    @Test fun off_tonight_and_snooze_hold_the_bedtime_screen() {
        val s = SleepCfg(on = true, bedtime = "22:00", wake = "07:00")
        val e = RemindersEngine()
        e.tick(at(28, 22, 0), 999.0, s, noBreak, emptyList())
        e.answer("sleep", "off_tonight", at(28, 22, 0), s, noBreak, emptyList())
        assertFalse(e.tick(at(29, 3, 0), 999.0, s, noBreak, emptyList()).show.any { it.key == "sleep" })
        assertTrue(e.tick(at(29, 22, 0), 999.0, s, noBreak, emptyList()).show.any { it.key == "sleep" })   // next night again
        val z = RemindersEngine()
        z.tick(at(28, 22, 0), 999.0, s, noBreak, emptyList())
        z.answer("sleep", "snooze:15", at(28, 22, 0), s, noBreak, emptyList())
        assertFalse(z.tick(at(28, 22, 14), 999.0, s, noBreak, emptyList()).show.any { it.key == "sleep" })
        assertTrue(z.tick(at(28, 22, 15), 999.0, s, noBreak, emptyList()).show.any { it.key == "sleep" })
    }

    @Test fun bedtime_screen_offers_the_emergency_only_with_uses_left() {
        val s = SleepCfg(on = true, bedtime = "22:00")
        val with = RemindersEngine().tick(at(28, 22, 0), 999.0, s, noBreak, emptyList(), emergencyLeft = 2).show.single { it.key == "sleep" }
        assertEquals(listOf("dismiss", "disable", "emergency"), with.buttons.map { it.action })
        assertEquals("Emergency (2 left)", with.buttons.last().label)
        val none = RemindersEngine().tick(at(28, 22, 0), 999.0, s, noBreak, emptyList(), emergencyLeft = 0).show.single { it.key == "sleep" }
        assertEquals(listOf("dismiss", "disable"), none.buttons.map { it.action })
    }

    @Test fun an_emergency_pause_holds_bedtime_and_ends_a_strict_break() {
        val s = SleepCfg(on = true, bedtime = "22:00", wake = "07:00")
        val e = RemindersEngine()
        assertTrue(e.tick(at(28, 22, 0), 999.0, s, noBreak, emptyList()).show.any { it.key == "sleep" })
        val paused = e.tick(at(28, 22, 1), 999.0, s, noBreak, emptyList(), paused = true)
        assertTrue("sleep" in paused.close)                               // the screen goes
        assertFalse(e.tick(at(28, 22, 10), 999.0, s, noBreak, emptyList(), paused = true).show.any { it.key == "sleep" })
        assertTrue(e.tick(at(28, 22, 21), 999.0, s, noBreak, emptyList()).show.any { it.key == "sleep" })   // back after it

        val b = BreakCfg(on = true, every = 1, strict = true, maxSnooze = 0, length = 10)
        val k = RemindersEngine()
        assertNotNull(k.tick(t0, 0.0, noSleep, b, emptyList(), dt = 60.0).startBreak)
        assertTrue(k.tick(t0.plusMinutes(1), 0.0, noSleep, b, emptyList(), paused = true).breakEnded)
    }

    @Test fun no_warning_while_paused() {
        val s = SleepCfg(on = true, bedtime = "22:00", before = 30)
        assertFalse(RemindersEngine().tick(at(28, 21, 40), 999.0, s, noBreak, emptyList(), paused = true).show.any { it.key == "sleep-warn" })
    }

    @Test fun break_snooze_follows_its_setting() {
        val b = BreakCfg(on = true, every = 1, snooze = 10)
        val e = RemindersEngine()
        assertTrue(e.tick(t0, 0.0, noSleep, b, emptyList(), dt = 60.0).show.any { it.key == "break" })
        e.answer("break", "snooze", t0, noSleep, b, emptyList())
        assertFalse(e.tick(t0.plusMinutes(9), 0.0, noSleep, b, emptyList()).show.any { it.key == "break" })
        assertTrue(e.tick(t0.plusMinutes(10), 0.0, noSleep, b, emptyList()).show.any { it.key == "break" })
    }

    @Test fun break_waits_for_its_interval_and_resets_when_away() {
        val b = BreakCfg(on = true, every = 45)
        val e = RemindersEngine()
        var shown = false
        for (i in 1..40 * 60 / 5) shown = shown || e.tick(t0.plusSeconds(i * 5L), 0.0, noSleep, b, emptyList()).show.any { it.key == "break" }
        assertFalse(shown)                                                // 40 min of use: not yet
        e.tick(t0.plusMinutes(41), 400.0, noSleep, b, emptyList())        // screen off 6+ min = a break
        for (i in 1..10 * 60 / 5) shown = shown || e.tick(t0.plusMinutes(48).plusSeconds(i * 5L), 0.0, noSleep, b, emptyList()).show.any { it.key == "break" }
        assertFalse(shown)                                                // counting from zero again
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

    @Test fun grayscale_left_on_by_an_older_copy_goes_off_at_wake_time() {
        // Adam's bug: after the reinstall the "Lockdown turned it on" mark was gone, so the grey screen was
        // taken for the user's own and never turned off. With the switch on, an unmarked grey screen is ours.
        assertEquals(false to false, grayscaleStep(wanted = false, switchOn = true, mark = null, isOn = true))
        assertEquals(false to false, grayscaleStep(wanted = false, switchOn = true, mark = null, isOn = null))   // Android won't say: write
        assertEquals(null to null, grayscaleStep(wanted = false, switchOn = true, mark = null, isOn = false))    // colour already back
    }

    @Test fun grayscale_the_user_set_is_left_alone() {
        assertEquals(null to null, grayscaleStep(wanted = false, switchOn = false, mark = null, isOn = true))   // switch off, never ours
        assertEquals(null to false, grayscaleStep(wanted = false, switchOn = true, mark = false, isOn = true))  // turned on in Android's settings
    }

    @Test fun grayscale_goes_on_at_bedtime_and_off_after_writing_only_on_change() {
        assertEquals(true to true, grayscaleStep(wanted = true, switchOn = true, mark = null, isOn = false))
        assertEquals(null to true, grayscaleStep(wanted = true, switchOn = true, mark = true, isOn = true))     // already on: no write
        assertEquals(true to true, grayscaleStep(wanted = true, switchOn = true, mark = true, isOn = null))     // can't tell: write
        assertEquals(false to false, grayscaleStep(wanted = false, switchOn = true, mark = true, isOn = true))  // wake time
        assertEquals(false to false, grayscaleStep(wanted = false, switchOn = false, mark = true, isOn = true)) // switch off at night
        assertEquals(null to false, grayscaleStep(wanted = false, switchOn = true, mark = true, isOn = false))  // already off
    }
}
