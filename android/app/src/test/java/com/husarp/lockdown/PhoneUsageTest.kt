package com.husarp.lockdown

import com.husarp.lockdown.engine.Active
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Reason
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.Schedule
import com.husarp.lockdown.engine.UsageCounter
import com.husarp.lockdown.engine.Window
import com.husarp.lockdown.engine.foregroundItems
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

class PhoneUsageTest {
    private val now = LocalDateTime.of(2026, 9, 28, 12, 0)
    private val chrome = Item("chrome", "Chrome", "com.android.chrome", ItemType.APP)
    private val parent = Item("parent", "Example", "example.com", ItemType.SITE,
        rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 1)))
    private val child = parent.copy(id = "child", target = "sub.example.com")

    private fun actives(items: List<Item>, groups: List<Group> = emptyList()) =
        items.map { Active(it, Rules.countedRules(it, groups)) }

    private fun minute(counter: UsageCounter, active: List<Active>) {
        counter.record(active, now)
        counter.record(active, now.plusSeconds(60))
    }

    @Test fun overlapping_site_limits_both_accrue_and_block_in_either_order() {
        for (items in listOf(listOf(chrome, parent, child), listOf(child, parent, chrome))) {
            val foreground = foregroundItems(items, chrome.target, "sub.example.com")
            val counter = UsageCounter()
            minute(counter, actives(foreground))
            for (site in listOf(parent, child)) {
                assertEquals(site.id, 60, counter.usage("item:${site.id}", "day:2026-09-28"))
                assertEquals(Reason.LIMIT, Rules.itemBlock(Rules.effectiveRules(site, emptyList()),
                    now.plusSeconds(60), counter.usage)?.reason)
            }
        }
    }

    @Test fun foreground_skips_disabled_and_unrelated_items_and_deduplicates_hostnames() {
        val multi = child.copy(target = "sub.example.com example.com sub.example.com")
        val items = listOf(chrome.copy(disabled = true), parent.copy(disabled = true), multi,
            parent.copy(id = "unrelated", target = "other.com"))
        assertEquals(listOf(multi), foregroundItems(items, chrome.target, "www.sub.example.com"))
        assertTrue(foregroundItems(items, chrome.target, null).isEmpty())
        assertTrue(foregroundItems(items, chrome.target, "notexample.com").isEmpty())
    }

    @Test fun browser_and_site_spend_shared_group_time_once_per_tick() {
        val group = Group("fun", "Fun", memberIds = listOf(chrome.id, child.id),
            rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 60, weeklyLimitMin = 120, monthlyLimitMin = 240)))
        val counter = UsageCounter()
        minute(counter, actives(listOf(chrome, child), listOf(group)))
        for (bucket in listOf("day:2026-09-28", "week:w2026-09-28", "month:m2026-09"))
            assertEquals(bucket, 60, counter.usage("group:fun", bucket))
        assertEquals(60, counter.usage("item:chrome", "day:2026-09-28"))
        assertEquals(60, counter.usage("item:child", "day:2026-09-28"))
    }

    @Test fun shared_allowance_spends_once_but_member_allowances_each_spend() {
        val schedule = Schedule(windows = listOf(Window(listOf(0), "11:00", "13:00")))
        for (shared in listOf(true, false)) {
            val group = Group("fun", "Fun", memberIds = listOf(chrome.id, child.id),
                rules = listOf(Rule(RuleType.SCHEDULED, schedule = schedule, allowanceMin = 60, allowanceShared = shared)))
            val counter = UsageCounter()
            val active = actives(listOf(chrome, child), listOf(group))
            minute(counter, active)
            for (a in active) {
                val eff = a.counted.single { it.rule.type == RuleType.SCHEDULED }
                val bucket = Rules.allowanceBucket(eff, now.withHour(13))
                assertEquals(60, counter.usage(eff.allowanceOwner, bucket))
            }
        }
    }

    @Test fun separate_groups_and_member_overrides_keep_their_own_time() {
        val rule = Rule(RuleType.TIME_LIMIT, dailyLimitMin = 60, weeklyLimitMin = 120)
        val groups = listOf(
            Group("fun", "Fun", memberIds = listOf(chrome.id, child.id), rules = listOf(rule),
                overrides = mapOf(child.id to mapOf("TIME_LIMIT" to rule))),
            Group("other", "Other", memberIds = listOf(child.id), rules = listOf(rule)))
        val counter = UsageCounter()
        minute(counter, actives(listOf(chrome, child), groups))
        for (owner in listOf("group:fun", "group:other", "item:child"))
            assertEquals(owner, 60, counter.usage(owner, "week:w2026-09-28"))
    }
}
