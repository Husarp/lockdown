package com.husarp.lockdown

import com.husarp.lockdown.engine.EffRule
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.LimitClock
import com.husarp.lockdown.engine.Reason
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.SchedMode
import com.husarp.lockdown.engine.Schedule
import com.husarp.lockdown.engine.Window
import com.husarp.lockdown.engine.Usage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import java.time.LocalTime

class RulesEngineTest {
    // 2026-09-28 is a Monday (weekday 0)
    private val monNoon = LocalDateTime.of(2026, 9, 28, 12, 0)
    private val mon23 = LocalDateTime.of(2026, 9, 28, 23, 0)

    private fun eff(rule: Rule, owner: String = "item:1", key: String = "i1${rule.type.name}") =
        EffRule(rule, owner, "item:1", owner, key)

    private fun usageOf(vararg pairs: Pair<Pair<String, String>, Int>): Usage {
        val m = pairs.toMap()
        return { owner, bucket -> m[owner to bucket] ?: 0 }
    }

    @Test fun permanent() {
        val (reason, until) = Rules.ruleBlock(eff(Rule(RuleType.PERMANENT)), monNoon)!!
        assertEquals(Reason.PERMANENT, reason); assertNull(until)
    }

    @Test fun temporary() {
        val future = Rule(RuleType.TEMPORARY, tempUntil = "2026-09-28T18:00:00")
        assertEquals(Reason.TEMPORARY, Rules.ruleBlock(eff(future), monNoon)!!.first)
        val past = Rule(RuleType.TEMPORARY, tempUntil = "2026-09-28T06:00:00")
        assertNull(Rules.ruleBlock(eff(past), monNoon))
    }

    @Test fun schedule_block_crosses_midnight() {
        val r = Rule(RuleType.SCHEDULED, schedule = Schedule(SchedMode.BLOCK, listOf(Window(listOf(0), "22:00", "06:00"))))
        val (reason, until) = Rules.ruleBlock(eff(r), mon23)!!            // Mon 23:00 -> blocked until Tue 06:00
        assertEquals(Reason.SCHEDULE, reason)
        assertEquals(LocalDateTime.of(2026, 9, 29, 6, 0), until)
        assertNull(Rules.ruleBlock(eff(r), monNoon))                      // Mon noon -> not in window
    }

    @Test fun schedule_allow_blocks_outside() {
        val r = Rule(RuleType.SCHEDULED, schedule = Schedule(SchedMode.ALLOW, listOf(Window(listOf(0), "09:00", "17:00"))))
        assertNull(Rules.ruleBlock(eff(r), monNoon))                      // inside allowed window
        val evening = LocalDateTime.of(2026, 9, 28, 18, 0)
        assertEquals(Reason.SCHEDULE, Rules.ruleBlock(eff(r), evening)!!.first)   // outside -> blocked
    }

    @Test fun time_limit_day() {
        val r = Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30)
        val over = usageOf(("item:1" to "day:2026-09-28") to 30 * 60)
        assertEquals(Reason.LIMIT, Rules.ruleBlock(eff(r), monNoon, over)!!.first)
        val under = usageOf(("item:1" to "day:2026-09-28") to 20 * 60)
        assertNull(Rules.ruleBlock(eff(r), monNoon, under))
    }

    @Test fun switch_limit_day() {
        val r = Rule(RuleType.SWITCH_LIMIT, dailySwitchLimit = 5)
        val e = eff(r)
        val over = usageOf(("item:1" to "op:${e.ruleKey}:2026-09-28") to 6)
        assertEquals(Reason.SWITCHES, Rules.ruleBlock(e, monNoon, over)!!.first)
        val at = usageOf(("item:1" to "op:${e.ruleKey}:2026-09-28") to 5)   // the 5th open is allowed; the 6th blocks
        assertNull(Rules.ruleBlock(e, monNoon, at))
    }

    @Test fun allowance_holds_off_the_block() {
        val r = Rule(RuleType.SCHEDULED, schedule = Schedule(SchedMode.BLOCK, listOf(Window(listOf(0), "00:00", "23:59"))), allowanceMin = 15)
        val e = eff(r)
        val until = Rules.scheduleUntil(r.schedule!!, monNoon)!!
        val used = usageOf(("item:1" to Rules.allowanceBucket(e, until)) to 10 * 60)   // 10 of 15 min used
        assertNull(Rules.ruleBlock(e, monNoon, used))                                   // still has allowance
        val spent = usageOf(("item:1" to Rules.allowanceBucket(e, until)) to 15 * 60)
        assertEquals(Reason.SCHEDULE, Rules.ruleBlock(e, monNoon, spent)!!.first)
    }

    @Test fun item_block_priority() {
        val perm = eff(Rule(RuleType.PERMANENT))
        val sched = eff(Rule(RuleType.SCHEDULED, schedule = Schedule(SchedMode.BLOCK, listOf(Window(listOf(0), "00:00", "23:59")))))
        val b = Rules.itemBlock(listOf(sched, perm), monNoon)!!
        assertEquals(Reason.PERMANENT, b.reason)     // permanent outranks schedule
    }

    @Test fun emergency_unlock_lifts_every_block_but_a_permanent_one() {
        val until = LocalDateTime.of(2026, 9, 28, 13, 0)
        val sched = eff(Rule(RuleType.SCHEDULED, schedule = Schedule(SchedMode.BLOCK, listOf(Window(listOf(0), "00:00", "23:59")))))
        assertNull(Rules.itemBlock(listOf(sched), monNoon, unlockedUntil = until))
        val perm = eff(Rule(RuleType.PERMANENT))
        assertEquals(Reason.PERMANENT, Rules.itemBlock(listOf(perm, sched), monNoon, unlockedUntil = until)!!.reason)
    }

    @Test fun group_limit_is_a_shared_pot() {
        val item = Item("1", "YT", "youtube.com", ItemType.SITE)
        val group = Group("7", "Fun", rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 90)), memberIds = listOf("1"))
        val effs = Rules.effectiveRules(item, listOf(group))
        assertEquals(1, effs.size)
        assertEquals("group:7", effs[0].usageOwner)   // inherited group limit counts against the shared pot
    }

    @Test fun a_member_extra_comes_on_top_of_the_group_rule() {
        val item = Item("1", "YT", "youtube.com", ItemType.SITE)
        val extra = Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30)
        val group = Group("7", "Fun", rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 90)),
            memberIds = listOf("1"), overrides = mapOf("1" to mapOf("TIME_LIMIT" to extra)))
        val effs = Rules.effectiveRules(item, listOf(group))
        assertEquals(2, effs.size)                    // the group's rule is kept, the extra is added
        assertEquals("group:7", effs[0].usageOwner)   // the group's limit: the shared pot
        assertEquals(90, effs[0].rule.dailyLimitMin)
        assertEquals("item:1", effs[1].usageOwner)    // the extra: the member's own pot
        assertEquals(30, effs[1].rule.dailyLimitMin)
        assertEquals("Fun", effs[1].extraOf)
    }

    @Test fun disabled_item_has_no_rules_but_still_counts() {
        val item = Item("1", "YT", "youtube.com", ItemType.SITE, disabled = true, rules = listOf(Rule(RuleType.PERMANENT)))
        assertTrue(Rules.effectiveRules(item, emptyList()).isEmpty())        // paused: not enforced
        assertEquals(1, Rules.countedRules(item, emptyList()).size)          // but usage still counted
    }

    @Test fun limit_clock_custom_reset_time() {
        val clock = LimitClock(resetTime = LocalTime.of(3, 0))
        // at 02:00 on the 28th, the limit day started 03:00 on the 27th
        val (start, end) = clock.day(LocalDateTime.of(2026, 9, 28, 2, 0))
        assertEquals(LocalDateTime.of(2026, 9, 27, 3, 0), start)
        assertEquals(LocalDateTime.of(2026, 9, 28, 3, 0), end)
        assertEquals("2026-09-27T03:00", clock.period("day", LocalDateTime.of(2026, 9, 28, 2, 0)).first)
    }

    @Test fun default_clock_day_key_is_plain_date() {
        assertEquals("2026-09-28", LimitClock.DEFAULT.period("day", monNoon).first)
        assertEquals("w2026-09-28", LimitClock.DEFAULT.period("week", monNoon).first)   // Monday-start week
        assertEquals("m2026-09", LimitClock.DEFAULT.period("month", monNoon).first)
    }
}
