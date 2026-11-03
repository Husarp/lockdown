package com.husarp.lockdown

import com.husarp.lockdown.data.BlockNow
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.engine.*
import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDateTime
import java.time.LocalTime

class BlockNowTest {
    private val now = LocalDateTime.of(2026, 11, 3, 23, 30)
    private val limit = Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30)
    private val hours = Rule(RuleType.SCHEDULED, schedule = Schedule())
    private val item = Item("a", "App", "app", ItemType.APP, rules = listOf(limit, hours))

    @Test fun hourCrossesMidnight() {
        assertEquals(LocalDateTime.of(2026, 11, 4, 0, 30), BlockNow.hour(now))
    }
    @Test fun todayEndsAtNextMidnight() {
        assertEquals(LocalDateTime.of(2026, 11, 4, 0, 0), BlockNow.midnight(now))
        assertEquals(now.toLocalDate().plusDays(1).atStartOfDay(), BlockNow.midnight(now.toLocalDate().atStartOfDay()))
    }
    @Test fun customTodayAndTomorrow() {
        assertEquals(now.toLocalDate().atTime(23, 45), BlockNow.until(now, LocalTime.of(23, 45), false))
        assertEquals(now.toLocalDate().plusDays(1).atTime(8, 0), BlockNow.until(now, LocalTime.of(8, 0), true))
    }
    @Test fun preservesLimitsAndHoursAndNeverShortens() {
        val first = BlockNow.merge(item.rules, now.plusHours(2))
        val second = BlockNow.merge(first, now.plusHours(1))
        assertEquals(listOf(limit, hours), second.filter { it.type != RuleType.TEMPORARY })
        assertEquals(now.plusHours(2), BlockNow.end(second))
        assertEquals(1, second.count { it.type == RuleType.TEMPORARY })
        assertEquals(now.plusHours(3), BlockNow.end(BlockNow.merge(second, now.plusHours(3))))
    }
    @Test fun updatesOnlySavedTarget() {
        val other = item.copy(id = "b")
        val c = Config(items = listOf(item, other), groups = listOf(Group("g", "Group")))
        val draft = item.copy(name = "Unsaved name", rules = emptyList())
        val saved = BlockNow.apply(c, item.id, false, now.plusHours(1), now)
        assertEquals(item.name, saved.items[0].name)
        assertEquals(other, saved.items[1])
        assertEquals(c.groups, saved.groups)
        assertEquals(c.settings, saved.settings)
        val rebased = draft.copy(rules = BlockNow.merge(draft.rules, BlockNow.end(saved.items[0].rules)!!))
        assertEquals("Unsaved name", rebased.name)
        assertTrue(AntiBypass.itemLooser(saved.items[0], rebased, now))
        assertEquals(now.plusHours(1), BlockNow.end(saved.items[0].rules)) // discarding draft keeps saved block
    }
    @Test fun groupUpdatePreservesMembersAndItems() {
        val group = Group("g", "Group", rules = listOf(limit), memberIds = listOf(item.id))
        val c = Config(items = listOf(item), groups = listOf(group))
        val saved = BlockNow.apply(c, "g", true, now.plusHours(1), now)
        assertEquals(c.items, saved.items)
        assertEquals(group.memberIds, saved.groups.single().memberIds)
        assertEquals(limit, saved.groups.single().rules.first())
        assertEquals(now.plusHours(1), BlockNow.end(saved.groups.single().rules))
    }
    @Test fun staleTargetIsNotResurrected() {
        assertEquals(Config(), BlockNow.apply(Config(), "gone", false, now.plusHours(1), now))
    }
    @Test fun rejectsPastAndPresent() {
        for (at in listOf(now, now.minusSeconds(1))) {
            assertThrows(IllegalArgumentException::class.java) { BlockNow.apply(Config(), "a", false, at, now) }
        }
    }
    @Test fun anyShorteningOrRemovalNeedsChallenge() {
        val rules = BlockNow.merge(item.rules, now.plusHours(1))
        val temp = rules.last()
        assertTrue(AntiBypass.ruleLooser(temp, temp.copy(tempUntil = now.plusHours(1).minusSeconds(1).toString()), now))
        assertTrue(AntiBypass.itemLooser(item.copy(rules = rules), item, now))
        assertFalse(AntiBypass.itemLooser(item, item.copy(rules = rules), now))
        assertFalse(AntiBypass.ruleLooser(temp, temp, now))
    }
    @Test fun temporaryActuallyBlocksAndExpires() {
        val updated = item.copy(rules = BlockNow.merge(emptyList(), now.plusHours(1)))
        val eff = Rules.effectiveRules(updated, emptyList())
        assertEquals(Reason.TEMPORARY, Rules.itemBlock(eff, now, { _, _ -> 0 })?.reason)
        assertNull(Rules.itemBlock(eff, now.plusHours(1), { _, _ -> 0 }))
    }
    @Test fun groupShorteningAndRemovalAreGuarded() {
        val group = Group("g", "Group", rules = BlockNow.merge(emptyList(), now.plusHours(1)))
        val shorter = group.copy(rules = listOf(group.rules.single().copy(tempUntil = now.plusHours(1).minusSeconds(1).toString())))
        assertTrue(AntiBypass.groupLooser(group, shorter, now))
        assertTrue(AntiBypass.groupLooser(group, group.copy(rules = emptyList()), now))
    }
    @Test fun pauseAndEmergencyAreExplainedWithoutChangingThem() {
        val pause = BlockPause(now.toString(), now.plusMinutes(30).toString())
        val paused = Config(items = listOf(item), pause = pause)
        assertTrue(BlockNow.notice(paused, item.id, false, now).contains("paused"))
        assertEquals(pause, BlockNow.apply(paused, item.id, false, now.plusHours(1), now).pause)
        val use = EmergencyUse(now.toString(), "t", now.plusMinutes(10).toString(), items = listOf(item.id))
        val emergency = Config(items = listOf(item), groups = listOf(Group("g", "Group", memberIds = listOf(item.id))), emergencyUses = listOf(use))
        assertTrue(BlockNow.notice(emergency, item.id, false, now).contains("emergency unlock"))
        assertTrue(BlockNow.notice(emergency, "g", true, now).contains("emergency unlock"))
        assertEquals(listOf(use), BlockNow.apply(emergency, item.id, false, now.plusHours(1), now).emergencyUses)
    }
    @Test fun mergeNormalizesDuplicateOrMalformedTemporaryRules() {
        val rules = listOf(limit, Rule(RuleType.TEMPORARY, tempUntil = "broken"),
            Rule(RuleType.TEMPORARY, tempUntil = now.plusHours(3).toString()),
            Rule(RuleType.TEMPORARY, tempUntil = now.plusHours(2).toString()))
        val merged = BlockNow.merge(rules, now.plusHours(1))
        assertEquals(2, merged.size)
        assertEquals(now.plusHours(3), BlockNow.end(merged))
    }
    @Test fun disabledAndPausedNotices() {
        assertTrue(BlockNow.notice(Config(enabled = false), "a", false, now).contains("off"))
        assertTrue(BlockNow.notice(Config(items = listOf(item.copy(disabled = true))), "a", false, now).contains("paused"))
        assertTrue(BlockNow.notice(Config(groups = listOf(Group("g", "Group", disabled = true))), "g", true, now).contains("paused"))
    }
}
