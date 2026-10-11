package com.husarp.lockdown

import com.husarp.lockdown.engine.AlertsCfg
import com.husarp.lockdown.engine.Active
import com.husarp.lockdown.engine.BlockWatcher
import com.husarp.lockdown.engine.Cadence
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.GreyUsedCache
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.LimitClock
import com.husarp.lockdown.engine.Mode
import com.husarp.lockdown.engine.ModeBlocksCache
import com.husarp.lockdown.engine.Modes
import com.husarp.lockdown.engine.RemindersEngine
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.SchedMode
import com.husarp.lockdown.engine.Schedule
import com.husarp.lockdown.engine.UsageCounter
import com.husarp.lockdown.engine.Window
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

/** The battery work: how often the loops run (Cadence), and the lookups that are kept instead of worked out again. */
class BatteryTest {
    // ---------- Cadence: the screen on keeps every pace it always had ----------

    @Test fun screen_on_keeps_the_quick_pace() {
        assertEquals(3000L, Cadence.stepMs(true))
        assertEquals(5000L, Cadence.alertMs(true))
        assertEquals(60_000L, Cadence.bedtimeMs(true))
        assertEquals(1000L, Cadence.linkPollMs(true))
        assertEquals(3000L, Cadence.linkTickMs(true))
        assertEquals(RemindersEngine.TICK_SEC * 1000L, Cadence.remindMs(true, 0.0))
        assertEquals(RemindersEngine.TICK_SEC * 1000L, Cadence.remindMs(true, 99_999.0))   // (screen on but locked for hours)
    }

    @Test fun screen_off_stretches_every_loop() {
        assertEquals(30_000L, Cadence.stepMs(false))
        assertEquals(60_000L, Cadence.alertMs(false))
        assertEquals(5 * 60_000L, Cadence.bedtimeMs(false))
        assertEquals(60_000L, Cadence.linkPollMs(false))
        assertEquals(60_000L, Cadence.linkTickMs(false))
        assertTrue(Cadence.stepMs(false) > Cadence.stepMs(true))
        assertTrue(Cadence.alertMs(false) > Cadence.alertMs(true))
        assertTrue(Cadence.bedtimeMs(false) > Cadence.bedtimeMs(true))
    }

    @Test fun reminders_stay_quick_for_the_minute_the_engine_still_counts_as_use() {
        val quick = RemindersEngine.TICK_SEC * 1000L
        assertEquals(quick, Cadence.remindMs(false, 0.0))
        assertEquals(quick, Cadence.remindMs(false, RemindersEngine.USING_IDLE_SEC.toDouble()))
        assertEquals(Cadence.REMIND_OFF_MS, Cadence.remindMs(false, RemindersEngine.USING_IDLE_SEC + RemindersEngine.TICK_SEC + 0.0))
        assertEquals(Cadence.REMIND_OFF_MS, Cadence.remindMs(false, 3600.0))
        // a clock-time reminder has half an hour of slack (RemindersEngine.LATE_FIRE_MIN): the stretch can't miss it
        assertTrue(Cadence.REMIND_OFF_MS < RemindersEngine.LATE_FIRE_MIN * 60_000L)
    }

    @Test fun island_sync_is_quick_only_when_someone_waits() {
        assertEquals(5000L, Cadence.linkSyncMs(screenOn = true, islandInFront = true, pending = false))
        assertEquals(5000L, Cadence.linkSyncMs(screenOn = true, islandInFront = false, pending = true))
        assertEquals(30_000L, Cadence.linkSyncMs(screenOn = true, islandInFront = false, pending = false))
        // the screen off: slow, but well inside the 10 minutes after which main calls the helper late
        assertEquals(3 * 60_000L, Cadence.linkSyncMs(screenOn = false, islandInFront = true, pending = true))
        assertTrue(Cadence.linkSyncMs(false, false, false) < 10 * 60_000L)
        // main can't be reached: asking every 5 s does not help
        assertEquals(30_000L, Cadence.linkSyncMs(screenOn = true, islandInFront = true, pending = true, failing = true))
    }

    @Test fun dismiss_countdown_wakes_once_a_second_on_the_second() {
        assertEquals(0L, Cadence.countdownMs(readyAt = 10_000, nowMs = 10_000))              // over: nothing to schedule
        assertEquals(0L, Cadence.countdownMs(readyAt = 10_000, nowMs = 12_000))
        assertEquals(1020L, Cadence.countdownMs(readyAt = 10_000, nowMs = 0))               // "10" -> "9" in a second
        assertEquals(770L, Cadence.countdownMs(readyAt = 10_000, nowMs = 250))              // 9.75 s left: "9" at 9.0 s
        assertEquals(1020L, Cadence.countdownMs(readyAt = 10_000, nowMs = 1000))
        // every wake-up lands in a new second, never twice in the same one (it used to be four times a second)
        var now = 0L; var wakes = 0; var last = Int.MAX_VALUE
        while (now < 10_000) {
            val left = RemindersEngine.waitLeft(10_000, now)
            assertTrue("left $left at $now", left < last)
            last = left; wakes++
            now += Cadence.countdownMs(10_000, now)
        }
        assertEquals(10, wakes)
    }

    // ---------- the usage file is written only when something was counted ----------

    private val t0 = LocalDateTime.of(2026, 9, 28, 12, 0, 0)

    @Test fun usage_counter_reports_a_change_only_when_a_counter_moved() {
        val item = Item("1", "YT", "com.yt", ItemType.APP, rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30)))
        val a = Active(item, Rules.countedRules(item, emptyList()))
        val c = UsageCounter()
        c.record(emptyList(), t0)
        c.record(emptyList(), t0.plusSeconds(3))
        c.record(emptyList(), t0.plusSeconds(6))           // the screen off, or nothing limited in front
        assertEquals(0L, c.changes)
        c.record(listOf(a), t0.plusSeconds(9))             // in front now: its first 3 s count
        val afterFirst = c.changes
        c.record(listOf(a), t0.plusSeconds(12))            // 3 s of use
        assertTrue(c.changes > afterFirst)
        val now = c.changes
        c.record(emptyList(), t0.plusSeconds(15))
        c.record(emptyList(), t0.plusSeconds(18))
        assertEquals(now, c.changes)
        c.touch()                                          // the Island helper's time added from outside
        assertEquals(now + 1, c.changes)
    }

    @Test fun pruning_old_buckets_counts_as_a_change() {
        val c = UsageCounter(mutableMapOf("item:1\u0000day:2026-01-01" to 500, "item:1\u0000day:2026-09-28" to 10))
        c.prune(t0)
        assertEquals(1, c.counters.size)
        assertEquals(1L, c.changes)
        c.prune(t0)                                        // nothing left to drop
        assertEquals(1L, c.changes)
    }

    // ---------- the running mode's lookups are kept ----------

    private val apps = listOf(Item("1", "Reddit", "com.r", ItemType.APP), Item("2", "News", "news.com other.org", ItemType.SITE), Item("3", "Mail", "com.m", ItemType.APP))
    private val mode = Mode("m", "Work", items = listOf("1", "2"), extra = emptyList())

    @Test fun mode_blocks_match_the_walk_over_all_items() {
        val b = ModeBlocksCache().get(mode, apps, emptyList(), emptyMap())
        assertTrue(b.blocksApp("com.r")); assertTrue(b.blocksApp("COM.R"))
        assertFalse(b.blocksApp("com.m"))
        // the same answers the old per-call code gave: the host itself, or any host under it, dot-aligned
        val old = Modes.blockedHosts(Modes.targets(mode, apps, emptyList(), emptyMap()))
        for (h in listOf("news.com", "www.news.com", "a.b.news.com", "OTHER.org", "x.other.org", "badnews.com", "news.com.evil.io", "org", "com", "", "news.co")) {
            val expected = old.any { h.lowercase() == it || h.lowercase().endsWith(".$it") }
            assertEquals("host '$h'", expected, b.blocksHost(h))
        }
        assertTrue(b.blocksHost("news.com")); assertFalse(b.blocksHost("badnews.com"))
    }

    @Test fun mode_blocks_are_kept_until_something_changes() {
        val cache = ModeBlocksCache()
        val groups = emptyList<Group>(); val cats = emptyMap<String, String>()
        val first = cache.get(mode, apps, groups, cats)
        assertSame(first, cache.get(mode, apps, groups, cats))                       // kept: not walked again
        val edited = apps + Item("4", "Game", "com.g", ItemType.APP)
        val second = cache.get(mode.copy(items = listOf("1", "2", "4")), edited, groups, cats)
        assertNotSame(first, second)                                                // a new config / mode: worked out again
        assertTrue(second.blocksApp("com.g")); assertFalse(first.blocksApp("com.g"))
        assertNotSame(second, cache.get(mode, apps, groups, cats))                   // and back
    }

    @Test fun grey_scan_is_kept_and_false_without_grey() {
        val cache = GreyUsedCache()
        assertFalse(cache.used(apps, emptyList()))
        val greyApp = Item("5", "Game", "com.g", ItemType.APP, blockType = "grey")
        assertTrue(cache.used(apps + greyApp, emptyList()))
        assertTrue(cache.used(apps, listOf(Group("g", "Fun", appBlock = "grey", memberIds = listOf("1")))))
        assertFalse(cache.used(apps, listOf(Group("g", "Fun", appBlock = "close", memberIds = listOf("1")))))
    }

    // ---------- the alert watcher skips what can't matter ----------

    private val clock = LimitClock.DEFAULT
    private fun at(h: Int, m: Int) = LocalDateTime.of(2026, 9, 28, h, m)
    private val evening = Rule(RuleType.SCHEDULED, schedule = Schedule(SchedMode.BLOCK, listOf(Window((0..6).toList(), "21:00", "23:00"))))

    @Test fun watcher_with_both_alerts_off_says_nothing_and_forgets() {
        val items = listOf(Item("1", "Reddit", "com.r", ItemType.APP, rules = listOf(evening)))
        val w = BlockWatcher()
        w.check(items, emptyList(), { _, _ -> 0 }, clock, at(20, 0), emptySet(), AlertsCfg(started = true, warn = false))
        assertEquals(emptySet<String>(), w.prevBlocked)
        val off = AlertsCfg(warn = false, started = false)
        assertEquals(emptyList<String>(), w.check(items, emptyList(), { _, _ -> 0 }, clock, at(20, 56), setOf("1"), off))
        assertNull(w.prevBlocked)
        assertTrue(w.urgent.isEmpty())
        // switched on again: the first look only records what is blocked, it announces nothing
        assertEquals(emptyList<String>(), w.check(items, emptyList(), { _, _ -> 0 }, clock, at(21, 30), emptySet(), AlertsCfg(warn = false)))
    }

    @Test fun watcher_skips_items_without_any_rule_but_not_group_members() {
        val items = listOf(Item("1", "Plain", "com.p", ItemType.APP), Item("2", "Member", "com.m", ItemType.APP),
            Item("3", "Own", "com.o", ItemType.APP, rules = listOf(evening)))
        val groups = listOf(Group("g", "Evenings", rules = listOf(evening), memberIds = listOf("2")))
        val msgs = BlockWatcher().check(items, groups, { _, _ -> 0 }, clock, at(20, 59), emptySet(), AlertsCfg(started = false))
        assertEquals(listOf("Evenings starts in 1 min (21:00): Member will be blocked.", "Own will be blocked in 1 min (21:00)."), msgs)
        // a disabled group's members are plain items again
        val off = groups.map { it.copy(disabled = true) }
        assertEquals(listOf("Own will be blocked in 1 min (21:00)."),
            BlockWatcher().check(items, off, { _, _ -> 0 }, clock, at(20, 59), emptySet(), AlertsCfg(started = false)))
    }
}
