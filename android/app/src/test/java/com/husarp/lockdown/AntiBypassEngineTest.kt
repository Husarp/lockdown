package com.husarp.lockdown

import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.AntiBypassCfg
import com.husarp.lockdown.engine.Emergency
import com.husarp.lockdown.engine.EmergencyTier
import com.husarp.lockdown.engine.EmergencyUse
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.Window
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import kotlin.random.Random

class AntiBypassEngineTest {
    private val t0 = LocalDateTime.of(2026, 9, 28, 12, 0)   // Monday noon
    private val allDayMon = Window(listOf(0), "00:00", "23:59")
    private val sundayEve = Window(listOf(6), "18:00", "20:00")

    @Test fun status_reflects_the_challenge() {
        assertEquals("free", AntiBypass.status(AntiBypassCfg(), t0))                          // nothing set
        assertEquals("phrase", AntiBypass.status(AntiBypassCfg(phrase = true), t0))
        assertEquals("closed", AntiBypass.status(AntiBypassCfg(hours = true, windows = listOf(sundayEve)), t0))
        assertEquals("free", AntiBypass.status(AntiBypassCfg(hours = true, windows = listOf(allDayMon)), t0))
    }

    @Test fun unlock_opens_for_the_window() {
        val (cfg, wait) = AntiBypass.unlock(AntiBypassCfg(phrase = true), t0)
        assertNull(wait)                                                                     // no wait configured
        assertEquals("free", AntiBypass.status(cfg, t0))
        assertNotNull(AntiBypass.unlockedUntil(cfg, t0))
        assertNull(AntiBypass.unlockedUntil(cfg, t0.plusMinutes(6)))                         // expired after UNLOCK_MIN
    }

    @Test fun wait_holds_the_unlock_off() {
        val (cfg, wait) = AntiBypass.unlock(AntiBypassCfg(phrase = true, waitMin = 10), t0)
        assertEquals(t0.plusMinutes(10), wait)
        assertEquals("waiting", AntiBypass.status(cfg, t0))
        assertNull(AntiBypass.unlockedUntil(cfg, t0))                                        // nothing allowed yet
        assertEquals("free", AntiBypass.status(cfg, t0.plusMinutes(11)))                     // wait over
    }

    @Test fun new_phrase_is_grouped_lowercase() {
        val p = AntiBypass.newPhrase(10, complex = false, rng = Random(42))
        assertEquals(10, p.replace(" ", "").length)
        assertTrue(p.matches(Regex("[a-z]{5} [a-z]{5}")))
    }

    @Test fun settings_looser_flags_weakening() {
        val base = AntiBypassCfg(phrase = true, length = 60, hours = true, windows = listOf(sundayEve), waitMin = 5)
        assertTrue(AntiBypass.settingsLooser(base, base.copy(phrase = false)))               // phrase off
        assertTrue(AntiBypass.settingsLooser(base, base.copy(length = 30)))                  // shorter
        assertTrue(AntiBypass.settingsLooser(base, base.copy(waitMin = 0)))                  // less wait
        assertTrue(AntiBypass.settingsLooser(base, base.copy(windows = listOf(allDayMon))))  // different hours
        assertFalse(AntiBypass.settingsLooser(base, base.copy(length = 90, waitMin = 10)))   // stronger
    }

    @Test fun own_phrase_is_free_when_turning_the_phrase_on_but_not_after() {
        val off = AntiBypassCfg(phrase = false)
        assertFalse(AntiBypass.settingsLooser(off, off.copy(phrase = true, customPhrase = "my own words")))   // on with yours
        val random = off.copy(phrase = true)
        assertTrue(AntiBypass.settingsLooser(random, random.copy(customPhrase = "my own words")))             // random -> yours
    }

    @Test fun rule_looser_cases() {
        val day30 = Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30)
        assertTrue(AntiBypass.ruleLooser(day30, Rule(RuleType.TIME_LIMIT, dailyLimitMin = 60), t0))
        assertFalse(AntiBypass.ruleLooser(day30, Rule(RuleType.TIME_LIMIT, dailyLimitMin = 20), t0))
        assertTrue(AntiBypass.ruleLooser(day30, null, t0))                                   // removed
        val temp2h = Rule(RuleType.TEMPORARY, tempUntil = t0.plusHours(2).toString())
        assertTrue(AntiBypass.ruleLooser(temp2h, Rule(RuleType.TEMPORARY, tempUntil = t0.plusHours(1).toString()), t0))
        assertFalse(AntiBypass.ruleLooser(Rule(RuleType.PERMANENT), Rule(RuleType.PERMANENT), t0))
    }

    @Test fun item_looser_cases() {
        val app = Item("1", "A", "com.a", ItemType.APP, blockType = "close", rules = listOf(Rule(RuleType.PERMANENT)))
        assertTrue(AntiBypass.itemLooser(app, app.copy(disabled = true), t0))                // paused
        assertTrue(AntiBypass.itemLooser(app, app.copy(blockType = "minimize"), t0))         // weaker block
        assertTrue(AntiBypass.itemLooser(app, null, t0))                                     // removed
        assertFalse(AntiBypass.itemLooser(app, app.copy(name = "Renamed"), t0))              // cosmetic

        val site = Item("2", "S", "a.com b.com", ItemType.SITE)
        assertTrue(AntiBypass.itemLooser(site, site.copy(target = "a.com"), t0))             // dropped a host
    }

    @Test fun removing_an_empty_group_is_not_loosening() {
        assertFalse(AntiBypass.groupLooser(Group("e", "Empty"), null, t0))                    // nothing to loosen
        assertTrue(AntiBypass.groupLooser(Group("g", "Fun", rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30))), null, t0))
    }

    @Test fun group_looser_cases() {
        val g = Group("g", "Fun", rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30)), memberIds = listOf("1", "2"))
        assertTrue(AntiBypass.groupLooser(g, g.copy(memberIds = listOf("1")), t0))           // member removed
        assertFalse(AntiBypass.groupLooser(g, g.copy(name = "Renamed"), t0))
        val overridden = g.copy(overrides = mapOf("1" to mapOf("TIME_LIMIT" to Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30))))
        val loosened = g.copy(overrides = mapOf("1" to mapOf("TIME_LIMIT" to Rule(RuleType.TIME_LIMIT, dailyLimitMin = 90))))
        assertTrue(AntiBypass.groupLooser(overridden, loosened, t0))                         // a member's own limit raised
    }

    private fun one(min: Int, uses: Int, per: String) = listOf(EmergencyTier(Emergency.LEGACY_TIER, uses, per, min))

    @Test fun emergency_and_protection_looser() {
        assertTrue(AntiBypass.emergencyLooser(false, one(20, 3, "week"), true, one(20, 3, "week")))    // turned on
        assertTrue(AntiBypass.emergencyLooser(true, one(20, 3, "week"), true, one(30, 3, "week")))     // longer
        assertTrue(AntiBypass.emergencyLooser(true, one(20, 3, "week"), true, one(20, 3, "day")))      // per day
        assertFalse(AntiBypass.emergencyLooser(true, one(20, 3, "week"), true, one(20, 3, "week")))

        assertTrue(AntiBypass.protectionLooser(listOf("adult"), emptyList(), emptyList(), emptyList()))  // list off
        assertTrue(AntiBypass.protectionLooser(listOf("adult"), emptyList(), listOf("adult"), listOf("x.com")))  // allowed a site
        assertFalse(AntiBypass.protectionLooser(listOf("adult"), emptyList(), listOf("adult", "gambling"), emptyList()))
    }

    @Test fun emergency_uses_count_per_period() {
        val week = EmergencyTier(Emergency.LEGACY_TIER, 3, "week", 20)
        fun used(vararg at: LocalDateTime) = at.map { EmergencyUse(it.toString(), week.id, it.plusMinutes(20).toString()) }
        val u1 = Emergency.usesLeft(used(t0.minusHours(1)), week, t0)
        assertEquals(2, u1.left)
        // Sunday 09-27 is in the previous Monday-start week, so it doesn't count against this week
        val prevWeek = Emergency.usesLeft(used(t0.minusHours(1), t0.minusHours(2), t0.minusDays(1)), week, t0)
        assertEquals(1, prevWeek.left)
        val out = Emergency.usesLeft(used(t0.minusHours(1), t0.minusHours(2), t0.minusHours(3)), week, t0)
        assertEquals(0, out.left)
        assertNull(Emergency.start(true, listOf(week), used(t0, t0, t0), week.id, listOf("a"), false, t0).use)
        assertEquals(t0.plusMinutes(20).toString(), Emergency.start(true, listOf(week), emptyList(), week.id, listOf("a"), false, t0).use!!.until)
    }
}
