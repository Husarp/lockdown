package com.husarp.lockdown

import com.husarp.lockdown.block.Enforce
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.BlockMethod
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Mode
import com.husarp.lockdown.engine.ModeState
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.grayscaleRestore
import com.husarp.lockdown.engine.grayscaleStep
import com.husarp.lockdown.data.Live
import com.husarp.lockdown.engine.Reason
import com.husarp.lockdown.engine.SchedMode
import com.husarp.lockdown.engine.Schedule
import com.husarp.lockdown.engine.Window
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

/** "Grey this app": an app way that greys the screen while it's in front instead of closing the app. */
class GreyAppTest {
    private val t0 = LocalDateTime.of(2026, 10, 9, 12, 0)
    private val always = listOf(Rule(RuleType.PERMANENT))
    private val forNow = listOf(Rule(RuleType.TEMPORARY, tempUntil = t0.plusHours(2).toString()))
    private val insta = Item("i", "Instagram", "com.insta", ItemType.APP, blockType = "grey", rules = always)

    @Test fun the_app_way_closes_by_default_and_closing_covers_greying() {
        assertEquals(setOf("close"), BlockMethod.appFlags(""))
        assertTrue(BlockMethod.greyOnly("grey"))
        assertFalse(BlockMethod.greyOnly(""))
        assertFalse(BlockMethod.greyOnly("close,grey"))
        assertFalse(BlockMethod.greyOnly("minimize,grey"))                 // a PC way closes it here
        assertEquals("close", BlockMethod.mergeApp("grey", "close"))
        assertEquals("grey", BlockMethod.mergeApp("grey", "grey"))
        assertEquals("close,background", BlockMethod.mergeApp("grey", "close,background"))
    }

    @Test fun greying_instead_of_closing_needs_the_challenge_closing_instead_is_free() {
        val closed = insta.copy(blockType = "")
        assertTrue(AntiBypass.itemLooser(closed, insta, t0))                              // close -> grey
        assertTrue(AntiBypass.itemLooser(closed.copy(blockType = "close"), insta, t0))
        assertFalse(AntiBypass.itemLooser(insta, closed, t0))                             // grey -> close
        assertFalse(AntiBypass.itemLooser(insta, insta.copy(blockType = "close,grey"), t0))
        assertFalse(AntiBypass.itemLooser(insta.copy(blockType = "close,grey"), closed, t0))
        assertTrue(AntiBypass.itemLooser(insta, insta.copy(rules = emptyList()), t0))      // no more grey at all
    }

    @Test fun a_grey_app_stays_open_and_a_closing_one_still_closes() {
        val cfg = Config(items = listOf(insta))
        val v = Enforce.app(cfg, "com.insta", t0, null)
        assertNotNull(v)
        assertTrue(v!!.grey)
        assertFalse(Enforce.app(cfg.copy(items = listOf(insta.copy(blockType = ""))), "com.insta", t0, null)!!.grey)
        assertNull(Enforce.app(cfg.copy(enabled = false), "com.insta", t0, null))              // Lockdown off: colour
        assertNull(Enforce.app(cfg.copy(items = listOf(insta.copy(disabled = true))), "com.insta", t0, null))
    }

    @Test fun a_grey_app_anywhere_on_screen_keeps_it_grey() {
        val cfg = Config(items = listOf(insta, insta.copy(id = "c", target = "com.game", blockType = "")))
        // its video in picture-in-picture over the launcher, or next to another app in split screen
        assertTrue(Enforce.greyOnScreen(cfg, listOf("com.launcher", "com.insta"), t0, null))
        assertFalse(Enforce.greyOnScreen(cfg, listOf("com.launcher", "com.other"), t0, null))
        assertFalse(Enforce.greyOnScreen(cfg, listOf("com.game"), t0, null))           // closed, not greyed
        assertFalse(Enforce.greyOnScreen(cfg, emptyList(), t0, null))
        assertFalse(Enforce.greyOnScreen(cfg.copy(enabled = false), listOf("com.insta"), t0, null))
        val work = ModeState(Mode("w", "Work", items = listOf("i")), t0, null, locked = false, scheduled = false, phase = null)
        assertFalse(Enforce.greyOnScreen(cfg, listOf("com.insta"), t0, work))         // a mode closes it instead
    }

    @Test fun a_mode_that_blocks_a_grey_app_still_closes_it() {
        val work = Mode("w", "Work", items = listOf("i"))
        val state = ModeState(work, t0, null, locked = false, scheduled = false, phase = null)
        val v = Enforce.app(Config(items = listOf(insta)), "com.insta", t0, state)!!
        assertFalse(v.grey)
        assertEquals("mode", v.reason)
    }

    @Test fun a_grey_app_is_in_use_its_time_counts_a_closed_one_doesnt() {
        val cfg = Config(items = listOf(insta, insta.copy(id = "c", target = "com.game", blockType = "")))
        assertTrue(Enforce.counts(cfg, cfg.items[0], t0))
        assertFalse(Enforce.counts(cfg, cfg.items[1], t0))
        assertTrue(Enforce.counts(cfg, cfg.items[1].copy(rules = emptyList()), t0))       // not blocked
        assertEquals("grey", Enforce.itemWay(cfg, cfg.items[0], t0))
        assertNull(Enforce.itemWay(cfg.copy(enabled = false), cfg.items[0], t0))
    }

    // ---------- in a group ----------

    private val game = Item("g", "Game", "com.game", ItemType.APP, rules = forNow)              // its own block: closes
    private val evening = Group("e", "Evening", rules = always, memberIds = listOf("g", "i"), appBlock = "grey")

    @Test fun a_group_greys_its_member_apps_and_its_own_rule_still_closes() {
        val cfg = Config(items = listOf(game, insta.copy(rules = emptyList())), groups = listOf(evening))
        assertTrue(Enforce.app(cfg, "com.insta", t0, null)!!.grey)                       // only the group blocks it
        assertFalse(Enforce.app(cfg, "com.game", t0, null)!!.grey)                       // its own block: closed
        assertTrue(Enforce.app(cfg, "com.game", t0.plusHours(3), null)!!.grey)            // over: only the group's
        assertTrue(Enforce.counts(cfg, cfg.items[1], t0))
        val noOwn = cfg.copy(items = listOf(game.copy(rules = emptyList()), insta))
        assertTrue(Enforce.app(noOwn, "com.game", t0, null)!!.grey)
        // a member closed in this group (its own on top of the group's way)
        val closedHere = noOwn.copy(groups = listOf(evening.copy(memberBlocks = mapOf("g" to "close"))))
        assertFalse(Enforce.app(closedHere, "com.game", t0, null)!!.grey)
        assertEquals("grey", BlockMethod.inGroup(game, evening.copy(memberBlocks = mapOf("g" to "close")), own = false))
        // a group that hasn't chosen: each app its own way, as before
        val unchosen = noOwn.copy(groups = listOf(evening.copy(appBlock = null)))
        assertFalse(Enforce.app(unchosen, "com.game", t0, null)!!.grey)
        assertTrue(Enforce.app(unchosen, "com.insta", t0, null)!!.grey)
    }

    @Test fun a_groups_weaker_app_way_needs_the_challenge() {
        val items = listOf(game, insta).associateBy { it.id }
        val closing = evening.copy(appBlock = null)
        assertTrue(AntiBypass.groupLooser(closing, evening, t0, items))                         // members closed -> greyed
        assertTrue(AntiBypass.groupLooser(evening.copy(appBlock = "close"), evening, t0, items))
        assertFalse(AntiBypass.groupLooser(evening, evening.copy(appBlock = "close"), t0, items))  // greyed -> closed
        assertFalse(AntiBypass.groupLooser(evening, closing, t0, items))                         // back to each its own (game closes, insta greys)
        val ownClose = evening.copy(memberBlocks = mapOf("g" to "close"))
        assertTrue(AntiBypass.groupLooser(ownClose, evening, t0, items))                         // member's own close dropped
        assertFalse(AntiBypass.groupLooser(evening, ownClose, t0, items))
        assertFalse(AntiBypass.groupLooser(closing, closing.copy(name = "Night"), t0, items))     // unchanged ways
    }

    @Test fun the_grayscale_permission_matters_once_something_greys() {
        assertFalse(BlockMethod.greyUsed(listOf(game), listOf(evening.copy(appBlock = null))))
        assertTrue(BlockMethod.greyUsed(listOf(insta), emptyList()))
        assertTrue(BlockMethod.greyUsed(listOf(game), listOf(evening)))
        assertFalse(BlockMethod.greyUsed(listOf(Item("s", "Site", "grey.com", ItemType.SITE, blockType = "dns")), emptyList()))
    }

    // ---------- the screen: same steps as bedtime grayscale ----------

    @Test fun leaving_the_grey_app_gives_the_colour_back_as_it_was_and_bedtime_keeps_it_grey() {
        // in front: grey, Lockdown's mark on
        assertEquals(true to true, grayscaleStep(wanted = true, switchOn = false, mark = false, isOn = false))
        // left (no bedtime): colour back, mark off - the user's own correction (mode 11, on) comes back
        assertEquals(false to false, grayscaleStep(wanted = false, switchOn = false, mark = true, isOn = true))
        assertEquals(1 to 11, grayscaleRestore(1, 11))
        // left during bedtime grayscale: wanted still (bedtime OR app) - nothing written
        assertEquals(null to true, grayscaleStep(wanted = true, switchOn = true, mark = true, isOn = true))
        // grey of the user's own before the app: it stays as it was
        assertEquals(1 to 0, grayscaleRestore(1, 0, ownGrey = true))
    }

    // ---------- the list's line and look (Live.blockedInfo) ----------

    @Test fun the_blocked_line_says_grey_and_why_for_a_limit_a_schedule_and_a_grey_only_app() {
        val noUse = { _: String, _: String -> 0 }
        val grey = Live.blockedInfo(Config(items = listOf(insta)), insta, t0, usage = noUse)
        assertTrue(grey.grey)
        assertTrue(grey.text.startsWith("Grey"))
        assertEquals(Reason.PERMANENT, grey.reason)

        val limited = insta.copy(blockType = "", rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30)))
        val used = { owner: String, bucket: String -> if (owner == "item:i" && bucket.startsWith("day:")) 1800 else 0 }
        val lim = Live.blockedInfo(Config(items = listOf(limited)), limited, t0, usage = used)
        assertFalse(lim.grey)
        assertTrue(lim.text.startsWith("Blocked"))
        assertEquals(Reason.LIMIT, lim.reason)
        assertEquals(Reason.LIMIT, Live.blockedInfo(Config(items = listOf(limited.copy(blockType = "grey"))), limited.copy(blockType = "grey"), t0, usage = used)
            .let { assertTrue(it.grey); it.reason })

        val night = Schedule(SchedMode.BLOCK, listOf(Window((0..6).toList(), "11:00", "13:00")))
        val hours = insta.copy(blockType = "", rules = listOf(Rule(RuleType.SCHEDULED, schedule = night)))
        val sch = Live.blockedInfo(Config(items = listOf(hours)), hours, t0, usage = noUse)
        assertEquals(Reason.SCHEDULE, sch.reason)
        assertEquals("Blocked until 13:00", sch.text)
        assertFalse(sch.grey)
    }
}
