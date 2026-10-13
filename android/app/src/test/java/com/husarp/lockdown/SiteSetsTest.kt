package com.husarp.lockdown

import com.husarp.lockdown.block.Enforce
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.ImportedSites
import com.husarp.lockdown.data.ProtectionCfg
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.BlockPause
import com.husarp.lockdown.engine.DomainSet
import com.husarp.lockdown.engine.EmergencyUse
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemIndex
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.time.LocalDateTime

/** The compact site sets: protection lists, imported sites, and the blocklist index. */
class SiteSetsTest {
    private val now = LocalDateTime.of(2026, 10, 13, 12, 0)

    // ---------- DomainSet ----------

    @Test fun a_listed_site_blocks_itself_and_its_subdomains_not_its_parent() {
        val s = DomainSet.of(listOf("bad.example.com", "casino.io"))
        assertTrue(s.matches("bad.example.com"))
        assertTrue(s.matches("www.bad.example.com"))
        assertTrue(s.matches("cdn.eu.bad.example.com"))
        assertTrue(s.matches("CASINO.IO."))                // case and a trailing dot don't matter
        assertFalse(s.matches("example.com"))
        assertFalse(s.matches("notbad.example.com"))       // a name ending the same isn't a subdomain
        assertFalse(s.matches("casino.io.evil.net"))
        assertFalse(s.matches(""))
    }

    @Test fun allowed_exceptions_still_win_as_before() {
        val s = DomainSet.of(listOf("ads.net", "tracker.ads.net"))
        assertFalse(s.matches("ads.net", setOf("ads.net")))
        assertFalse(s.matches("img.ads.net", setOf("img.ads.net")))
        assertFalse(s.matches("x.img.ads.net", setOf("img.ads.net")))     // an allowed parent frees what's under it
        assertTrue(s.matches("tracker.ads.net", setOf("ads.net")))        // ... unless something more exact is listed
        assertTrue(s.matches("img.ads.net", setOf("other.net")))
    }

    @Test fun wildcard_hosts_file_and_junk_lines() {
        assertEquals("a.com", DomainSet.listLine("*.a.com"))
        assertEquals("b.org", DomainSet.listLine("0.0.0.0 b.org"))
        assertEquals("c.net", DomainSet.listLine("127.0.0.1\tc.net  # comment"))
        assertEquals("xn--80ak6aa92e.xn--p1ai", DomainSet.listLine("xn--80ak6aa92e.xn--p1ai"))   // a .рф site
        for (junk in listOf("# title", "", "   ", "!adblock", "localhost", "0.0.0.0 localhost", "1.2.3.4", "<!DOCTYPE html>",
            "<title>One moment, please...</title>", "nodot", "a..com"))
            assertNull(junk, DomainSet.listLine(junk))
    }

    @Test fun the_file_round_trips_and_a_foreign_file_is_refused() {
        val s = DomainSet.of(listOf("a.com", "b.com", "a.com", "*.c.com", "WWW.d.com"))
        assertEquals(4, s.size)
        val out = ByteArrayOutputStream().also { s.write(it) }.toByteArray()
        assertEquals(4, DomainSet.countOf(ByteArrayInputStream(out)))
        val back = DomainSet.read(ByteArrayInputStream(out))
        for (h in listOf("a.com", "x.b.com", "c.com", "d.com", "www.d.com")) assertTrue(h, back.matches(h))
        assertFalse(back.matches("e.com"))
        assertEquals(-1, DomainSet.countOf(ByteArrayInputStream("a.com\nb.com\n".toByteArray())))
        try { DomainSet.read(ByteArrayInputStream("a.com\n".toByteArray())); throw AssertionError("read a text file") } catch (_: IOException) {}
    }

    @Test fun the_bundled_lists_all_load() {
        val dir = java.io.File("src/main/assets/protection")
        for (f in dir.listFiles()!!.filter { it.name.endsWith(".txt") }) {
            val lines = f.readLines().map { it.trim().lowercase() }.filter { it.isNotEmpty() && !it.startsWith("#") }
            val set = DomainSet.Builder().apply { lines.forEach { l -> DomainSet.listLine(l)?.let(::add) } }.build()
            assertEquals(f.name, lines.distinct().size, set.size)            // not one line dropped
            assertTrue(f.name, lines.all { set.matches(it) && set.matches("sub.$it") })
        }
    }

    // ---------- ItemIndex ----------

    private fun site(id: String, target: String, disabled: Boolean = false) =
        Item(id, target, target, ItemType.SITE, disabled = disabled, rules = listOf(Rule(RuleType.PERMANENT)))

    @Test fun the_index_finds_what_the_scan_found() {
        val items = listOf(
            Item("a1", "YouTube", "com.google.android.youtube", ItemType.APP),
            site("s1", "youtube.com m.youtube.com youtu.be"),
            site("s2", "www.reddit.com"),
            site("s3", "old.reddit.com"),
            site("s4", "com"),
            site("s5", "Twitch.TV", disabled = true),
        )
        val ix = ItemIndex.of(items)
        assertEquals("a1", ix.app("COM.google.android.youtube")?.id)
        assertNull(ix.app("com.other"))
        val hosts = listOf("youtube.com", "www.youtube.com", "music.youtube.com", "youtu.be", "reddit.com", "old.reddit.com",
            "x.old.reddit.com", "twitch.tv", "a.twitch.tv", "example.org", "notyoutube.com", "")
        for (h in hosts) {
            val scan = items.filter { it.type == ItemType.SITE && Enforce.hostMatches(it.target, h) }.map { it.id }
            assertEquals(h, scan, ix.sites(h).map { it.id })
        }
        assertSame(ix, ItemIndex.of(items))             // made once per list
    }

    // ---------- imported sites ----------

    private fun imported(n: Int) = (1..n).map { site("i$it", "site$it.example") }

    @Test fun a_big_import_moves_into_the_compact_list_and_the_rest_stays() {
        val mine = listOf(
            Item("y", "YouTube", "youtube.com m.youtube.com", ItemType.SITE, rules = listOf(Rule(RuleType.PERMANENT))),
            site("p", "paused.example", disabled = true),
            Item("t", "timed.example", "timed.example", ItemType.SITE, rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30))),
            site("g", "ingroup.example"),
            site("e", "unlocked.example"),
            Item("app", "Game", "com.game", ItemType.APP, rules = listOf(Rule(RuleType.PERMANENT))),
        )
        val cfg = Config(items = mine + imported(60), groups = listOf(Group("G", "G", memberIds = listOf("g"))),
            emergencyUses = listOf(EmergencyUse("2026-10-13T10:00", "t", "2026-10-13T10:20", items = listOf("e"))))
        val out = ImportedSites.migrate(cfg)
        assertEquals(mine.map { it.id }, out.items.map { it.id })
        assertEquals(60, out.imported.size)
        assertEquals(out.imported.sorted(), out.imported)
        assertSame(out, ImportedSites.migrate(out))                       // once only
    }

    @Test fun a_few_import_shaped_items_stay_items() {
        val cfg = Config(items = imported(ImportedSites.MIN_BATCH - 1))
        assertSame(cfg, ImportedSites.migrate(cfg))
    }

    @Test fun every_moved_site_stays_blocked_for_good() {
        val before = Config(items = imported(80))
        val after = ImportedSites.migrate(before)
        for (h in listOf("site1.example", "www.site42.example", "cdn.site80.example")) {
            assertNotNull(h, Enforce.site(before, h, now, null))
            val v = Enforce.site(after, h, now, null)
            assertNotNull(h, v)
            assertEquals("dns", v!!.blockType)
            assertEquals("permanent", v.reason)
        }
        assertNull(Enforce.site(after, "site81.example", now, null))
        assertNull(Enforce.site(after, "example", now, null))
    }

    @Test fun pause_and_the_master_switch_free_imported_sites_as_they_freed_the_items_and_nothing_else_does() {
        val cfg = Config(imported = listOf("bad.example"),
            protection = ProtectionCfg(allowed = listOf("bad.example")))           // a protection-list exception doesn't
        assertNotNull(Enforce.site(cfg, "bad.example", now, null))
        assertNull(Enforce.site(cfg.copy(enabled = false), "bad.example", now, null))
        val paused = cfg.copy(pause = BlockPause(started = now.minusMinutes(5).toString(), until = now.plusHours(1).toString()))
        assertNull(Enforce.site(paused, "bad.example", now, null))
        // an emergency for another item covering the same host doesn't free it
        val other = Item("x", "bad.example", "bad.example", ItemType.SITE, rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 600)))
        val unlocked = cfg.copy(items = listOf(other),
            emergencyUses = listOf(EmergencyUse(now.minusMinutes(1).toString(), "t", now.plusMinutes(19).toString(), items = listOf("x"))))
        assertNotNull(Enforce.site(unlocked, "bad.example", now, null))
    }

    @Test fun import_adds_new_sites_only() {
        val cfg = Config(items = listOf(Item("y", "YouTube", "youtube.com m.youtube.com", ItemType.SITE)), imported = listOf("a.com"))
        val (out, n) = ImportedSites.add(cfg, listOf("a.com", "b.com", "B.com", "youtube.com", "www.c.com", "<html>", "c.com"))
        assertEquals(2, n)
        assertEquals(listOf("a.com", "b.com", "c.com"), out.imported)
    }

    @Test fun an_exported_config_with_imported_items_comes_in_compact() {
        val json = Json { encodeDefaults = true }
        val text = json.encodeToString(Config(items = imported(70)))
        val c = Store.decodeImport(text, Config())!!
        assertTrue(c.items.isEmpty())
        assertEquals(70, c.imported.size)
        // and the compact config is far smaller
        assertTrue(json.encodeToString(c).length * 5 < text.length)
    }
}
