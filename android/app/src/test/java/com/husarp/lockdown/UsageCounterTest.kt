package com.husarp.lockdown

import com.husarp.lockdown.engine.Active
import com.husarp.lockdown.engine.BlockPause
import com.husarp.lockdown.engine.EmergencyUse
import com.husarp.lockdown.engine.LimitClock
import com.husarp.lockdown.engine.QuotaExemptions
import com.husarp.lockdown.engine.Schedule
import com.husarp.lockdown.engine.SchedMode
import com.husarp.lockdown.engine.Window
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.SwitchCount
import com.husarp.lockdown.engine.UsageCounter
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import java.time.LocalTime

class UsageCounterTest {
    private val t0 = LocalDateTime.of(2026, 9, 28, 12, 0, 0)   // Monday noon

    private fun active(item: Item, groups: List<Group> = emptyList(), launched: Boolean = false) =
        Active(item, Rules.countedRules(item, groups), launched)

    @Test fun time_accrues_between_ticks() {
        val item = Item("1", "YT", "youtube.com", ItemType.SITE, rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30)))
        val c = UsageCounter()
        c.record(listOf(active(item)), t0)                       // first tick: baseline only
        assertEquals(0, c.usage("item:1", "day:2026-09-28"))
        c.record(listOf(active(item)), t0.plusSeconds(60))       // +60s of use
        assertEquals(60, c.usage("item:1", "day:2026-09-28"))
    }

    @Test fun accrued_time_makes_the_rule_block() {
        val item = Item("1", "YT", "youtube.com", ItemType.SITE, rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 1)))
        val c = UsageCounter()
        c.record(listOf(active(item)), t0)
        val later = t0.plusSeconds(60)
        c.record(listOf(active(item)), later)                    // 60s used, limit is 1 min
        val block = Rules.itemBlock(Rules.effectiveRules(item, emptyList()), later, c.usage)
        assertNotNull(block)
        assertEquals(com.husarp.lockdown.engine.Reason.LIMIT, block!!.reason)
    }

    @Test fun a_long_gap_is_not_counted() {
        val item = Item("1", "YT", "youtube.com", ItemType.SITE, rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30)))
        val c = UsageCounter()
        c.record(listOf(active(item)), t0)
        c.record(listOf(active(item)), t0.plusSeconds(300))      // 5-min gap: device asleep / service paused
        assertEquals(0, c.usage("item:1", "day:2026-09-28"))
    }

    @Test fun group_time_is_one_shared_pot() {
        val g = Group("7", "Fun", rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 90)), memberIds = listOf("1", "2"))
        val i1 = Item("1", "A", "a.com", ItemType.SITE)
        val i2 = Item("2", "B", "b.com", ItemType.SITE)
        val c = UsageCounter()
        c.record(listOf(active(i1, listOf(g))), t0)
        c.record(listOf(active(i1, listOf(g))), t0.plusSeconds(60))       // 60s on A
        c.record(listOf(active(i2, listOf(g))), t0.plusSeconds(60))
        c.record(listOf(active(i2, listOf(g))), t0.plusSeconds(120))      // 60s on B
        assertEquals(120, c.usage("group:7", "day:2026-09-28"))          // shared pot holds both
    }

    @Test fun switch_mode_counts_each_return_to_front() {
        val item = Item("1", "IG", "instagram.com", ItemType.SITE,
            rules = listOf(Rule(RuleType.SWITCH_LIMIT, dailySwitchLimit = 5, switchMode = SwitchCount.SWITCH)))
        val bucket = "op:i1SWITCH_LIMIT:2026-09-28"
        val c = UsageCounter()
        c.record(listOf(active(item)), t0)                       // enter front -> 1 switch
        assertEquals(1, c.usage("item:1", bucket))
        c.record(listOf(active(item)), t0.plusSeconds(5))        // still in front -> no new switch
        assertEquals(1, c.usage("item:1", bucket))
        c.record(emptyList(), t0.plusSeconds(10))               // left
        c.record(listOf(active(item)), t0.plusSeconds(15))       // back -> another switch
        assertEquals(2, c.usage("item:1", bucket))
    }

    @Test fun visit_mode_app_counts_only_on_launch() {
        val app = Item("1", "TikTok", "com.tiktok", ItemType.APP,
            rules = listOf(Rule(RuleType.SWITCH_LIMIT, dailySwitchLimit = 5, switchMode = SwitchCount.VISIT)))
        val bucket = "op:i1SWITCH_LIMIT:2026-09-28"
        val c = UsageCounter()
        c.record(listOf(active(app, launched = true)), t0)       // fresh launch -> counts
        assertEquals(1, c.usage("item:1", bucket))
        c.record(listOf(active(app, launched = false)), t0.plusSeconds(5))   // still open, not a launch
        assertEquals(1, c.usage("item:1", bucket))
    }

    @Test fun visit_mode_site_counts_after_the_gap() {
        val site = Item("1", "News", "news.com", ItemType.SITE,
            rules = listOf(Rule(RuleType.SWITCH_LIMIT, dailySwitchLimit = 5, switchMode = SwitchCount.VISIT, visitGapMin = 10)))
        val bucket = "op:i1SWITCH_LIMIT:2026-09-28"
        val c = UsageCounter()
        c.record(listOf(active(site)), t0)                       // first sight -> a visit
        assertEquals(1, c.usage("item:1", bucket))
        c.record(listOf(active(site)), t0.plusSeconds(30))       // continuous, within the gap -> not a new visit
        assertEquals(1, c.usage("item:1", bucket))
        c.record(emptyList(), t0.plusSeconds(40))
        c.record(listOf(active(site)), t0.plusSeconds(30 + 700)) // back after >10 min away -> a new visit
        assertEquals(2, c.usage("item:1", bucket))
    }

    @Test fun prune_drops_old_buckets_keeps_current() {
        val c = UsageCounter()
        c.counters["item:1\u0000day:2000-01-01"] = 99
        c.counters["item:1\u0000day:2026-09-28"] = 60
        c.counters["item:1\u0000win:i1SCHEDULED:2000-01-01T10:00"] = 30
        c.counters["group:7\u0000week:w2026-09-28"] = 42
        c.counters["group:7\u0000week:w2000-01-03"] = 7
        c.counters["group:7\u0000month:m2026-09"] = 8
        c.counters["group:7\u0000month:m2000-01"] = 9
        c.counters["item:1\u0000op:g7SWITCH_LIMIT:2000-01-01T03:00"] = 3
        c.counters["item:1\u0000op:g7SWITCH_LIMIT:2026-09-28"] = 2
        c.prune(t0)
        assertNull(c.counters["group:7\u0000week:w2000-01-03"])            // old week, month, openings: gone
        assertNull(c.counters["group:7\u0000month:m2000-01"])
        assertNull(c.counters["item:1\u0000op:g7SWITCH_LIMIT:2000-01-01T03:00"])
        assertEquals(8, c.usage("group:7", "month:m2026-09"))
        assertEquals(2, c.usage("item:1", "op:g7SWITCH_LIMIT:2026-09-28"))
        assertNull(c.counters["item:1\u0000day:2000-01-01"])               // old day: gone
        assertFalse(c.counters.containsKey("item:1\u0000win:i1SCHEDULED:2000-01-01T10:00"))  // past window: gone
        assertEquals(60, c.usage("item:1", "day:2026-09-28"))              // current day: kept
        assertEquals(42, c.usage("group:7", "week:w2026-09-28"))           // week: kept
    }

    @Test fun empty_snapshot_closes_the_gap_without_counting() {
        val item = Item("1", "YT", "youtube.com", ItemType.SITE, rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30)))
        val c = UsageCounter()
        c.record(listOf(active(item)), t0)
        c.record(emptyList(), t0.plusSeconds(60))                // nothing tracked in front
        c.record(listOf(active(item)), t0.plusSeconds(65))       // 5s of real use after coming back
        assertEquals(5, c.usage("item:1", "day:2026-09-28"))
        assertTrue(c.usage("item:1", "day:2026-09-28") < 60)
    }
    @Test fun anEmergencySpansThreeAmWithoutSpendingTheNewDay() {
        val start = LocalDateTime.of(2026, 6, 1, 2, 0)
        val end = start.plusMinutes(80)
        // Two overlapping tiers cover the example without exceeding the existing 60-minute tier maximum.
        val grants = listOf(
            EmergencyUse(start.toString(), "long", start.plusHours(1).toString(), listOf("1")),
            EmergencyUse(start.plusMinutes(50).toString(), "short", end.toString(), listOf("1")),
        )
        val ex = QuotaExemptions(uses = grants)
        val c = UsageCounter(clock = LimitClock(LocalTime.of(3, 0)))
        val item = Item("1", "Video", "video.example", ItemType.SITE,
            rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 5, weeklyLimitMin = 15, monthlyLimitMin = 60)))
        val observed = listOf(active(item))
        for (minute in 0L..80L) c.record(observed, start.plusMinutes(minute), ex)
        assertEquals(0, c.usage("item:1", "day:2026-06-01T03:00"))
        assertEquals(0, c.usage("item:1", "week:w2026-06-01"))
        assertEquals(0, c.usage("item:1", "month:m2026-06"))
        c.record(observed, end.plusMinutes(1), ex)
        assertEquals(60, c.usage("item:1", "day:2026-06-01T03:00"))
        assertEquals(60, c.usage("item:1", "week:w2026-06-01"))
        assertEquals(60, c.usage("item:1", "month:m2026-06"))
    }

    @Test fun aCrossingTickSplitsDayWeekAndMonthAndDoesNotAliasExemptTime() {
        val midnight = LocalDateTime.of(2026, 6, 1, 0, 0)
        val item = Item("1", "Video", "video.example", ItemType.SITE,
            rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 5, weeklyLimitMin = 15, monthlyLimitMin = 60)))
        for (paused in listOf(false, true)) {
            val pauses = if (paused) listOf(BlockPause(midnight.minusMinutes(1).toString(), midnight.plusMinutes(1).toString()))
                else emptyList()
            val ex = QuotaExemptions(pauses)
            val c = UsageCounter()
            c.record(listOf(active(item)), midnight.minusSeconds(2), ex)
            c.record(listOf(active(item)), midnight.plusSeconds(2), ex)
            for (bucket in listOf("day:2026-05-31", "day:2026-06-01", "week:w2026-05-25", "week:w2026-06-01",
                    "month:m2026-05", "month:m2026-06"))
                assertEquals(bucket, if (paused) 0 else 2, c.usage("item:1", bucket))
        }
    }

    @Test fun clippingBeforeRoundingKeepsPaidCarryAndTheOpeningBaseline() {
        val ex = QuotaExemptions(listOf(BlockPause(t0.plusNanos(750_000_000).toString(),
            t0.plusNanos(2_250_000_000).toString())))
        val item = Item("1", "Video", "video.example", ItemType.SITE,
            rules = listOf(Rule(RuleType.SWITCH_LIMIT, dailySwitchLimit = 3, switchMode = SwitchCount.VISIT)))
        val c = UsageCounter()
        c.record(emptyList(), t0, ex)
        c.record(listOf(active(item)), t0.plusSeconds(1), ex)
        c.record(listOf(active(item)), t0.plusSeconds(2), ex)
        c.record(listOf(active(item)), t0.plusNanos(2_500_000_000), ex)
        assertEquals(1, c.usage("item:1", "day:2026-09-28"))
        assertEquals(0, c.usage("item:1", "op:i1SWITCH_LIMIT:2026-09-28"))
        c.record(emptyList(), t0.plusSeconds(3), ex)
        c.record(listOf(active(item)), t0.plusMinutes(10), ex)
        assertEquals(1, c.usage("item:1", "op:i1SWITCH_LIMIT:2026-09-28"))
    }

    @Test fun anUnrelatedMemberPaysTheSharedAllowanceWhenAllowedHoursEnd() {
        val a = Item("1", "Video", "video.example", ItemType.SITE)
        val b = Item("2", "Game", "com.game", ItemType.APP)
        val day = t0.dayOfWeek.value - 1
        val schedule = Schedule(SchedMode.ALLOW, listOf(Window(listOf(day), "11:00", "12:00")))
        for (shared in listOf(true, false)) {
            val g = Group("7", "Shared", rules = listOf(Rule(RuleType.SCHEDULED,
                schedule = schedule, allowanceMin = 2, allowanceShared = shared)), memberIds = listOf(a.id, b.id),
                overrides = mapOf(a.id to mapOf("TIME_LIMIT" to Rule(RuleType.TIME_LIMIT, dailyLimitMin = 2))))
            val ex = QuotaExemptions(uses = listOf(
                EmergencyUse(t0.minusSeconds(1).toString(), "1", t0.plusMinutes(1).toString(), listOf(a.id)),
                EmergencyUse(t0.minusSeconds(5).toString(), "2", t0.plusMinutes(2).toString(), alerts = true),
            ))
            val c = UsageCounter()
            val items = listOf(active(a, listOf(g)), active(b, listOf(g)))
            c.record(items, t0.minusSeconds(2), ex)
            c.record(items, t0.plusSeconds(2), ex)
            val bucket = "win:g7SCHEDULED:2026-10-05T11:00"
            assertEquals(1, c.usage("item:1", "day:2026-09-28"))
            assertEquals(4, c.usage("item:2", "day:2026-09-28"))
            assertEquals(2, c.usage(if (shared) "group:7" else "item:2", bucket))
            assertEquals(0, c.usage("item:1", bucket))
        }
    }

}
