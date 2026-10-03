package com.husarp.lockdown

import com.husarp.lockdown.data.Config
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.BlockMethod
import com.husarp.lockdown.engine.EffRule
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.LimitClock
import com.husarp.lockdown.engine.Mode
import com.husarp.lockdown.engine.Modes
import com.husarp.lockdown.engine.Reason
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.SwitchCount
import com.husarp.lockdown.engine.Usage
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import java.time.LocalTime

/** Rules parity with the PC: per-weekday daily limits (0.84.12), a group's way of blocking its sites (0.84.11),
 *  the limit reset time, the visit gap and marking apps / sites for modes. */
class RulesParityTest {
    // 2026-09-25 is a Friday, 2026-09-28 a Monday
    private fun at(day: Int, h: Int, m: Int = 0) = LocalDateTime.of(2026, 9, 25 + day, h, m)
    private val fri = 0; private val sat = 1; private val sun = 2
    private val weekdays3hWeekend2h = List(5) { 180 } + List(2) { 120 }

    private fun perDay(days: List<Int?>, weekly: Int? = null): Rule {
        val (one, list) = Rules.makeDayLimits(days)
        return Rule(RuleType.TIME_LIMIT, dailyLimitMin = one, dailyLimitDays = list, weeklyLimitMin = weekly)
    }

    private fun eff(r: Rule) = EffRule(r, "item:1", "item:1", "item:1", "i1${r.type.name}")

    private fun used(bucket: String, sec: Int): Usage = { o, b -> if (o == "item:1" && b == bucket) sec else 0 }

    private fun clock(h: Int) = LimitClock(LocalTime.of(h, 0))

    // ---------- per-weekday daily limits ----------

    @Test fun one_amount_every_day_stays_one_amount_and_an_old_rule_reads_as_before() {
        assertEquals(60 to null, Rules.makeDayLimits(List(7) { 60 }))
        assertEquals(null to weekdays3hWeekend2h, Rules.makeDayLimits(weekdays3hWeekend2h))
        val old = Rule(RuleType.TIME_LIMIT, dailyLimitMin = 90)
        assertNull(Rules.dayLimits(old))
        assertEquals(mapOf("day" to 90), Rules.timeLimits(old, at(sat, 12)))
        assertEquals("3h 00m Mon–Fri, 2h 00m Sat–Sun", Rules.dayLimitsText(weekdays3hWeekend2h))
        assertEquals("1h 10m Mon–Sat, no limit Sun", Rules.dayLimitsText(List(6) { 70 } + listOf(null)))
    }

    @Test fun monday_3h_saturday_2h() {
        val r = perDay(weekdays3hWeekend2h)
        assertEquals(180, Rules.timeLimits(r, at(fri, 12))["day"])
        assertEquals(120, Rules.timeLimits(r, at(sat, 12))["day"])
        assertNull(Rules.ruleBlock(eff(r), at(sat, 12), used("day:2026-09-26", 119 * 60)))
        assertEquals(Reason.LIMIT to at(sun, 0), Rules.ruleBlock(eff(r), at(sat, 12), used("day:2026-09-26", 120 * 60)))
        assertNull(Rules.ruleBlock(eff(r), at(fri, 12), used("day:2026-09-25", 150 * 60)))       // Friday has 3 h
    }

    @Test fun reset_at_3am_a_day_belongs_to_the_weekday_it_started_on() {
        val c = clock(3)
        assertEquals(4, Rules.limitWeekday(at(sat, 1), c))                                       // still Friday
        assertEquals(5, Rules.limitWeekday(at(sat, 3), c))
        val r = perDay(weekdays3hWeekend2h)
        val friBucket = Rules.timeBucket("day", at(sat, 1), c)
        assertEquals("day:2026-09-25T03:00", friBucket)
        assertNull(Rules.ruleBlock(eff(r), at(sat, 1), used(friBucket, 150 * 60), c))           // 2.5 h < Friday's 3 h
        assertEquals(Reason.LIMIT to at(sat, 3), Rules.ruleBlock(eff(r), at(sat, 2), used(friBucket, 180 * 60), c))
        val sunBucket = Rules.timeBucket("day", at(3, 1), c)                                      // Monday 01:00: Sunday's
        assertEquals(Reason.LIMIT to at(3, 3), Rules.ruleBlock(eff(r), at(3, 1), used(sunBucket, 120 * 60), c))
    }

    @Test fun a_day_without_a_limit_doesnt_block_but_its_time_still_counts() {
        val r = perDay(List(6) { 180 } + listOf(null))
        val sunday = at(sun, 22)
        assertNull(Rules.ruleBlock(eff(r), sunday, used("day:2026-09-27", 10 * 3600)))
        assertFalse("day" in Rules.timeLimits(r, sunday))
        val item = Item("1", "Game", "com.game", ItemType.APP, rules = listOf(r))
        assertTrue(("item:1" to "day:2026-09-27") in Rules.usageTargets(item, Rules.effectiveRules(item, emptyList()), sunday))
    }

    @Test fun a_weekly_limit_still_stacks() {
        val r = perDay(weekdays3hWeekend2h, weekly = 600)
        val u: Usage = { _, b -> if (b == "week:w2026-09-21") 600 * 60 else 0 }
        assertEquals(Reason.LIMIT, Rules.ruleBlock(eff(r), at(sat, 12), u)!!.first)              // day fine, week out
    }

    @Test fun raising_saturday_only_needs_the_challenge_lowering_is_free() {
        val t = at(fri, 12)
        val old = perDay(weekdays3hWeekend2h)
        assertTrue(AntiBypass.ruleLooser(old, perDay(List(5) { 180 } + listOf(150, 120)), t))  // Saturday raised
        assertFalse(AntiBypass.ruleLooser(old, perDay(List(5) { 180 } + listOf(60, 120)), t))  // Saturday lowered
        assertTrue(AntiBypass.ruleLooser(old, perDay(List(6) { 180 } + listOf(null)), t))       // Sunday cleared
        assertFalse(AntiBypass.ruleLooser(old, perDay(weekdays3hWeekend2h, weekly = 900), t))   // a week limit added
    }

    @Test fun from_one_amount_to_per_weekday_and_back() {
        val t = at(fri, 12)
        val one = Rule(RuleType.TIME_LIMIT, dailyLimitMin = 150)
        assertTrue(AntiBypass.ruleLooser(one, perDay(weekdays3hWeekend2h), t))                 // weekdays above 150
        assertFalse(AntiBypass.ruleLooser(one, perDay(List(5) { 150 } + listOf(60, 60)), t))
        assertTrue(AntiBypass.ruleLooser(perDay(weekdays3hWeekend2h), Rule(RuleType.TIME_LIMIT, dailyLimitMin = 180), t))
        assertFalse(AntiBypass.ruleLooser(perDay(weekdays3hWeekend2h), Rule(RuleType.TIME_LIMIT, dailyLimitMin = 120), t))
    }

    @Test fun a_groups_and_a_members_weekday_amounts_are_guarded() {
        val t = at(fri, 12)
        val g = Group("g", "Fun", rules = listOf(perDay(weekdays3hWeekend2h)), memberIds = listOf("yt"),
            overrides = mapOf("yt" to mapOf("TIME_LIMIT" to perDay(List(5) { 60 } + listOf(90, 90)))))
        assertTrue(AntiBypass.groupLooser(g, g.copy(rules = listOf(perDay(List(5) { 180 } + listOf(180, 120)))), t))
        assertTrue(AntiBypass.groupLooser(g, g.copy(overrides = mapOf("yt" to mapOf("TIME_LIMIT" to perDay(List(5) { 60 } + listOf(120, 90))))), t))
        assertFalse(AntiBypass.groupLooser(g, g.copy(overrides = mapOf("yt" to mapOf("TIME_LIMIT" to perDay(List(5) { 60 } + listOf(30, 90))))), t))
    }

    // ---------- how a group blocks its member sites ----------

    private val yt = Item("yt", "YouTube", "youtube.com", ItemType.SITE, blockType = "dns", rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30)))
    private val group = Group("g", "Evening", rules = listOf(Rule(RuleType.PERMANENT)), memberIds = listOf("yt"))

    private fun wayNow(item: Item, groups: List<Group>, usage: Usage = { _, _ -> 0 }): String {
        val eff = Rules.effectiveRules(item, groups)
        return BlockMethod.blockingWay(item, groups, Rules.blockingRules(eff, at(fri, 12), usage))
    }

    @Test fun a_groups_way_applies_to_its_members_and_one_that_hasnt_chosen_blocks_as_each_is_set() {
        assertEquals("dns", wayNow(yt, listOf(group)))                                           // not chosen: its own
        assertEquals("close", wayNow(yt, listOf(group.copy(siteBlock = "close"))))
    }

    @Test fun the_items_own_rules_keep_its_own_way_and_both_together_do_both() {
        val g = group.copy(siteBlock = "back")
        val ownOut: Usage = { o, b -> if (o == "item:yt" && b.startsWith("day:")) 3600 else 0 }
        assertEquals("dns,back", wayNow(yt, listOf(g), ownOut))                                   // its limit + the group
        val noGroupRule = g.copy(rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 600)))
        assertEquals("dns", wayNow(yt, listOf(noGroupRule), ownOut))                              // only its own rule blocks
        assertEquals("close", BlockMethod.merge("back", "close"))                                 // leaving covers going back
    }

    @Test fun a_members_extra_rule_goes_the_groups_way_and_a_member_can_only_add() {
        val g = Group("g", "Evening", memberIds = listOf("yt"), siteBlock = "dns", memberBlocks = mapOf("yt" to "close"),
            overrides = mapOf("yt" to mapOf("PERMANENT" to Rule(RuleType.PERMANENT))))
        val bare = yt.copy(rules = emptyList())
        assertEquals("dns,close", wayNow(bare, listOf(g)))
        assertEquals("dns", BlockMethod.inGroup(bare, g, own = false))
    }

    @Test fun a_weaker_way_needs_the_challenge_a_stronger_one_is_free() {
        val t = at(fri, 12)
        val items = mapOf("yt" to yt.copy(blockType = "close"))
        val chosen = group.copy(siteBlock = "close")
        assertTrue(AntiBypass.groupLooser(chosen, chosen.copy(siteBlock = "back"), t, items))       // close -> back
        assertTrue(AntiBypass.groupLooser(chosen, chosen.copy(siteBlock = null, memberBlocks = emptyMap()), t,
            mapOf("yt" to yt.copy(blockType = "dns"))))                                           // back to its own, weaker
        assertFalse(AntiBypass.groupLooser(group.copy(siteBlock = "back"), group.copy(siteBlock = "close"), t, items))
        assertTrue(AntiBypass.groupLooser(group, group.copy(siteBlock = "dns"), t, items))         // first choice weaker than it was
        val withOwn = chosen.copy(siteBlock = "dns", memberBlocks = mapOf("yt" to "close"))
        assertTrue(AntiBypass.groupLooser(withOwn, withOwn.copy(memberBlocks = emptyMap()), t, items))   // member's own dropped
        assertFalse(AntiBypass.groupLooser(chosen.copy(siteBlock = "dns"), withOwn, t, items))      // member's own added
    }

    @Test fun an_emergency_unlock_leaves_only_permanent_rules_blocking() {
        val item = yt.copy(rules = listOf(Rule(RuleType.PERMANENT), Rule(RuleType.TIME_LIMIT, dailyLimitMin = 0)))
        val eff = Rules.effectiveRules(item, emptyList())
        assertEquals(2, Rules.blockingRules(eff, at(fri, 12)).size)
        assertEquals(listOf(RuleType.PERMANENT), Rules.blockingRules(eff, at(fri, 12), unlockedUntil = at(fri, 13)).map { it.rule.type })
    }

    // ---------- the limit reset time ----------

    @Test fun a_later_reset_time_never_starts_the_next_weekday_early() {
        val r = perDay(List(5) { 60 } + listOf(300, 300))
        val now = at(fri, 20)
        for (h in listOf(3, 11, 12, 13, 23)) {
            val c = LimitClock.of(LocalTime.of(h, 0), LimitClock.change(LimitClock.DEFAULT, LocalTime.of(h, 0), now))
            assertEquals(4, Rules.limitWeekday(now, c))
            assertEquals(4, Rules.limitWeekday(at(sat, h).minusMinutes(1), c))
            assertEquals("at $h", Reason.LIMIT to at(sat, h), Rules.ruleBlock(eff(r), now, used("day:2026-09-25", 3600), c))
        }
        assertFalse(LimitClock.resetLooser(LocalTime.MIDNIGHT, LocalTime.of(3, 0)))
    }

    @Test fun a_later_reset_time_cant_skip_a_stricter_weekday() {
        val r = perDay(List(4) { 60 } + listOf(null, 30, 60))                                  // Fri no limit, Sat 30 min
        for ((old, new, changedAt) in listOf(Triple(0, 23, at(fri, 1)), Triple(11, 13, at(fri, 12)))) {
            val c = LimitClock.of(LocalTime.of(new, 0), LimitClock.change(clock(old), LocalTime.of(new, 0), changedAt))
            val satNoon = at(sat, 12)
            val bucket = Rules.timeBucket("day", satNoon, c)
            assertEquals("$old->$new", 4, Rules.limitWeekday(satNoon, c))                       // still the carried Friday
            assertNull("$old->$new", Rules.ruleBlock(eff(r), satNoon, used(bucket, 29 * 60), c))
            assertEquals("$old->$new", Reason.LIMIT, Rules.ruleBlock(eff(r), satNoon, used(bucket, 30 * 60), c)!!.first)
        }
    }

    @Test fun a_reset_moved_across_noon_needs_the_challenge_either_way() {
        assertTrue(LimitClock.crossesNoon(LocalTime.of(11, 0), LocalTime.of(13, 0)))          // Sun's day would count as Mon
        assertTrue(LimitClock.crossesNoon(LocalTime.of(13, 0), LocalTime.of(11, 0)))          // Sun would count twice
        assertTrue(LimitClock.crossesNoon(LocalTime.MIDNIGHT, LocalTime.of(23, 0)))
        assertFalse(LimitClock.crossesNoon(LocalTime.MIDNIGHT, LocalTime.of(3, 0)))
        assertFalse(LimitClock.crossesNoon(LocalTime.of(13, 0), LocalTime.of(23, 0)))
    }

    @Test fun an_earlier_reset_time_leaves_the_running_day_alone_or_starts_a_fresh_one_now() {
        val old = clock(3)
        val now = at(sat, 1)                                                                    // Friday's day until Sat 03:00
        assertTrue(LimitClock.resetLooser(LocalTime.of(3, 0), LocalTime.MIDNIGHT))
        val later = LimitClock.of(LocalTime.MIDNIGHT, LimitClock.change(old, LocalTime.MIDNIGHT, now))
        assertEquals(old.period("day", now), later.period("day", now))                         // same day, same end
        assertEquals("2026-09-26", later.period("day", at(sat, 4)).first)                       // then midnight days
        val fresh = LimitClock.of(LocalTime.MIDNIGHT, LimitClock.startNow(old, now))
        assertEquals("2026-09-26", fresh.period("day", now).first)                              // a new day at once
    }

    @Test fun a_change_never_ends_the_running_week_early_nor_stretches_a_day_twice() {
        val sunNight = at(sun, 23)                                                              // week ends Mon 00:00
        val c = LimitClock.of(LocalTime.of(23, 0), LimitClock.change(LimitClock.DEFAULT, LocalTime.of(23, 0), sunNight))
        assertEquals("w2026-09-21", c.period("week", sunNight).first)
        val twice = LimitClock.of(LocalTime.of(23, 0),
            com.husarp.lockdown.engine.ResetCarry(at(fri, 0).toString(), at(5, 0).toString()))
        assertEquals(at(sun, 0), twice.day(at(fri, 12)).second)                                 // capped at two days
    }

    @Test fun the_config_keeps_the_reset_carry_and_old_files_load() {
        val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
        val carry = LimitClock.change(clock(3), LocalTime.of(5, 0), at(fri, 20))
        val cfg = Config(resetHour = 5, resetCarry = carry,
            items = listOf(Item("1", "G", "g", ItemType.APP, rules = listOf(perDay(weekdays3hWeekend2h)))),
            groups = listOf(group.copy(siteBlock = "close", memberBlocks = mapOf("yt" to "back"))))
        val back = json.decodeFromString(Config.serializer(), json.encodeToString(Config.serializer(), cfg))
        assertEquals(cfg, back)
        assertEquals(at(sat, 5), back.clock().day(at(fri, 20)).second)
        val old = json.decodeFromString(Config.serializer(), """{"resetHour":3,"groups":[{"id":"g","name":"G"}]}""")
        assertNull(old.resetCarry); assertNull(old.groups[0].siteBlock)
    }

    // ---------- opening limits: the visit gap ----------

    @Test fun the_visit_gap_unset_counts_as_five_minutes() {
        val t = at(fri, 12)
        val unset = Rule(RuleType.SWITCH_LIMIT, dailySwitchLimit = 10, switchMode = SwitchCount.VISIT)
        assertFalse(AntiBypass.ruleLooser(unset, unset.copy(visitGapMin = 3), t))                // shorter: more visits count
        assertTrue(AntiBypass.ruleLooser(unset, unset.copy(visitGapMin = 10), t))
        assertTrue(AntiBypass.ruleLooser(unset, unset.copy(dailySwitchLimit = null, weeklySwitchLimit = 50), t))
    }

    // ---------- Productive / Neutral / Distracting ----------

    @Test fun marking_something_less_blocked_than_a_mode_blocks_needs_the_challenge() {
        val modes = listOf(Mode("work", "Work", categories = listOf("distracting")), Mode("relax", "Relax"))
        assertTrue(Modes.categoryLooser(modes, "distracting", "productive"))
        assertFalse(Modes.categoryLooser(modes, "productive", "distracting"))
        assertFalse(Modes.categoryLooser(modes, "neutral", "productive"))                        // no mode blocks Neutral
        assertFalse(Modes.categoryLooser(modes, null, "productive"))                             // was in none
        val app = Item("1", "Game", "com.game", ItemType.APP)
        assertEquals("productive", Modes.itemCategory(app, mapOf("app:com.game" to "productive")))
    }
}
