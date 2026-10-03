package com.husarp.lockdown

import com.husarp.lockdown.engine.Active
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.Emergency
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Modes
import com.husarp.lockdown.engine.Reason
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.SchedMode
import com.husarp.lockdown.engine.Schedule
import com.husarp.lockdown.engine.SwitchCount
import com.husarp.lockdown.engine.UsageCounter
import com.husarp.lockdown.engine.Window
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

/**
 * A member's own limits in a group come ON TOP of the group's, never instead (PC 0.84.3): the group's rules
 * always apply and its time fills the group's pot; the member's extras only tighten.
 */
class GroupMergeTest {
    private val monNoon = LocalDateTime.of(2026, 9, 28, 12, 0)   // Monday
    private val allWeek = (0..6).toList()

    private val yt = Item("yt", "YouTube", "youtube.com", ItemType.SITE)
    private val tw = Item("tw", "Twitch", "twitch.tv", ItemType.SITE)

    private fun fun_(extras: Map<String, Rule> = emptyMap(), vararg rules: Rule) =
        Group("fun", "Fun", rules = rules.toList(), memberIds = listOf("yt", "tw"),
            overrides = if (extras.isEmpty()) emptyMap() else mapOf("yt" to extras))

    private fun sched(start: String, end: String, mode: SchedMode = SchedMode.BLOCK, allowance: Int? = null, shared: Boolean = true) =
        Rule(RuleType.SCHEDULED, schedule = Schedule(mode, listOf(Window(allWeek, start, end))),
            allowanceMin = allowance, allowanceShared = shared)

    /** Use [item] for [minutes] starting at [from], one tick a minute. */
    private fun use(c: UsageCounter, item: Item, groups: List<Group>, from: LocalDateTime, minutes: Int): LocalDateTime {
        val active = listOf(Active(item, Rules.countedRules(item, groups)))
        c.record(active, from)
        var t = from
        repeat(minutes) { t = t.plusMinutes(1); c.record(active, t) }
        c.record(emptyList(), t)
        return t
    }

    private fun block(item: Item, groups: List<Group>, now: LocalDateTime, c: UsageCounter) =
        Rules.itemBlock(Rules.effectiveRules(item, groups), now, c.usage)

    @Test fun youtube_stops_at_its_own_hour_and_that_hour_fills_the_group() {
        // Fun: 2 h a day shared; YouTube's own extra: 1 h a day
        val g = fun_(mapOf("TIME_LIMIT" to Rule(RuleType.TIME_LIMIT, dailyLimitMin = 60)), Rule(RuleType.TIME_LIMIT, dailyLimitMin = 120))
        val c = UsageCounter()
        val t = use(c, yt, listOf(g), monNoon, 60)
        assertEquals(Reason.LIMIT, block(yt, listOf(g), t, c)!!.reason)          // YouTube: its own hour is used
        assertEquals(3600, c.usage("group:fun", "day:2026-09-28"))               // ...and it filled Fun's pot
        assertNull(block(tw, listOf(g), t, c))                                   // Twitch has Fun's other hour
        val t2 = use(c, tw, listOf(g), t, 60)
        assertEquals(Reason.LIMIT, block(tw, listOf(g), t2, c)!!.reason)         // Fun's 2 h are gone
    }

    @Test fun when_the_group_runs_out_first_the_member_stops_too() {
        val g = fun_(mapOf("TIME_LIMIT" to Rule(RuleType.TIME_LIMIT, dailyLimitMin = 60)), Rule(RuleType.TIME_LIMIT, dailyLimitMin = 120))
        val c = UsageCounter()
        val t = use(c, tw, listOf(g), monNoon, 110)                               // Twitch eats 110 of Fun's 120 min
        val t2 = use(c, yt, listOf(g), t, 10)                                     // YouTube's 10 min end Fun's 2 h
        assertEquals(Reason.LIMIT, block(yt, listOf(g), t2, c)!!.reason)         // its own hour isn't used up, still blocked
    }

    @Test fun an_old_looser_extra_no_longer_loosens_the_group() {
        // a "customised" 3 h in a 2 h group used to replace the group's 2 h; now the group's 2 h still holds
        val g = fun_(mapOf("TIME_LIMIT" to Rule(RuleType.TIME_LIMIT, dailyLimitMin = 180)), Rule(RuleType.TIME_LIMIT, dailyLimitMin = 120))
        val c = UsageCounter()
        val t = use(c, yt, listOf(g), monNoon, 120)
        assertEquals(Reason.LIMIT, block(yt, listOf(g), t, c)!!.reason)
    }

    @Test fun blocked_hours_add_up() {
        // Fun blocked 15-18, YouTube's own 13-14: YouTube is blocked 13-14 and 15-18, Twitch 15-18 only
        val g = fun_(mapOf("SCHEDULED" to sched("13:00", "14:00")), sched("15:00", "18:00"))
        val c = UsageCounter()
        val at = { h: Int, m: Int -> monNoon.withHour(h).withMinute(m) }
        assertNotNull(block(yt, listOf(g), at(13, 30), c))
        assertNull(block(tw, listOf(g), at(13, 30), c))
        assertNotNull(block(yt, listOf(g), at(16, 0), c))
        assertNotNull(block(tw, listOf(g), at(16, 0), c))
        assertNull(block(yt, listOf(g), at(14, 30), c))
    }

    @Test fun allowed_hours_narrow() {
        // Fun allowed only 10-20; YouTube's own "allowed only" 12-14 narrows it, never widens it
        val g = fun_(mapOf("SCHEDULED" to sched("12:00", "14:00", SchedMode.ALLOW)), sched("10:00", "20:00", SchedMode.ALLOW))
        val c = UsageCounter()
        val at = { h: Int -> monNoon.withHour(h) }
        assertNull(block(yt, listOf(g), at(13), c))
        assertNotNull(block(yt, listOf(g), at(16), c))       // outside its own window
        assertNull(block(tw, listOf(g), at(16), c))          // the others keep the group's window
        assertNotNull(block(yt, listOf(g), at(21), c))       // outside the group's
        val wider = fun_(mapOf("SCHEDULED" to sched("08:00", "23:00", SchedMode.ALLOW)), sched("10:00", "20:00", SchedMode.ALLOW))
        assertNotNull(block(yt, listOf(wider), at(21), c))   // a wider own window doesn't open the group's
    }

    @Test fun the_shared_allowance_stays_shared_and_youtube_spends_it() {
        // Fun blocked all day with 30 min allowed, shared; YouTube's own blocked hours have their own 5 min
        val g = fun_(mapOf("SCHEDULED" to sched("11:00", "13:00", allowance = 5)), sched("00:00", "23:59", allowance = 30))
        val c = UsageCounter()
        val t = use(c, yt, listOf(g), monNoon, 5)
        assertEquals(Reason.SCHEDULE, block(yt, listOf(g), t, c)!!.reason)   // YouTube's own 5 min are spent
        assertNull(block(tw, listOf(g), t, c))                               // Fun's shared pot has 25 min left
        val t2 = use(c, tw, listOf(g), t, 25)
        assertNotNull(block(tw, listOf(g), t2, c))                           // YouTube's 5 min came out of it too
    }

    @Test fun openings_fill_the_group_and_the_member() {
        val g = fun_(mapOf("SWITCH_LIMIT" to Rule(RuleType.SWITCH_LIMIT, dailySwitchLimit = 1, switchMode = SwitchCount.SWITCH)),
            Rule(RuleType.SWITCH_LIMIT, dailySwitchLimit = 3, switchMode = SwitchCount.SWITCH))
        val c = UsageCounter()
        var t = use(c, yt, listOf(g), monNoon, 1)                          // YouTube opened once: its own 1 and Fun's 1 of 3
        assertNull(block(yt, listOf(g), t, c))
        t = use(c, yt, listOf(g), t.plusMinutes(1), 1)                       // a second time: over its own 1
        assertEquals(Reason.SWITCHES, block(yt, listOf(g), t, c)!!.reason)
        assertNull(block(tw, listOf(g), t, c))                               // Fun: 2 of 3
        t = use(c, tw, listOf(g), t.plusMinutes(1), 1)
        t = use(c, tw, listOf(g), t.plusMinutes(1), 1)                       // Fun: 4 > 3
        assertEquals(Reason.SWITCHES, block(tw, listOf(g), t, c)!!.reason)
    }

    @Test fun blocked_until_is_the_latest_of_the_rules_blocking_it() {
        // Fun blocked 15-18, YouTube's own 17-20: at 17:30 YouTube is blocked until 20:00, not 18:00
        val g = fun_(mapOf("SCHEDULED" to sched("17:00", "20:00")), sched("15:00", "18:00"))
        val b = block(yt, listOf(g), monNoon.withHour(17).withMinute(30), UsageCounter())!!
        assertEquals(monNoon.withHour(20).withMinute(0), b.until)
    }

    @Test fun a_disabled_group_still_counts_the_member_both_ways() {
        val g = fun_(mapOf("TIME_LIMIT" to Rule(RuleType.TIME_LIMIT, dailyLimitMin = 60)), Rule(RuleType.TIME_LIMIT, dailyLimitMin = 120))
            .copy(disabled = true)
        val c = UsageCounter()
        val t = use(c, yt, listOf(g), monNoon, 10)
        assertEquals(600, c.usage("group:fun", "day:2026-09-28"))
        assertEquals(600, c.usage("item:yt", "day:2026-09-28"))
        assertNull(block(yt, listOf(g), t, c))                               // but nothing blocks
    }

    @Test fun adding_or_tightening_an_extra_is_free_removing_or_relaxing_one_needs_the_challenge() {
        val base = fun_(emptyMap(), Rule(RuleType.TIME_LIMIT, dailyLimitMin = 120))
        val oneHour = fun_(mapOf("TIME_LIMIT" to Rule(RuleType.TIME_LIMIT, dailyLimitMin = 60)), Rule(RuleType.TIME_LIMIT, dailyLimitMin = 120))
        assertFalse(AntiBypass.groupLooser(base, oneHour, monNoon))                                   // added
        assertFalse(AntiBypass.groupLooser(base, base.copy(overrides = mapOf("yt" to mapOf(
            "TIME_LIMIT" to Rule(RuleType.TIME_LIMIT, dailyLimitMin = 180)))), monNoon))              // even a big one: the group still holds
        assertFalse(AntiBypass.groupLooser(oneHour, oneHour.copy(overrides = mapOf("yt" to mapOf(
            "TIME_LIMIT" to Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30)))), monNoon))               // tightened
        assertTrue(AntiBypass.groupLooser(oneHour, base, monNoon))                                    // removed
        assertTrue(AntiBypass.groupLooser(oneHour, oneHour.copy(overrides = mapOf("yt" to mapOf(
            "TIME_LIMIT" to Rule(RuleType.TIME_LIMIT, dailyLimitMin = 90)))), monNoon))               // relaxed
        val hours = fun_(mapOf("SCHEDULED" to sched("13:00", "14:00")))
        assertTrue(AntiBypass.groupLooser(hours, fun_(mapOf("SCHEDULED" to sched("13:00", "13:30"))), monNoon))   // hours changed
        val ranOut = fun_(mapOf("TEMPORARY" to Rule(RuleType.TEMPORARY, tempUntil = "2026-09-28T11:00:00")))
        assertFalse(AntiBypass.groupLooser(ranOut, fun_(), monNoon))                                  // run out: clearing it is free
        val running = fun_(mapOf("TEMPORARY" to Rule(RuleType.TEMPORARY, tempUntil = "2026-09-28T15:00:00")))
        assertTrue(AntiBypass.groupLooser(running, fun_(), monNoon))
    }

    @Test fun emergency_never_lifts_a_permanent_block_from_the_group() {
        val g = fun_(emptyMap(), Rule(RuleType.PERMANENT))
        val effs = Rules.effectiveRules(yt, listOf(g))
        assertTrue(Rules.permanent(effs))
        assertEquals(Reason.PERMANENT, Rules.itemBlock(effs, monNoon, unlockedUntil = monNoon.plusMinutes(20))!!.reason)
    }

    @Test fun an_emergency_dated_in_the_future_still_counts_as_used() {
        // used with the clock set a week forward, then set back: it doesn't hand the use back
        val uses = Emergency.usesLeft(listOf(monNoon.plusDays(7)), monNoon, "week", allowed = 3)
        assertEquals(2, uses.left)
    }

    @Test fun a_scheduled_rule_on_its_allowance_is_soon_not_allowed() {
        val r = sched("00:00", "23:59", allowance = 30)
        val e = Rules.effectiveRules(yt.copy(rules = listOf(r)), emptyList()).single()
        assertNull(Rules.ruleBlock(e, monNoon))
        assertEquals("soon", Rules.ruleState(e, monNoon))
    }

    @Test fun a_neutral_subdomain_does_not_take_youtube_out_of_modes() {
        val item = Item("yt", "YouTube", "youtube.com", ItemType.SITE)
        assertEquals("distracting", Modes.itemCategory(item, mapOf("site:music.youtube.com" to "neutral")))
        assertEquals("neutral", Modes.itemCategory(item, mapOf("site:youtube.com" to "neutral")))
        val two = Item("x", "X", "a.com b.com", ItemType.SITE)
        assertEquals("distracting", Modes.itemCategory(two, mapOf("site:a.com" to "neutral", "site:b.com" to "distracting")))
        assertEquals("productive", Modes.itemCategory(two, mapOf("site:a.com" to "productive")))
    }

    @Test fun removing_a_member_with_extra_limits_needs_the_challenge() {
        val g = fun_(mapOf("TIME_LIMIT" to Rule(RuleType.TIME_LIMIT, dailyLimitMin = 60)), Rule(RuleType.TIME_LIMIT, dailyLimitMin = 120))
        assertTrue(AntiBypass.groupLooser(g, g.copy(memberIds = listOf("tw"), overrides = emptyMap()), monNoon))
        assertTrue(AntiBypass.groupLooser(g, null, monNoon))                                        // deleting the group
        assertTrue(AntiBypass.groupLooser(g, g.copy(disabled = true), monNoon))                     // pausing it
    }

    @Test fun extras_of_an_item_that_is_not_a_member_do_nothing() {
        val g = Group("fun", "Fun", rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 120)), memberIds = listOf("tw"),
            overrides = mapOf("yt" to mapOf("PERMANENT" to Rule(RuleType.PERMANENT))))
        assertTrue(Rules.effectiveRules(yt, listOf(g)).isEmpty())
        assertFalse(AntiBypass.groupLooser(g, g.copy(overrides = emptyMap()), monNoon))              // enforced nothing
    }

    @Test fun an_extra_of_a_kind_the_group_lacks_still_applies() {
        // a group with only night hours can give YouTube 1 h a day of its own
        val g = fun_(mapOf("TIME_LIMIT" to Rule(RuleType.TIME_LIMIT, dailyLimitMin = 60)), sched("22:00", "06:00"))
        val c = UsageCounter()
        val t = use(c, yt, listOf(g), monNoon, 60)
        assertEquals(Reason.LIMIT, block(yt, listOf(g), t, c)!!.reason)
        assertNull(block(tw, listOf(g), t, c))
    }
}
