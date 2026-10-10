package com.husarp.lockdown

import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.HomeCard
import com.husarp.lockdown.data.HomeLayout
import com.husarp.lockdown.data.Live
import com.husarp.lockdown.engine.BlockPause
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Reason
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import java.time.LocalDateTime
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/** Home's cards: which show and in what order; a card a later version adds turns up by itself. */
class HomeLayoutTest {
    private val ids = HomeLayout.CATALOGUE.map { it.first }

    @Test fun no_saved_layout_is_the_default() {
        val l = HomeLayout.resolve(null)
        assertEquals(ids, l.map { it.id })
        assertEquals(HomeLayout.CATALOGUE.map { it.second }, l.map { it.on })
        assertFalse(l.first { it.id == "pause" }.on)
        assertTrue(l.first { it.id == "blocked" }.on)
        assertEquals(HomeLayout.reset(), l)
        assertFalse("status" in ids)                          // always first, not in the list
    }

    @Test fun unknown_and_duplicate_cards_are_dropped_and_a_missing_one_is_appended_with_its_default() {
        val saved = listOf(HomeCard("apps"), HomeCard("rocket"), HomeCard("blocked", on = false), HomeCard("apps", on = false))
        val l = HomeLayout.resolve(saved)
        assertEquals(listOf("apps", "blocked"), l.take(2).map { it.id })
        assertTrue(l[0].on)                                    // the first "apps" wins
        assertFalse(l[1].on)
        assertEquals(ids.size, l.size)
        assertEquals(ids.filter { it != "apps" && it != "blocked" }, l.drop(2).map { it.id })
        assertFalse(l.first { it.id == "emergency" }.on)       // appended with its default
    }

    @Test fun moving_past_either_end_does_nothing() {
        val l = HomeLayout.reset()
        assertSame(l, HomeLayout.move(l, ids.first(), -1))
        assertSame(l, HomeLayout.move(l, ids.last(), 1))
        assertSame(l, HomeLayout.move(l, "rocket", 1))
        val down = HomeLayout.move(l, ids[0], 1)
        assertEquals(listOf(ids[1], ids[0]) + ids.drop(2), down.map { it.id })
        assertEquals(l.map { it.id }, HomeLayout.move(down, ids[0], -1).map { it.id })
    }

    @Test fun toggling_shows_and_hides_one_card() {
        val l = HomeLayout.toggle(HomeLayout.reset(), "pause")
        assertTrue(l.first { it.id == "pause" }.on)
        assertEquals(HomeLayout.reset().filter { it.id != "pause" }, l.filter { it.id != "pause" })
        assertFalse(HomeLayout.toggle(l, "pause").first { it.id == "pause" }.on)
    }

    // ---------- Home's "Blocked now" (Live.blockedNow) ----------

    private val t0 = LocalDateTime.of(2026, 10, 9, 12, 0)
    private val always = listOf(Rule(RuleType.PERMANENT))
    private fun temp(until: LocalDateTime) = listOf(Rule(RuleType.TEMPORARY, tempUntil = until.toString()))
    private val noUse = { _: String, _: String -> 0 }

    @Test fun only_always_blocked_items_are_counted_not_listed_and_paused_ones_skipped() {
        val sites = (1..2000).map { Item("s$it", "site$it.com", "site$it.com", ItemType.SITE, rules = always) }
        val cfg = Config(items = sites + listOf(
            Item("a", "Game", "com.game", ItemType.APP, rules = always),
            Item("b", "Paused", "com.paused", ItemType.APP, rules = always, disabled = true),
            Item("c", "Free", "com.free", ItemType.APP),
        ))
        val b = Live.blockedNow(cfg, t0, usage = noUse)
        assertEquals(1, b.alwaysApps)
        assertEquals(2000, b.alwaysSites)
        assertTrue(b.rows.isEmpty())
        assertEquals(0, b.total)
    }

    @Test fun rows_are_the_other_blocks_soonest_end_first_up_to_the_limit() {
        val items = listOf(
            Item("late", "Late", "com.late", ItemType.APP, rules = temp(t0.plusHours(6))),
            Item("soon", "Soon", "com.soon", ItemType.APP, rules = temp(t0.plusHours(1))),
            Item("mid", "Mid", "mid.com", ItemType.SITE, rules = temp(t0.plusHours(3))),
            Item("gone", "Gone", "com.gone", ItemType.APP, rules = temp(t0.minusHours(1))),     // ran out: not blocked
            Item("lim", "Limited", "com.lim", ItemType.APP),
            Item("inG", "Grouped forever", "com.g", ItemType.APP, rules = always),
        )
        val g = Group("g", "Social", rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30)), memberIds = listOf("lim", "inG"))
        val used = { owner: String, bucket: String -> if (owner == "group:g" && bucket.startsWith("day:")) 1800 else 0 }
        val b = Live.blockedNow(Config(items = items, groups = listOf(g)), t0, limit = 3, usage = used)
        assertEquals(listOf("Soon", "Mid", "Late"), b.rows.map { it.item.name })
        assertEquals(4, b.total)                                                  // + the group's limit, cut by the limit
        assertEquals(Reason.TEMPORARY, b.rows[0].reason)
        assertEquals(t0.plusHours(1), b.rows[0].until)
        assertEquals(1, b.alwaysApps)                                             // a permanent rule in a group counts too
        val all = Live.blockedNow(Config(items = items, groups = listOf(g)), t0, usage = used)
        assertEquals(Reason.LIMIT, all.rows.last().reason)
        assertTrue(Live.blockedNow(Config(items = items, groups = listOf(g.copy(disabled = true))), t0, usage = used).rows.none { it.item.id == "lim" })
    }

    @Test fun an_always_grey_app_counts_as_grey_not_blocked() {
        val grey = Item("i", "Insta", "com.insta", ItemType.APP, blockType = "grey", rules = always)
        val b = Live.blockedNow(Config(items = listOf(grey, Item("a", "Game", "com.game", ItemType.APP, rules = always))), t0, usage = noUse)
        assertEquals(1, b.alwaysGrey)
        assertEquals(1, b.alwaysApps)
        // in a group (evaluated): the group's way greys it
        val inG = grey.copy(blockType = "", rules = emptyList())
        val g = Group("g", "Social", rules = always, memberIds = listOf("i"), appBlock = "grey")
        val gb = Live.blockedNow(Config(items = listOf(inG), groups = listOf(g)), t0, usage = noUse)
        assertEquals(1, gb.alwaysGrey)
        assertEquals(0, gb.alwaysApps)
        assertEquals(0, Live.blockedNow(Config(items = listOf(inG), groups = listOf(g.copy(appBlock = null))), t0, usage = noUse).alwaysGrey)
    }

    @Test fun a_grey_only_app_is_marked_grey() {
        val cfg = Config(items = listOf(Item("i", "Insta", "com.insta", ItemType.APP, blockType = "grey", rules = temp(t0.plusHours(2)))))
        assertTrue(Live.blockedNow(cfg, t0, usage = noUse).rows.single().grey)
    }
}
