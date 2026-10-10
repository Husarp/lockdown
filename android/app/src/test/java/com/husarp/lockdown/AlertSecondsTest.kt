package com.husarp.lockdown

import com.husarp.lockdown.data.Config
import com.husarp.lockdown.engine.AlertsCfg
import com.husarp.lockdown.engine.BlockWatcher
import com.husarp.lockdown.engine.BreakCfg
import com.husarp.lockdown.engine.CustomCfg
import com.husarp.lockdown.engine.Interval
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.LimitClock
import com.husarp.lockdown.engine.ReminderTier
import com.husarp.lockdown.engine.RemindersEngine
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.SchedMode
import com.husarp.lockdown.engine.Schedule
import com.husarp.lockdown.engine.SleepCfg
import com.husarp.lockdown.engine.Window
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

/**
 * Alert intervals in seconds (2026-10-08): the bedtime steps, a break's "every" and snooze, your reminders' "every"
 * and the "N min left" repeats can be 5 s or 30 s, for very annoying alerts. What was saved in minutes reads the same.
 */
class AlertSecondsTest {
    private val t0 = LocalDateTime.of(2026, 9, 28, 12, 0)   // Monday noon
    private val noSleep = SleepCfg()
    private val noBreak = BreakCfg(on = false)
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    // ---------- typing and showing ----------

    @Test fun the_box_takes_seconds_minutes_and_hours() {
        assertEquals(30, Interval.parse("30s"))
        assertEquals(30, Interval.parse(" 30 s "))
        assertEquals(5, Interval.parse("5 sec"))
        assertEquals(300, Interval.parse("5 min"))
        assertEquals(300, Interval.parse("5"))                          // a bare number is minutes, as before
        assertEquals(90, Interval.parse("1,5"))
        assertEquals(5400, Interval.parse("1h30"))
        assertEquals(3600, Interval.parse("1 h"))
        for (bad in listOf("4s", "0", "", "Off", "soon", "25h", "99999999999999999999")) assertNull(bad, Interval.parse(bad))
    }

    @Test fun an_interval_shows_the_way_you_would_type_it() {
        assertEquals(listOf("5 s", "30 s", "90 s", "5 min", "1h", "1h30", "Off"), listOf(5, 30, 90, 300, 3600, 5400, 0).map(Interval::text))
        for (s in listOf(5, 30, 90, 300, 3600, 5400, 86400)) assertEquals(s, Interval.parse(Interval.text(s)))   // it reads back
    }

    // ---------- what was saved in minutes ----------

    @Test fun minutes_saved_before_read_as_seconds() {
        assertEquals(900, ReminderTier("23:00", 15).seconds)
        assertEquals(30, ReminderTier("23:00", 15, everySec = 30).seconds)
        assertEquals(listOf(900, 300, 60), SleepCfg().tiers.map { it.seconds })     // the defaults: 15 min, 5 min, 1 min
        assertEquals(1800 to 300, BreakCfg(every = 30, snooze = 5).let { it.everySeconds to it.snoozeSeconds })
        assertEquals(1200, CustomCfg("a", every = 20).everySeconds)
        assertEquals(120, AlertsCfg(repeatMin = 2).repeatSeconds)
        assertEquals(540, SleepCfg(repeat = 9).repeatSeconds)
    }

    @Test fun an_old_config_file_reads_the_same_and_a_new_one_keeps_its_seconds() {
        val old = """{"sleep":{"on":true,"repeat":7,"tiers":[{"from":"23:00","every":15}]},"breaks":{"every":30,"snooze":10},
            "customs":[{"id":"w","text":"Water","every":20}],"alerts":{"repeatMin":2}}"""
        val c = json.decodeFromString<Config>(old)
        assertEquals(listOf(900), c.sleep.tiers.map { it.seconds })
        assertEquals(420, c.sleep.repeatSeconds)
        assertEquals(1800 to 600, c.breaks.everySeconds to c.breaks.snoozeSeconds)
        assertEquals(1200, c.customs.single().everySeconds)
        assertEquals(120, c.alerts.repeatSeconds)
        val set = c.copy(sleep = c.sleep.copy(tiers = listOf(ReminderTier("23:00", everySec = 30))), breaks = c.breaks.copy(everySec = 45),
            customs = c.customs.map { it.copy(everySec = 5) }, alerts = c.alerts.copy(repeatSec = 30))
        val text = json.encodeToString(set)
        val back = json.decodeFromString<Config>(text)
        assertEquals(30, back.sleep.tiers.single().seconds)
        assertEquals(45, back.breaks.everySeconds)
        assertEquals(5, back.customs.single().everySeconds)
        assertEquals(30, back.alerts.repeatSeconds)
        // the old minutes field is still written, so an older copy (Lockdown in Island) can still read the file
        assertTrue(Regex(""""tiers":\[\{"from":"23:00","every":\d+""").containsMatchIn(text))
    }

    // ---------- bedtime ----------

    private fun at(day: Int, h: Int, m: Int, s: Int = 0) = LocalDateTime.of(2026, 9, day, h, m, s)

    /** Dismissed at [dismissAt]: back exactly [after] seconds later (not a tick before). */
    private fun backAfterSec(s: SleepCfg, dismissAt: LocalDateTime, after: Long): Boolean {
        val e = RemindersEngine()
        assertTrue(e.tick(dismissAt, 999.0, s, noBreak, emptyList()).show.any { it.key == "sleep" })
        e.answer("sleep", "dismiss", dismissAt, s, noBreak, emptyList())
        val early = e.tick(dismissAt.plusSeconds(after - 1), 999.0, s, noBreak, emptyList())
        val due = e.tick(dismissAt.plusSeconds(after), 999.0, s, noBreak, emptyList())
        return early.show.none { it.key == "sleep" } && due.show.any { it.key == "sleep" }
    }

    @Test fun the_bedtime_screen_comes_back_after_30_seconds() {
        val s = SleepCfg(on = true, bedtime = "23:00", wake = "07:00",
            tiers = listOf(ReminderTier("23:00", everySec = 15 * 60), ReminderTier("00:00", everySec = 30)))
        assertTrue(backAfterSec(s, at(28, 23, 10), 15 * 60))
        assertTrue(backAfterSec(s, at(29, 0, 10), 30))
    }

    @Test fun a_step_is_never_under_5_seconds() {
        val s = SleepCfg(on = true, bedtime = "23:00", wake = "07:00", tiers = listOf(ReminderTier("23:00", everySec = 1)))
        assertTrue(backAfterSec(s, at(28, 23, 10), 5))
        val flat = SleepCfg(on = true, bedtime = "23:00", wake = "07:00", repeatSec = 0, tiers = emptyList())
        assertTrue(backAfterSec(flat, at(28, 23, 10), 5))
    }

    // ---------- breaks ----------

    @Test fun a_break_every_30_seconds_with_a_10_second_snooze() {
        val b = BreakCfg(on = true, everySec = 30, snoozeSec = 10, text = "Up! {every} min of it.")
        val e = RemindersEngine()
        assertFalse(e.tick(t0, 0.0, noSleep, b, emptyList(), dt = 25.0).show.any { it.key == "break" })
        val shown = e.tick(t0.plusSeconds(5), 0.0, noSleep, b, emptyList(), dt = 5.0).show.single { it.key == "break" }
        assertTrue(shown.text, shown.text.startsWith("Up! 30 s of it."))                    // (not "30 s min")
        assertTrue(shown.buttons.any { it.label == "Snooze 10 s" })
        e.answer("break", "snooze", t0.plusSeconds(5), noSleep, b, emptyList())
        assertFalse(e.tick(t0.plusSeconds(10), 0.0, noSleep, b, emptyList(), dt = 5.0).show.any { it.key == "break" })
        assertTrue(e.tick(t0.plusSeconds(15), 0.0, noSleep, b, emptyList(), dt = 5.0).show.any { it.key == "break" })   // 10 s on
    }

    @Test fun a_break_in_whole_minutes_says_what_it_always_said() {
        val e = RemindersEngine()
        val shown = e.tick(t0, 0.0, noSleep, BreakCfg(on = true, every = 45), emptyList(), dt = 45 * 60.0).show.single { it.key == "break" }
        assertTrue(shown.text, shown.text.startsWith("You've been at the screen for 45 min. Take 5 min away from it."))
        assertTrue(shown.buttons.any { it.label == "Snooze 5 min" })
    }

    // ---------- your reminders ----------

    private fun popups(e: RemindersEngine, c: CustomCfg, from: LocalDateTime, seconds: Int): Int {
        var n = 0
        for (i in 0 until seconds / 5) {
            val r = e.tick(from.plusSeconds(i * 5L), 0.0, noSleep, noBreak, listOf(c), dt = 5.0)
            n += r.show.count { it.key == "custom:${c.id}" }
        }
        return n
    }

    @Test fun a_reminder_every_30_seconds_is_not_held_by_the_pace() {
        val c = CustomCfg("n", text = "Sit up", kind = "interval", everySec = 30)
        val e = RemindersEngine()
        assertEquals(1, popups(e, c, t0, 30))
        e.answer("custom:n", "done", t0.plusSeconds(30), noSleep, noBreak, listOf(c))
        assertEquals(1, popups(e, c, t0.plusSeconds(30), 30))                             // 30 s later, not 20 min
    }

    @Test fun a_reminder_of_a_minute_or_more_still_keeps_the_pace() {
        val c = CustomCfg("m", text = "Water", kind = "interval", everySec = 60)
        val e = RemindersEngine()
        assertEquals(1, popups(e, c, t0, 60))
        e.answer("custom:m", "done", t0.plusSeconds(60), noSleep, noBreak, listOf(c))
        assertEquals(0, popups(e, c, t0.plusSeconds(60), 5 * 60))                       // waits for the pace, as before
    }

    // ---------- "N min left" repeats ----------

    @Test fun the_warning_repeats_every_30_seconds_while_in_use() {
        val evening = Rule(RuleType.SCHEDULED, schedule = Schedule(SchedMode.BLOCK, listOf(Window((0..6).toList(), "21:00", "23:00"))))
        val items = listOf(Item("1", "Reddit", "com.r", ItemType.APP, rules = listOf(evening)))
        val w = BlockWatcher()
        val cfg = AlertsCfg(warnMin = 5, repeatSec = 30)
        fun check(t: LocalDateTime, inUse: Set<String>) = w.check(items, emptyList(), { _, _ -> 0 }, LimitClock.DEFAULT, t, inUse, cfg)
        assertEquals(1, check(at(28, 20, 56), setOf("1")).size)
        assertEquals(0, check(at(28, 20, 56, 25), setOf("1")).size)
        assertEquals(1, check(at(28, 20, 56, 30), setOf("1")).size)
        assertEquals(0, check(at(28, 20, 57, 30), emptySet()).size)                    // not in use: no repeat
    }
}
