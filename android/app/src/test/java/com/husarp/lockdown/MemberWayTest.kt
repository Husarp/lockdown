package com.husarp.lockdown

import com.husarp.lockdown.block.Enforce
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.BlockMethod
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

/** A member app's own way in a group (T3): instead of the group's, lighter needs the challenge, removing it falls back. */
class MemberWayTest {
    private val t0 = LocalDateTime.of(2026, 10, 9, 12, 0)
    private val always = listOf(Rule(RuleType.PERMANENT))
    private val game = Item("g", "Game", "com.game", ItemType.APP)
    private val chat = Item("c", "Chat", "com.chat", ItemType.APP)
    private val items = listOf(game, chat).associateBy { it.id }
    private fun group(app: String?, own: Map<String, String> = emptyMap()) =
        Group("e", "Evening", rules = always, memberIds = listOf("g", "c"), appBlock = app, memberBlocks = own)

    @Test fun the_effective_way_is_its_own_else_the_groups() {
        val g = group("close", mapOf("g" to "grey"))
        assertEquals("grey", BlockMethod.inGroup(game, g))
        assertEquals("close", BlockMethod.inGroup(chat, g))
        assertEquals("close", BlockMethod.inGroup(game, g, own = false))
        assertEquals("grey", BlockMethod.inGroup(game, group(null, mapOf("g" to "grey"))))
        // a site's own is still added on top
        val site = Item("s", "Reddit", "reddit.com", ItemType.SITE)
        assertEquals("dns,close", BlockMethod.inGroup(site, Group("x", "X", siteBlock = "dns", memberBlocks = mapOf("s" to "close"))))
    }

    @Test fun enforcement_uses_the_members_own_way() {
        val cfg = Config(items = listOf(game, chat), groups = listOf(group("close", mapOf("g" to "grey"))))
        assertTrue(Enforce.app(cfg, "com.game", t0, null)!!.grey)        // only greyed: it saves and quits
        assertFalse(Enforce.app(cfg, "com.chat", t0, null)!!.grey)       // the group's way: closed
        val other = Config(items = listOf(game, chat), groups = listOf(group("grey", mapOf("g" to "close"))))
        assertFalse(Enforce.app(other, "com.game", t0, null)!!.grey)
        assertTrue(Enforce.app(other, "com.chat", t0, null)!!.grey)
        assertTrue(BlockMethod.greyUsed(listOf(game), listOf(group("close", mapOf("g" to "grey")))))
    }

    @Test fun a_lighter_own_way_needs_the_challenge_a_tighter_one_does_not() {
        fun looser(old: Group, new: Group) = AntiBypass.groupLooser(old, new, t0, items)
        assertTrue(looser(group("close"), group("close", mapOf("g" to "grey"))))                       // lighter
        assertFalse(looser(group("grey"), group("grey", mapOf("g" to "close"))))                       // tighter
        assertFalse(looser(group("close", mapOf("g" to "grey")), group("close", mapOf("g" to "close"))))
        assertFalse(looser(group("close", mapOf("g" to "grey")), group("close", mapOf("g" to "grey"))))  // equal
        assertFalse(looser(group("close", mapOf("g" to "grey")), group("close")))                      // own removed, group closes
        assertTrue(looser(group("grey", mapOf("g" to "close")), group("grey")))                        // own removed, group greys
        assertTrue(looser(group("close", mapOf("g" to "close")), group("grey")))                       // own close removed and the group greys
    }

    @Test fun the_field_round_trips_and_older_configs_load() {
        val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
        val g = group("close", mapOf("g" to "grey"))
        assertEquals(g, json.decodeFromString(Group.serializer(), json.encodeToString(Group.serializer(), g)))
        val old = json.decodeFromString(Group.serializer(), """{"id":"e","name":"Evening","appBlock":"close"}""")
        assertEquals(emptyMap<String, String>(), old.memberBlocks)
        assertEquals("close", BlockMethod.inGroup(game, old))
        assertNull(old.siteBlock)
    }
}
