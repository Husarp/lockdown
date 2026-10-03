package com.husarp.lockdown

import com.husarp.lockdown.engine.Alerts
import com.husarp.lockdown.engine.AlertsCfg
import com.husarp.lockdown.engine.BlockWatcher
import com.husarp.lockdown.engine.BlockedVisit
import com.husarp.lockdown.engine.DayDetail
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.HistoryLog
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.LimitClock
import com.husarp.lockdown.engine.MinuteAcc
import com.husarp.lockdown.engine.MinuteRow
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.SchedMode
import com.husarp.lockdown.engine.Schedule
import com.husarp.lockdown.engine.Stats
import com.husarp.lockdown.engine.SwitchEvent
import com.husarp.lockdown.engine.SwitchTracker
import com.husarp.lockdown.engine.Usage
import com.husarp.lockdown.engine.Window
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.LocalDateTime

class AlertsAndHistoryTest {
    // 2026-09-28 is a Monday
    private fun at(h: Int, m: Int, s: Int = 0, day: Int = 28) = LocalDateTime.of(2026, 9, day, h, m, s)
    private val clock = LimitClock.DEFAULT
    private val evening = Rule(RuleType.SCHEDULED, schedule = Schedule(SchedMode.BLOCK, listOf(Window((0..6).toList(), "21:00", "23:00"))))
    private fun limit(min: Int) = Rule(RuleType.TIME_LIMIT, dailyLimitMin = min)
    private fun used(sec: Int): Usage = { o, b -> if (o == "item:1" && b.startsWith("day:")) sec else 0 }

    // ---------- messages / the block notice ----------

    @Test fun messages_fill_placeholders_and_only_own_text_counts() {
        val now = at(12, 0)
        assertEquals("YouTube: time limit reached - blocked until 21:00.",
            Alerts.format(Alerts.DEFAULT_MESSAGES["limit"]!!, "YouTube", "limit", at(21, 0), now))
        assertEquals("X is blocked until Tuesday 08:00.", Alerts.format("{site} is blocked until {until}.", "X", "schedule", at(8, 0, day = 29), now))
        assertEquals("X: further notice, permanently blocked {oops}",
            Alerts.format("{site}: {until}, {reason} {oops}", "X", "permanent", null, now))
        val cfg = AlertsCfg(messages = mapOf("limit" to Alerts.DEFAULT_MESSAGES["limit"]!!, "schedule" to "  ", "mode" to "Not now, {site}."))
        assertNull(Alerts.ownMessage(cfg, "limit"))         // the default text = the notice keeps its usual words
        assertNull(Alerts.ownMessage(cfg, "schedule"))
        assertEquals("Not now, {site}.", Alerts.ownMessage(cfg, "mode"))
    }

    @Test fun notice_follows_item_reason_and_cooldown() {
        val min = 60_000L
        assertTrue(Alerts.shouldNotify(null, true, null, 0, 30))
        assertFalse(Alerts.shouldNotify(null, false, null, 0, 30))           // reason off
        assertTrue(Alerts.shouldNotify("on", false, null, 0, 30))            // the item says always
        assertFalse(Alerts.shouldNotify("off", true, null, 0, 30))           // the item says quiet
        assertFalse(Alerts.shouldNotify(null, true, 0, 29 * min, 30))        // explained 29 min ago
        assertTrue(Alerts.shouldNotify(null, true, 0, 30 * min, 30))
        assertFalse(Alerts.enabled(AlertsCfg(visits = mapOf("limit" to false)), "limit"))
        assertTrue(Alerts.enabled(AlertsCfg(), "limit"))
    }

    // ---------- the next block ----------

    @Test fun next_block_schedule_limit_and_unlock() {
        val sched = Rules.effectiveRules(Item("1", "A", "com.a", ItemType.APP, rules = listOf(evening)), emptyList())
        assertEquals(at(21, 0), Rules.nextBlock(sched, at(20, 50))!!.at)
        val lim = Rules.effectiveRules(Item("1", "A", "com.a", ItemType.APP, rules = listOf(limit(60))), emptyList())
        assertNull(Rules.nextBlock(lim, at(12, 0), used(50 * 60), clock, inUse = false))   // only predicted in use
        assertEquals(at(12, 10), Rules.nextBlock(lim, at(12, 0), used(50 * 60), clock, inUse = true)!!.at)
        // an emergency unlock until 21:30 inside the blocked hours: its end, as the "unlock" kind
        val nb = Rules.nextBlock(sched, at(21, 10), unlockedUntil = at(21, 30))!!
        assertEquals(at(21, 30), nb.at); assertNull(nb.eff)
        assertNull(Rules.nextBlock(sched, at(22, 50), unlockedUntil = at(23, 30)))    // free again by then: nothing to say
    }

    // ---------- the watcher ----------

    @Test fun warns_once_then_repeats_only_while_in_use() {
        val items = listOf(Item("1", "Reddit", "com.r", ItemType.APP, rules = listOf(evening)))
        val w = BlockWatcher()
        val cfg = AlertsCfg(warnMin = 5, repeatMin = 2)
        assertEquals(emptyList<String>(), w.check(items, emptyList(), { _, _ -> 0 }, clock, at(20, 50), emptySet(), cfg))
        assertEquals(listOf("Reddit will be blocked in 5 min (21:00)."), w.check(items, emptyList(), { _, _ -> 0 }, clock, at(20, 55), emptySet(), cfg))
        assertEquals(emptyList<String>(), w.check(items, emptyList(), { _, _ -> 0 }, clock, at(20, 58), emptySet(), cfg))   // not in use: once
        val again = w.check(items, emptyList(), { _, _ -> 0 }, clock, at(20, 58), setOf("1"), cfg)
        assertEquals(listOf("Reddit will be blocked in 2 min (21:00)."), again)
        assertTrue(again[0] in w.urgent)
        assertEquals(emptyList<String>(), w.check(items, emptyList(), { _, _ -> 0 }, clock, at(20, 59), setOf("1"), cfg))   // < 2 min since
    }

    @Test fun block_started_is_one_line_per_group_and_not_on_the_first_look() {
        val items = listOf(Item("1", "Reddit", "com.r", ItemType.APP), Item("2", "X", "com.x", ItemType.APP), Item("3", "Solo", "com.s", ItemType.APP, rules = listOf(evening)))
        val groups = listOf(Group("g", "Evenings", rules = listOf(evening), memberIds = listOf("1", "2")))
        val w = BlockWatcher()
        val cfg = AlertsCfg(warn = false)
        assertEquals(emptyList<String>(), w.check(items, groups, { _, _ -> 0 }, clock, at(21, 30), emptySet(), cfg))   // already blocked: no news
        val w2 = BlockWatcher()
        w2.check(items, groups, { _, _ -> 0 }, clock, at(20, 59), emptySet(), cfg)
        val msgs = w2.check(items, groups, { _, _ -> 0 }, clock, at(21, 0), setOf("3"), cfg)
        assertEquals(listOf("Evenings started - 2 things blocked until 23:00.", "Solo is now blocked until 23:00."), msgs)
        assertTrue("Solo is now blocked until 23:00." in w2.urgent)
        assertFalse("Evenings started - 2 things blocked until 23:00." in w2.urgent)
        // the group's warning was one line too
        val w3 = BlockWatcher()
        assertEquals(listOf("Evenings starts in 1 min (21:00): Reddit, X will be blocked.", "Solo will be blocked in 1 min (21:00)."),
            w3.check(items, groups, { _, _ -> 0 }, clock, at(20, 59), emptySet(), AlertsCfg(started = false)))
    }

    @Test fun time_limit_warning_and_started_reason() {
        val items = listOf(Item("1", "YouTube", "com.y", ItemType.APP, rules = listOf(limit(60))))
        val w = BlockWatcher()
        assertEquals(listOf("YouTube: 4 min of the time limit left."), w.check(items, emptyList(), used(56 * 60), clock, at(12, 0), setOf("1"), AlertsCfg()))
        assertEquals(listOf("YouTube is now blocked until 00:00 - time limit reached."),
            w.check(items, emptyList(), used(60 * 60), clock, at(12, 4), setOf("1"), AlertsCfg()).map { it.replace("Tuesday ", "") })
    }

    @Test fun allowance_note_once_per_stretch() {
        val r = evening.copy(allowanceMin = 10)
        val items = listOf(Item("1", "Reddit", "com.r", ItemType.APP, rules = listOf(r)))
        val w = BlockWatcher()
        val usage: Usage = { _, b -> if (b.startsWith("win:")) 4 * 60 else 0 }
        val msgs = w.check(items, emptyList(), usage, clock, at(21, 30), setOf("1"), AlertsCfg(warnMin = 1))
        assertEquals("Reddit is blocked now - you have 6 min of your 10 min allowance left (until 23:00).", msgs[0])
        assertFalse(w.check(items, emptyList(), usage, clock, at(21, 31), setOf("1"), AlertsCfg(warnMin = 1)).any { "allowance left (until" in it })
    }

    @Test fun pause_says_nothing_then_blocks_start_again() {
        val items = listOf(Item("1", "Reddit", "com.r", ItemType.APP, rules = listOf(Rule(RuleType.PERMANENT))))
        val w = BlockWatcher()
        w.check(items, emptyList(), { _, _ -> 0 }, clock, at(12, 0), emptySet(), AlertsCfg())
        assertEquals(emptyList<String>(), w.check(items, emptyList(), { _, _ -> 0 }, clock, at(12, 1), emptySet(), AlertsCfg(), paused = true))
        assertEquals(listOf("Reddit is now blocked."), w.check(items, emptyList(), { _, _ -> 0 }, clock, at(13, 0), emptySet(), AlertsCfg()))
    }

    @Test fun unlock_ending_is_warned() {
        val items = listOf(Item("1", "Reddit", "com.r", ItemType.APP, rules = listOf(evening)))
        val w = BlockWatcher()
        val msgs = w.check(items, emptyList(), { _, _ -> 0 }, clock, at(21, 26), emptySet(), AlertsCfg()) { at(21, 30) }
        assertEquals(listOf("Emergency unlock ends in 4 min: Reddit will be blocked again."), msgs)
    }

    // ---------- history ----------

    @Test fun history_lines_round_trip_and_roll_up() {
        val day = LocalDate.of(2026, 9, 28)
        val text = listOf(
            HistoryLog.minuteLine(MinuteRow(at(9, 0), "com.a", "", 60, 60)),
            HistoryLog.minuteLine(MinuteRow(at(9, 1), "com.chrome", "x.com", 30, 30)),
            HistoryLog.switchLine(SwitchEvent(at(9, 1, 5), "com.chrome", "x.com")),
            HistoryLog.blockedLine(BlockedVisit(at(9, 2, 7), "7", "reddit.com")),
            "garbage\tline", "",
        ).joinToString("\n")
        val d = HistoryLog.parseDay(day, text)
        assertEquals(2, d.rows.size); assertEquals(30, d.rows[1].seconds); assertEquals("x.com", d.rows[1].site)
        assertEquals(at(9, 1, 5), d.switches[0].ts)
        assertEquals(BlockedVisit(at(9, 2, 7), "7", "reddit.com"), d.blocked[0])
        val daily = HistoryLog.parseDaily((HistoryLog.rollUpLines(day, HistoryLog.totals(d)) + HistoryLog.rollUpLines(day, HistoryLog.totals(d))).joinToString("\n"))
        assertEquals(180, daily[day]!!.total)                                // a day written twice adds up
        assertEquals(2, daily[day]!!.switches["com.chrome" to "x.com"])
        assertEquals(2, daily[day]!!.blocked["7" to "reddit.com"])
    }

    @Test fun retention_keeps_32_days_and_never_takes_recent_days_for_a_wrong_clock() {
        val today = LocalDate.of(2026, 9, 28)
        val days = (0..40L).map { today.minusDays(it) }
        assertEquals(days.filter { it.isBefore(today.minusDays(32)) }.sorted(), HistoryLog.toRollUp(days, today))
        // only 20 days of use: nothing goes, even though they're long ago
        val sparse = (100..119L).map { today.minusDays(it) }
        assertEquals(emptyList<LocalDate>(), HistoryLog.toRollUp(sparse, today))
        // the clock jumped 60 days ahead: real detail stays until 33 days of use have followed it
        val real = (0..9L).map { today.minusDays(it) }
        assertEquals(emptyList<LocalDate>(), HistoryLog.toRollUp(real, today.plusDays(60)))
    }

    @Test fun minutes_and_switches() {
        val acc = MinuteAcc()
        assertEquals(emptyList<MinuteRow>(), acc.add(at(9, 0, 10), "a", "", 3.0))
        acc.add(at(9, 0, 40), "a", "", 40.0); acc.add(at(9, 0, 59), "b", "", 30.0)   // 73 s: capped at a minute
        val done = acc.add(at(9, 1, 2), "b", "", 3.0)
        assertEquals(listOf(MinuteRow(at(9, 0), "a", "", 43, 43), MinuteRow(at(9, 0), "b", "", 17, 17)), done)
        val t = SwitchTracker(home = "launcher")
        assertEquals("a", t.seen(at(9, 0), "a", "")!!.app)
        assertFalse(t.seen(at(9, 1), "launcher", "")!!.switch)               // the home screen is passed through (the visit ends)
        assertFalse(t.seen(at(9, 2), "a", "")!!.switch)                      // back to the same app: not a switch
        assertNull(t.seen(at(9, 2, 30), "a", ""))
        assertEquals("x.com", t.seen(at(9, 3), "chrome", "x.com")!!.site)
        assertEquals("y.com", t.seen(at(9, 4), "chrome", "y.com")!!.site)    // another site in the same browser
    }

    @Test fun a_visit_ends_when_the_phone_is_put_away_and_picks_up_again() {
        val t = SwitchTracker(home = "launcher")
        val day = LocalDate.of(2026, 9, 28)
        val ev = listOfNotNull(
            t.seen(at(9, 0), "insta", ""),
            t.end(at(9, 5)),                                                  // screen off
            t.end(at(9, 6)),                                                  // ... ends once
            t.seen(at(12, 0), "insta", ""),                                   // the same app again: not a switch
            t.seen(at(12, 2), "launcher", ""),                                // home ends the visit too
            t.seen(at(12, 3), "insta", ""),
            t.seen(at(12, 4), "mail", ""),
        )
        assertEquals(listOf(true, false, false, false, false, true), ev.map { it.switch })
        // the lines round-trip; the daily totals count only the switches
        val d = HistoryLog.parseDay(day, ev.joinToString("\n") { HistoryLog.switchLine(it) })
        assertEquals(ev, d.switches)
        assertEquals(mapOf(("insta" to "") to 1, ("mail" to "") to 1), HistoryLog.totals(d).switches)
        // 5 min, 2 min, 1 min of Instagram (not 3 hours), then mail until now
        val s = Stats.switchSummary(d.switches, at(12, 10))
        assertEquals(2, s.count)
        assertEquals(listOf(("app" to "insta") to 300.0, ("app" to "insta") to 120.0, ("app" to "insta") to 60.0, ("app" to "mail") to 360.0),
            Stats.visits(d.switches, at(12, 10)))
    }

    @Test fun blocked_tries_dont_shorten_the_usual_visit() {
        // in an app, then blocked tries on Reddit: the visit ends, Reddit gets no visit of its own
        val t = SwitchTracker(home = "launcher")
        val history = listOfNotNull(t.seen(at(9, 0), "com.a", ""), t.end(at(9, 5)), t.end(at(9, 6)))
        val items = listOf(Item("1", "Reddit", "reddit.com", ItemType.SITE))
        val blocked = listOf(BlockedVisit(at(9, 5), "1", "reddit.com"), BlockedVisit(at(9, 6), "1", "reddit.com"))
        assertEquals(2.0 * Stats.DEFAULT_VISIT_SEC, Stats.timeSaved(blocked, items, history, at(10, 0)), 0.01)
    }

    @Test fun csv_export() {
        val day = LocalDate.of(2026, 9, 28)
        val out = HistoryLog.csv(mapOf(day to mapOf(("com.a" to "") to 90, ("chrome" to "a,b.com") to 600))) { app, _ -> if (app == "com.a") "productive" else null }
        assertEquals("date,app,site,minutes,active minutes,category\n2026-09-28,chrome,\"a,b.com\",10.0,10.0,\n2026-09-28,com.a,,1.5,1.5,productive\n", out)
    }

    // ---------- stats ----------

    @Test fun time_saved_uses_the_usual_visit() {
        val items = listOf(Item("1", "Reddit", "reddit.com", ItemType.SITE), Item("2", "Game", "com.game", ItemType.APP))
        val history = listOf(SwitchEvent(at(9, 0), "chrome", "old.reddit.com"), SwitchEvent(at(9, 2), "com.a", ""),
            SwitchEvent(at(10, 0), "chrome", "reddit.com"), SwitchEvent(at(10, 4), "com.a", ""))
        val blocked = listOf(BlockedVisit(at(11, 0), "1", "reddit.com"), BlockedVisit(at(11, 5), "2", "com.game"))
        // reddit: visits of 2 and 4 min -> 3 min; the game: no history -> 5 min
        assertEquals(180.0 + 300.0, Stats.timeSaved(blocked, items, history, at(10, 5)), 0.01)
    }

    @Test fun weekday_heatmap_averages_each_weekday() {
        val mon1 = LocalDate.of(2026, 9, 21); val mon2 = LocalDate.of(2026, 9, 28)
        val rows = (0 until 40).map { MinuteRow(LocalDateTime.of(mon1, java.time.LocalTime.of(9, it)), "a", "", 60, 60) }
        val heat = Stats.weekdayHeatmap(rows, listOf(mon1, mon2))
        assertEquals(2, heat[0][9])        // 40 min on one Monday, 0 on the other: 20 min average
        assertEquals(0, heat[1][9])
        assertEquals(DayDetail(), HistoryLog.parseDay(mon1, ""))
    }
}
