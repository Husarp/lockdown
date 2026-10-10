package com.husarp.lockdown

import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Reason
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.SchedMode
import com.husarp.lockdown.engine.Schedule
import com.husarp.lockdown.engine.Window
import com.husarp.lockdown.ui.GroupLook
import com.husarp.lockdown.ui.Kinds
import com.husarp.lockdown.ui.Status
import com.husarp.lockdown.ui.theme.DarkTones
import com.husarp.lockdown.ui.theme.Hue
import com.husarp.lockdown.ui.theme.LightTones
import androidx.compose.ui.graphics.Color
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

/** The redesign's colour language: one hue and icon per kind of block, state and group; looks only. */
class LookTest {
    private val t0 = LocalDateTime.of(2026, 10, 9, 12, 0)

    @Test fun there_is_a_tone_for_every_hue_in_both_themes() {
        assertEquals(Hue.entries.size, LightTones.size)
        assertEquals(Hue.entries.size, DarkTones.size)
        assertEquals(Color(0xFF306A43), LightTones[Hue.GREEN.ordinal].accent)        // success: AA on every container
    }

    @Test fun every_rule_type_reason_and_status_has_a_hue_and_an_icon() {
        RuleType.entries.forEach { Kinds.rule(it).icon.name.isNotEmpty().let(::assertTrue) }
        Reason.entries.forEach { assertTrue(Kinds.reason(it).icon.name.isNotEmpty()); assertTrue(Kinds.reasonText(it).isNotEmpty()) }
        Status.entries.forEach { assertTrue(Kinds.status(it).icon.name.isNotEmpty()) }
        assertEquals(Hue.RED, Kinds.rule(RuleType.PERMANENT).hue)
        assertEquals(Hue.AMBER, Kinds.rule(RuleType.TIME_LIMIT).hue)
        assertEquals(Hue.AMBER, Kinds.rule(RuleType.SWITCH_LIMIT).hue)
        assertEquals(Hue.INDIGO, Kinds.rule(RuleType.SCHEDULED).hue)
        assertEquals(Hue.VIOLET, Kinds.rule(RuleType.TEMPORARY).hue)
        assertEquals(Kinds.rule(RuleType.TIME_LIMIT), Kinds.reason(Reason.LIMIT))
        assertEquals(Hue.SLATE, Kinds.status(Status.BLOCKED, grey = true).hue)        // greyed: slate, not red
        assertEquals(Hue.RED, Kinds.status(Status.BLOCKED).hue)
        assertEquals(Hue.GREEN, Kinds.category("productive").hue)
        assertEquals(Hue.PINK, Kinds.category("distracting").hue)
        assertEquals(Hue.SLATE, Kinds.category(null).hue)
    }

    @Test fun rules_read_in_a_few_words() {
        assertEquals("Always", Kinds.ruleText(Rule(RuleType.PERMANENT)))
        assertEquals("1h a day", Kinds.ruleText(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 60)))
        assertEquals("1h 30m a day", Kinds.ruleText(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 90)))
        assertEquals("10 opens a day", Kinds.ruleText(Rule(RuleType.SWITCH_LIMIT, dailySwitchLimit = 10)))
        val night = Schedule(SchedMode.BLOCK, listOf(Window((0..6).toList(), "22:00", "07:00")))
        assertEquals("22:00–07:00", Kinds.ruleText(Rule(RuleType.SCHEDULED, schedule = night)))
        assertEquals("Allowed 09:00–17:00", Kinds.ruleText(Rule(RuleType.SCHEDULED,
            schedule = Schedule(SchedMode.ALLOW, listOf(Window((0..4).toList(), "09:00", "17:00"))))))
        assertEquals("until 18:00", Kinds.ruleText(Rule(RuleType.TEMPORARY, tempUntil = t0.withHour(18).toString()), t0))
    }

    @Test fun a_sites_tile_colour_is_stable_and_never_looks_blocked_or_brand() {
        assertEquals(Kinds.siteHue("reddit.com"), Kinds.siteHue("www.reddit.com old.reddit.com"))
        val hosts = (1..300).map { "site$it.example" } + listOf("youtube.com", "x.com", "")
        hosts.forEach { h -> assertTrue(h, Kinds.siteHue(h) != Hue.RED && Kinds.siteHue(h) != Hue.ORANGE) }
        assertTrue(hosts.map { Kinds.siteHue(it) }.toSet().size > 4)                     // spread over the palette
    }

    @Test fun a_groups_icon_comes_from_its_name_in_english_and_polish() {
        assertEquals("social", GroupLook.autoIcon("Social"))
        assertEquals("social", GroupLook.autoIcon("Instagram & co"))          // not a game: words by their start
        assertEquals("moon", GroupLook.autoIcon("Late night"))
        assertEquals("moon", GroupLook.autoIcon("Noc"))
        assertEquals("game", GroupLook.autoIcon("Gry"))
        assertEquals("game", GroupLook.autoIcon("Gaming"))
        assertEquals("video", GroupLook.autoIcon("YouTube and Netflix"))
        assertEquals("chat", GroupLook.autoIcon("Messaging"))
        assertEquals("music", GroupLook.autoIcon("Muzyka"))
        assertEquals("news", GroupLook.autoIcon("Wiadomości"))
        assertEquals("shopping", GroupLook.autoIcon("Zakupy"))
        assertEquals("casino", GroupLook.autoIcon("Hazard"))
        assertEquals("work", GroupLook.autoIcon("Praca"))
        assertEquals("school", GroupLook.autoIcon("Szkoła"))
        assertEquals("social", GroupLook.autoIcon("Media społecznościowe"))
        assertEquals("folder", GroupLook.autoIcon("Stuff"))
        // short stems only as whole words
        assertEquals("folder", GroupLook.autoIcon("Better focus"))
        assertEquals("folder", GroupLook.autoIcon("Senior"))
        assertEquals("folder", GroupLook.autoIcon("Send later"))
        assertEquals("folder", GroupLook.autoIcon("Grafika"))
        assertEquals("folder", GroupLook.autoIcon("Gramatyka"))
        assertEquals("folder", GroupLook.autoIcon("Ready"))
        assertEquals("casino", GroupLook.autoIcon("Bet apps"))
        assertEquals("casino", GroupLook.autoIcon("Betting"))
        assertEquals("moon", GroupLook.autoIcon("Bedtime"))
        assertEquals("moon", GroupLook.autoIcon("Sen"))
        assertEquals("moon", GroupLook.autoIcon("Nocne"))
        assertEquals("game", GroupLook.autoIcon("Gra"))
        assertEquals("video", GroupLook.autoIcon("TV"))
        assertEquals("book", GroupLook.autoIcon("To read"))
        assertEquals("book", GroupLook.autoIcon("Books"))
        assertEquals("folder", GroupLook.autoIcon(""))
        assertEquals(24, GroupLook.KEYS.size)
        GroupLook.KEYS.forEach { assertTrue(GroupLook.vector(it).name.isNotEmpty()) }
    }

    @Test fun a_picked_look_wins_and_an_unknown_one_falls_back_to_automatic() {
        val g = Group("g1", "Social")
        assertEquals("social", GroupLook.iconKey(g))
        assertEquals("moon", GroupLook.iconKey(g.copy(icon = "moon")))
        assertEquals("social", GroupLook.iconKey(g.copy(icon = "rocket")))       // from a newer copy: automatic
        assertEquals(Hue.INDIGO, GroupLook.hue(g.copy(color = "INDIGO")))
        assertEquals(GroupLook.autoHue("g1"), GroupLook.hue(g.copy(color = "ULTRAVIOLET")))
        assertEquals(GroupLook.autoHue("g1"), GroupLook.hue(g))
    }

    @Test fun the_automatic_colour_is_stable_and_never_red_or_slate() {
        assertEquals(GroupLook.autoHue("abc123"), GroupLook.autoHue("abc123"))
        val hues = (1..500).map { GroupLook.autoHue(java.util.UUID.randomUUID().toString().take(8)) }
        hues.forEach { assertTrue(it != Hue.RED && it != Hue.SLATE) }
        assertTrue(hues.toSet().size == 8)
    }

    @Test fun an_item_ruled_only_by_a_group_says_why_its_on_the_list() {
        val yt = Item("yt", "YouTube", "youtube.com", ItemType.SITE)
        val g = Group("g", "Social", rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 60)), memberIds = listOf("yt"))
        val pills = Kinds.itemPills(yt, listOf(g))
        assertEquals(listOf("Social"), pills.map { it.text })
        assertEquals(GroupLook.hue(g), pills[0].hue)
        val own = Kinds.itemPills(yt.copy(rules = listOf(Rule(RuleType.PERMANENT))), listOf(g))
        assertEquals(listOf("Always", "Social"), own.map { it.text })                 // its own rules first
        assertEquals(listOf("Site"), Kinds.itemPills(yt, emptyList()).map { it.text })
        assertTrue(Kinds.itemPills(yt, emptyList())[0].outlined)
    }

    @Test fun a_members_pills_group_then_extras_then_its_way_then_its_own_rules() {
        val app = Item("ig", "Instagram", "com.instagram", ItemType.APP, rules = listOf(Rule(RuleType.SWITCH_LIMIT, dailySwitchLimit = 5)))
        val g = Group("g", "Social", rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 120)), memberIds = listOf("ig"),
            overrides = mapOf("ig" to mapOf("TIME_LIMIT" to Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30))),
            appBlock = "grey", memberBlocks = mapOf("ig" to "close"))
        val pills = Kinds.memberPills(g, app)
        assertEquals(listOf("Group rules", "+ 30m a day", "Closed", "5 opens a day"), pills.map { it.text })
        assertEquals(GroupLook.hue(g), pills[0].hue)
        assertEquals(Hue.AMBER, pills[1].hue)
        assertEquals(Hue.SLATE, pills[2].hue)
        assertTrue(pills[3].outlined)
        val site = Item("r", "Reddit", "reddit.com", ItemType.SITE)
        val sg = Group("s", "Sites", memberIds = listOf("r"), siteBlock = "back", memberBlocks = mapOf("r" to "dns"))
        assertEquals(listOf("Group rules", "Cut connection"), Kinds.memberPills(sg, site).map { it.text })
        assertNotEquals(Hue.RED, Kinds.memberPills(sg, site)[1].hue)
    }

    @Test fun a_group_sums_itself_up_in_a_sentence() {
        val night = Schedule(SchedMode.BLOCK, listOf(Window((0..6).toList(), "22:00", "07:00")))
        val g = Group("g", "Social", rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 60), Rule(RuleType.SCHEDULED, schedule = night)))
        assertEquals("Social: 1h a day together, blocked 22:00–07:00. 6 members.", com.husarp.lockdown.screens.groupSentence(g, 6))
        assertEquals("New: no shared rules yet. 1 member.", com.husarp.lockdown.screens.groupSentence(Group("n", "New"), 1))
    }

    @Test fun bedtime_hours_past_midnight_and_not() {
        val inside = { h: Int, m: Int, b: String, w: String -> com.husarp.lockdown.screens.inBedtime(h * 60 + m, b, w) }
        assertTrue(inside(23, 30, "23:00", "07:00"))
        assertTrue(inside(3, 0, "23:00", "07:00"))
        assertTrue(!inside(7, 0, "23:00", "07:00"))
        assertTrue(!inside(23, 10, "23:30", "07:00"))                        // to the minute
        assertTrue(inside(13, 0, "13:00", "15:00"))
        assertTrue(!inside(15, 0, "13:00", "15:00"))
        assertTrue(!inside(1, 0, "bad", "07:00"))
    }
}
