package com.husarp.lockdown

import com.husarp.lockdown.engine.Active
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
}
