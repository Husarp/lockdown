package com.husarp.lockdown

import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.Live
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.SchedMode
import com.husarp.lockdown.engine.Schedule
import com.husarp.lockdown.engine.TimeLeft
import com.husarp.lockdown.engine.TimeLeft.Kind
import com.husarp.lockdown.engine.Usage
import com.husarp.lockdown.engine.Window
import com.husarp.lockdown.ui.Status
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

/** One rule for "time left": the earliest blocker free, minutes allowed in blocked hours, blocked until. */
class TimeLeftTest {
    private val now = LocalDateTime.of(2026, 10, 12, 17, 0)           // a Monday, 17:00
    private val day = (0..6).toList()
    private val app = Item("a", "App", "com.app", ItemType.APP)

    private fun hours(start: String, end: String, allowance: Int? = null, mode: SchedMode = SchedMode.BLOCK) =
        Rule(RuleType.SCHEDULED, schedule = Schedule(mode, listOf(Window(day, start, end))), allowanceMin = allowance)

    private fun daily(min: Int) = Rule(RuleType.TIME_LIMIT, dailyLimitMin = min)

    private fun cfg(rules: List<Rule> = emptyList(), groupRules: List<Rule> = emptyList()): Config {
        val item = app.copy(rules = rules)
        return Config(items = listOf(item), groups = if (groupRules.isEmpty()) emptyList()
            else listOf(Group("g", "Group", rules = groupRules, memberIds = listOf("a"))))
    }

    private fun left(c: Config, usage: Usage = { _, _ -> 0 }) = Live.timeLeft(c, c.items[0], now, usage = usage)

    private fun used(owner: String, prefix: String, sec: Int): Usage = { o, b -> if (o == owner && b.startsWith(prefix)) sec else 0 }

    @Test fun free_without_limits_has_nothing_to_show() {
        val l = left(cfg(listOf(hours("22:00", "23:00"))))
        assertEquals(Kind.FREE, l.kind)
        assertEquals("", TimeLeft.line(l, now))
        assertEquals("Allowed", TimeLeft.chip(l))
        assertEquals(Kind.FREE, left(cfg()).kind)
    }

    @Test fun free_with_a_daily_limit_shows_the_time_left_and_the_fraction() {
        val l = left(cfg(listOf(daily(30))), used("item:a", "day:", 4 * 60))
        assertEquals(Kind.LEFT, l.kind)
        assertEquals("26 min left", TimeLeft.line(l, now))
        assertEquals(4f / 30f, l.fraction, 0.001f)
        assertFalse(l.low)
        assertEquals("Allowed", TimeLeft.chip(l))               // plenty left: the list stays "Allowed"
    }

    @Test fun nearly_out_is_running_low() {
        val c = cfg(listOf(daily(30)))
        val l = left(c, used("item:a", "day:", 27 * 60))
        assertEquals("3 min left", TimeLeft.line(l, now))
        assertTrue(l.low)
        assertEquals("3 min left", TimeLeft.chip(l))
        assertEquals(Status.SOON, Live.status(c, c.items[0], now, usage = used("item:a", "day:", 27 * 60)))
        assertEquals(Status.ALLOWED, Live.status(c, c.items[0], now, usage = used("item:a", "day:", 60)))
    }

    @Test fun minutes_round_up_and_long_times_read_as_hours() {
        assertEquals("5 min left", TimeLeft.line(left(cfg(listOf(daily(30))), used("item:a", "day:", 25 * 60 + 30)), now))
        assertEquals("2h 00m left", TimeLeft.line(left(cfg(listOf(daily(120)))), now))
    }

    @Test fun the_earliest_of_several_limits_wins() {
        val c = cfg(listOf(daily(30), Rule(RuleType.TIME_LIMIT, weeklyLimitMin = 600)))
        val usage: Usage = { _, b -> if (b.startsWith("day:")) 20 * 60 else if (b.startsWith("week:")) 598 * 60 else 0 }
        val l = left(c, usage)
        assertEquals("2 min left", TimeLeft.line(l, now))        // the week's, not the day's 10
        assertTrue(l.low)
    }

    @Test fun a_group_limit_counts_in_the_groups_pot() {
        val c = cfg(groupRules = listOf(daily(60)))
        val l = left(c, used("group:g", "day:", 55 * 60))
        assertEquals("5 min left", TimeLeft.line(l, now))
        assertTrue(l.low)
    }

    @Test fun blocked_hours_starting_soon_count_with_their_allowance() {
        assertEquals("8 min left", TimeLeft.line(left(cfg(listOf(hours("17:08", "20:00")))), now))
        assertTrue(left(cfg(listOf(hours("17:08", "20:00")))).low)
        // 5 min of allowance in those hours: it really blocks 5 min later
        assertEquals("13 min left", TimeLeft.line(left(cfg(listOf(hours("17:08", "20:00", allowance = 5)))), now))
        // far away: not a "left"
        assertEquals(Kind.FREE, left(cfg(listOf(hours("19:00", "20:00")))).kind)
        // soon beats a bigger limit left
        assertEquals("8 min left", TimeLeft.line(left(cfg(listOf(daily(30), hours("17:08", "20:00")))), now))
        // allowed hours (ALLOW mode) ending soon
        assertEquals("4 min left", TimeLeft.line(left(cfg(listOf(hours("10:00", "17:04", mode = SchedMode.ALLOW)))), now))
    }

    @Test fun an_opening_limit_shows_opens_when_it_is_what_ends_first() {
        val c = cfg(listOf(daily(60), Rule(RuleType.SWITCH_LIMIT, dailySwitchLimit = 10)))
        val usage: Usage = { _, b -> if (b.startsWith("day:")) 10 * 60 else if (b.startsWith("op:")) 9 else 0 }
        val l = left(c, usage)
        assertEquals("1 open left", TimeLeft.line(l, now))
        assertTrue(l.low)
        // few opens used, the time is what's nearly out
        val l2 = left(c, { _, b -> if (b.startsWith("day:")) 50 * 60 else if (b.startsWith("op:")) 1 else 0 })
        assertEquals("10 min left", TimeLeft.line(l2, now))
        assertEquals("3 opens left", TimeLeft.line(left(cfg(listOf(Rule(RuleType.SWITCH_LIMIT, dailySwitchLimit = 5))), { _, b -> if (b.startsWith("op:")) 2 else 0 }), now))
    }

    @Test fun adams_case_blocked_hours_with_5_minutes_allowed_beat_the_daily_limits_26() {
        val c = cfg(listOf(daily(30)), groupRules = listOf(hours("16:00", "18:00", allowance = 10)))
        // 4 min of the daily limit used, 5 of the group's 10 allowed minutes used (the allowance pot is shared)
        val usage: Usage = { o, b ->
            when {
                o == "item:a" && b.startsWith("day:") -> 4 * 60
                o == "group:g" && b.startsWith("win:") -> 5 * 60
                else -> 0
            }
        }
        val l = left(c, usage)
        assertEquals(Kind.ALLOWED, l.kind)
        assertEquals("Blocked · 5 min allowed", TimeLeft.line(l, now))
        assertEquals("5 min allowed", TimeLeft.chip(l))
        assertTrue(l.low)
        assertEquals(0.5f, l.fraction, 0.001f)
        assertEquals(Status.SOON, Live.status(c, c.items[0], now, usage = usage))
        assertEquals("5 min allowed", Live.chipText(c, c.items[0], now, usage = usage))
    }

    @Test fun a_daily_limit_nearer_than_the_allowance_wins_while_in_blocked_hours() {
        val c = cfg(listOf(daily(30)), groupRules = listOf(hours("16:00", "18:00", allowance = 10)))
        val l = left(c, used("item:a", "day:", 27 * 60))
        assertEquals(Kind.ALLOWED, l.kind)
        assertEquals("Blocked · 3 min allowed", TimeLeft.line(l, now))
    }

    @Test fun allowance_used_up_is_blocked_until_the_hours_end() {
        val c = cfg(listOf(daily(30)), groupRules = listOf(hours("16:00", "18:00", allowance = 10)))
        val usage: Usage = { o, b ->
            when {
                o == "item:a" && b.startsWith("day:") -> 4 * 60
                o == "group:g" && b.startsWith("win:") -> 10 * 60
                else -> 0
            }
        }
        val l = left(c, usage)
        assertEquals(Kind.BLOCKED, l.kind)
        assertEquals(LocalDateTime.of(2026, 10, 12, 18, 0), l.until)
        assertEquals("Blocked until 18:00", TimeLeft.line(l, now))
        assertFalse(TimeLeft.line(l, now).contains("min"))
        assertEquals(Status.BLOCKED, Live.status(c, c.items[0], now, usage = usage))
    }

    @Test fun blocked_waits_for_the_blocker_that_frees_last() {
        // hours end 18:00, but the daily limit is used up and only resets at midnight
        val c = cfg(listOf(daily(30)), groupRules = listOf(hours("16:00", "18:00")))
        val l = left(c, used("item:a", "day:", 30 * 60))
        assertEquals(Kind.BLOCKED, l.kind)
        assertEquals(LocalDateTime.of(2026, 10, 13, 0, 0), l.until)
        // the other way round: the limit resets before the hours end
        val c2 = cfg(listOf(daily(30)), groupRules = listOf(hours("16:00", "02:00")))
        assertEquals(LocalDateTime.of(2026, 10, 13, 2, 0), left(c2, used("item:a", "day:", 30 * 60)).until)
    }

    @Test fun permanent_temporary_and_modes_are_blockers_too() {
        val perm = left(cfg(listOf(Rule(RuleType.PERMANENT), daily(30))))
        assertEquals(Kind.BLOCKED, perm.kind)
        assertNull(perm.until)
        assertEquals("Blocked", TimeLeft.line(perm, now))
        val tempEnd = now.plusHours(2)
        val temp = left(cfg(listOf(Rule(RuleType.TEMPORARY, tempUntil = tempEnd.toString()), daily(30))))
        assertEquals(tempEnd, temp.until)
        // a running mode: until its end, or the latest of it and a rule's block
        val eff = Rules.effectiveRules(app.copy(rules = listOf(hours("16:00", "18:00"))), emptyList())
        val m = TimeLeft.of(eff, now, modeBlocks = true, modeUntil = LocalDateTime.of(2026, 10, 12, 19, 30))
        assertEquals(LocalDateTime.of(2026, 10, 12, 19, 30), m.until)
        assertEquals("Blocked until 19:30", TimeLeft.line(m, now))
        assertEquals(Kind.BLOCKED, TimeLeft.of(emptyList(), now, modeBlocks = true).kind)
        assertNull(TimeLeft.of(emptyList(), now, modeBlocks = true, modeUntil = null).until)
    }

    @Test fun a_paused_item_shows_nothing() {
        val c = cfg(listOf(Rule(RuleType.PERMANENT)))
        val p = c.copy(items = listOf(c.items[0].copy(disabled = true)))
        assertEquals(Kind.FREE, Live.timeLeft(p, p.items[0], now).kind)
        assertEquals("Paused", Live.chipText(p, p.items[0], now))
    }
}
