package com.husarp.lockdown

import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.ProtectionCfg
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.IconBook
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.KeywordsCfg
import com.husarp.lockdown.engine.Mode
import com.husarp.lockdown.engine.ModeExtra
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.SiteIconChain
import com.husarp.lockdown.engine.SiteIconChain.Have
import com.husarp.lockdown.engine.SiteIconChain.Page
import com.husarp.lockdown.ui.SiteIconGate
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.time.LocalDateTime

class SiteIconsTest {
    private val t0 = LocalDateTime.of(2026, 10, 12, 12, 0)
    private val now = 1_800_000_000_000L
    private val day = SiteIconChain.DAY_MS

    /** A fake network that records every address it was asked for. */
    private class FakeNet(val images: Map<String, ByteArray> = emptyMap(), val pages: Map<String, String> = emptyMap()) : SiteIconChain.Net {
        val asked = ArrayList<String>()
        override fun image(url: String): ByteArray? { asked.add(url); return images[url] }
        override fun page(url: String): Page? { asked.add(url); return pages[url]?.let { Page(url, it) } }
    }
    private val good = byteArrayOf(1, 2, 3)
    private val decode: (ByteArray) -> String? = { if (it.isNotEmpty() && it[0] == 1.toByte()) "ok" else null }

    // ---- bundled lookup ----

    @Test fun a_subdomain_uses_its_parent_sites_bundled_icon() {
        val names = setOf("youtube.com.png", "reddit.com.png")
        assertEquals("youtube.com.png", SiteIconChain.bundledFor(names, listOf("m.youtube.com")))
        assertEquals("youtube.com.png", SiteIconChain.bundledFor(names, SiteIconChain.hostsOf("www.youtube.com")))
        assertEquals("reddit.com.png", SiteIconChain.bundledFor(names, SiteIconChain.hostsOf("old.reddit.com")))
        assertEquals("youtube.com.png", SiteIconChain.bundledFor(names, SiteIconChain.hostsOf("youtu.be m.youtube.com googlevideo.com")))   // any host of the item
        assertNull(SiteIconChain.bundledFor(names, listOf("notyoutube.com")))
        assertNull(SiteIconChain.bundledFor(names, listOf("example.org")))
    }

    @Test fun the_real_bundled_folder_resolves_m_youtube_com() {
        val dir = File("src/main/assets/sites/icons")
        val names = dir.list()!!.toSet()
        assertTrue("150 bundled icons expected, got ${names.size}", names.size >= 100)
        assertEquals("youtube.com.png", SiteIconChain.bundledFor(names, SiteIconChain.hostsOf("m.youtube.com")))
        assertEquals("reddit.com.png", SiteIconChain.bundledFor(names, SiteIconChain.hostsOf("https://www.reddit.com/r/x")))
        assertNull(SiteIconChain.bundledFor(names, SiteIconChain.hostsOf("unknown.example")))
        assertTrue(File(dir, "youtube.com.png").length() in 1..20_000)
    }

    // ---- host names ----

    @Test fun only_public_host_names_are_looked_up() {
        assertEquals("youtube.com", SiteIconChain.hostOf("https://www.YouTube.com/watch?v=1"))
        assertEquals("example.co.uk", SiteIconChain.hostOf("*.example.co.uk"))
        assertEquals("xn--bcher-kva.example", SiteIconChain.hostOf("bücher.example"))
        for (bad in listOf("192.168.0.1", "10.0.0.5", "localhost", "router.local", "nas.lan", "intranet", "a_b.com", "-x.com", "x..com", "", "  ", "http://[::1]/"))
            assertNull("'$bad'", SiteIconChain.hostOf(bad))
        assertEquals(listOf("youtube.com", "m.youtube.com"), SiteIconChain.hostsOf("youtube.com  m.youtube.com youtube.com 10.0.0.1"))
        assertEquals(listOf("m.youtube.com", "youtube.com"), SiteIconChain.walk("m.youtube.com"))
        assertEquals(listOf("a.b.c.com", "b.c.com", "c.com"), SiteIconChain.walk("a.b.c.com"))
    }

    // ---- the fetch order ----

    @Test fun the_site_itself_first_and_nothing_more_when_its_favicon_works() {
        val net = FakeNet(images = mapOf("https://example.com/favicon.ico" to good))
        assertEquals("ok", SiteIconChain.fetch("example.com", false, net, decode = decode))
        assertEquals(listOf("https://example.com/favicon.ico"), net.asked)
    }

    @Test fun no_favicon_ico_then_the_home_pages_icon_link_then_the_service() {
        val html = """<html><head><link rel="icon" href="/img/i.png" sizes="32x32"><link rel="apple-touch-icon" href="https://cdn.example.com/a.png" sizes="180x180"></head>"""
        val net = FakeNet(images = mapOf("https://example.com/img/i.png" to good), pages = mapOf("https://example.com/" to html))
        assertEquals("ok", SiteIconChain.fetch("example.com", false, net, decode = decode))
        // the 180 px one is the best (smallest from 64 up) and is asked first; it fails here, then the 32 px one works
        assertEquals(listOf("https://example.com/favicon.ico", "https://example.com/", "https://cdn.example.com/a.png", "https://example.com/img/i.png"), net.asked)

        val none = FakeNet()
        assertNull(SiteIconChain.fetch("example.com", false, none, decode = decode))
        assertEquals(listOf("https://example.com/favicon.ico", "https://example.com/", SiteIconChain.serviceUrl("example.com")), none.asked)

        val service = FakeNet(images = mapOf(SiteIconChain.serviceUrl("example.com") to good))
        assertEquals("ok", SiteIconChain.fetch("example.com", false, service, decode = decode))
        assertEquals("https://www.google.com/s2/favicons?domain=example.com&sz=64", service.asked.last())
    }

    @Test fun an_answer_that_is_not_a_usable_picture_goes_on_to_the_next_step() {
        val net = FakeNet(images = mapOf("https://example.com/favicon.ico" to byteArrayOf(9, 9), SiteIconChain.serviceUrl("example.com") to good))
        assertEquals("ok", SiteIconChain.fetch("example.com", false, net, decode = decode))
        assertEquals(listOf("https://example.com/favicon.ico", "https://example.com/", SiteIconChain.serviceUrl("example.com")), net.asked)
    }

    @Test fun a_blocked_domain_is_never_asked_directly_only_the_service() {
        val net = FakeNet(images = mapOf(
            "https://blocked.example/favicon.ico" to good,                      // would work, but must not be asked
            SiteIconChain.serviceUrl("blocked.example") to good))
        assertEquals("ok", SiteIconChain.fetch("blocked.example", true, net, decode = decode))
        assertEquals(listOf(SiteIconChain.serviceUrl("blocked.example")), net.asked)
        assertTrue(net.asked.none { it.startsWith("https://blocked.example") })
        val nothing = FakeNet()
        assertNull(SiteIconChain.fetch("blocked.example", true, nothing, decode = decode))
        assertEquals(listOf(SiteIconChain.serviceUrl("blocked.example")), nothing.asked)
    }

    @Test fun a_stopped_run_asks_nothing_more() {
        val net = FakeNet(images = mapOf("https://example.com/favicon.ico" to good))
        assertNull(SiteIconChain.fetch("example.com", false, net, alive = { false }, decode = decode))
        assertTrue(net.asked.isEmpty())
    }

    @Test fun a_redirect_must_be_https_and_not_to_a_blocked_site() {
        val ok = { _: String -> true }
        assertEquals("https://www.example.com/favicon.ico", SiteIconChain.redirect("https://example.com/favicon.ico", "https://www.example.com/favicon.ico", ok))
        assertEquals("https://example.com/new/i.png", SiteIconChain.redirect("https://example.com/a/b", "/new/i.png", ok))
        assertNull(SiteIconChain.redirect("https://example.com/a", "http://example.com/a", ok))
        assertNull(SiteIconChain.redirect("https://example.com/a", null, ok))
        assertNull(SiteIconChain.redirect("https://example.com/a", "https://blocked.example/i.ico") { it != "blocked.example" })
    }

    @Test fun icon_links_best_size_first_and_only_usable_ones() {
        val html = """
            <link rel="stylesheet" href="/x.css">
            <link rel="icon" href="/16.png" sizes="16x16">
            <link rel="shortcut icon" href="/nosize.ico">
            <link rel="icon" sizes="48x48" href='/48.png'>
            <link rel="apple-touch-icon" sizes="180x180" href="/180.png">
            <link rel="icon" href="/128.png" sizes="128x128">
            <link rel="mask-icon" href="/mask.svg">
            <link rel="icon" type="image/svg+xml" href="/v.svg">
            <link rel="icon" href="data:image/png;base64,AAAA">
            <link rel="icon" href="http://example.com/insecure.png" sizes="64x64">
            <link rel="ICON" href="//cdn.example.com/64.png" sizes="64x64 32x32">
        """
        assertEquals(listOf("https://cdn.example.com/64.png", "https://example.com/128.png", "https://example.com/180.png",
            "https://example.com/48.png", "https://example.com/nosize.ico", "https://example.com/16.png"),
            SiteIconChain.iconLinks(html, "https://example.com/"))
        assertEquals(64, SiteIconChain.sizeOf("32x32 64X64"))
        assertEquals(0, SiteIconChain.sizeOf("any"))
    }

    // ---- what to fetch, when ----

    private fun site(id: String, name: String, target: String) =
        Item(id, name, target, ItemType.SITE, rules = listOf(Rule(RuleType.PERMANENT)))

    @Test fun your_lists_sites_are_collected_once_each() {
        val cfg = Config(
            items = listOf(
                site("1", "somesite.net", "somesite.net"),
                site("2", "Reddit", "reddit.com old.reddit.com"),
                site("3", "Again", "www.reddit.com"),                           // same main host as 2: once
                Item("4", "Chrome", "com.android.chrome", ItemType.APP),        // an app: no icon from the net
                site("5", "LAN box", "192.168.0.10"),                           // an address: never
            ),
            keywords = KeywordsCfg(exceptions = listOf("bank.example", "word")),   // a site and a word
            protection = ProtectionCfg(allowed = listOf("fp.example", "bank.example")),
        )
        val modes = listOf(Mode("m", "Work", extra = listOf(
            ModeExtra("Docs", ItemType.SITE, listOf("docs.example.org", "wiki.example.org")),
            ModeExtra("An app", ItemType.APP, listOf("com.some.app")))))
        val got = SiteIconChain.wanted(cfg, modes)
        // sites you named first, then the others, the mode's, the exceptions, the excused
        assertEquals(listOf("reddit.com", "somesite.net", "docs.example.org", "wiki.example.org", "bank.example", "fp.example"), got.map { it.key })
        assertEquals(listOf("reddit.com", "old.reddit.com"), got.first().hosts)
    }

    @Test fun only_sites_without_an_icon_are_fetched_and_never_when_the_setting_is_off() {
        val wants = listOf("bundled.com", "saved.com", "stale.com", "new.com").map { SiteIconChain.Want(it, listOf(it)) }
        val have = { w: SiteIconChain.Want -> when (w.key) {
            "bundled.com" -> Have(bundled = true)
            "saved.com" -> Have(savedAt = now - 29 * day)                     // fresh enough: the saved icon is reused
            "stale.com" -> Have(savedAt = now - 31 * day)                     // over 30 days: fetched again (the old one shows meanwhile)
            else -> Have()
        } }
        assertEquals(listOf("stale.com", "new.com"), SiteIconChain.plan(true, wants, now, IconBook(), have))
        assertEquals(emptyList<String>(), SiteIconChain.plan(false, wants, now, IconBook(), have))      // setting off: nothing
        assertEquals(listOf("new.com"), SiteIconChain.plan(true, wants, now, IconBook(), have, busy = { it == "stale.com" }))
    }

    @Test fun a_failure_waits_before_the_next_try_and_longer_each_time() {
        val w = listOf(SiteIconChain.Want("down.example", listOf("down.example")))
        val none = { _: SiteIconChain.Want -> Have() }
        var book = IconBook().failed("down.example", now)
        assertTrue(SiteIconChain.plan(true, w, now + 9 * 60_000, book, none).isEmpty())          // not sooner than 10 min
        assertEquals(listOf("down.example"), SiteIconChain.plan(true, w, now + 10 * 60_000, book, none))
        book = book.failed("down.example", now + 10 * 60_000)                                    // failed again: an hour
        assertTrue(SiteIconChain.plan(true, w, now + 69 * 60_000, book, none).isEmpty())
        assertEquals(1, SiteIconChain.plan(true, w, now + 70 * 60_000, book, none).size)
        assertEquals(listOf(10L, 60L, 360L, 1440L, 10080L, 10080L), (1..6).map { SiteIconChain.retryMs(it) / 60_000 })
        assertTrue(SiteIconChain.plan(true, w, now, book.fetched("down.example"), none).isNotEmpty())   // got it: no wait
        assertTrue(book.fetched("down.example").fails.isEmpty())
    }

    @Test fun a_run_and_a_day_are_limited() {
        val wants = (1..100).map { SiteIconChain.Want("s$it.example", listOf("s$it.example")) }
        val none = { _: SiteIconChain.Want -> Have() }
        assertEquals(SiteIconChain.MAX_PER_RUN, SiteIconChain.plan(true, wants, now, IconBook(), none).size)
        var book = IconBook()
        repeat(4) { book = book.tried(30, now + it) }                                            // 120 tried today
        assertEquals(0, book.room(now))
        assertTrue(SiteIconChain.plan(true, wants, now, book, none).isEmpty())
        assertEquals(SiteIconChain.MAX_PER_DAY, book.room(now + day))                            // a new day
        assertEquals(30, SiteIconChain.plan(true, wants, now + day, book, none).size)
        assertEquals(90, IconBook().tried(30, now).room(now) )
    }

    @Test fun the_book_survives_a_file_and_forgets_old_failures() {
        val json = Json { encodeDefaults = true }
        val book = IconBook().tried(5, now).failed("a.example", now).failed("a.example", now).failed("old.example", now - 40 * day)
        val back = json.decodeFromString<IconBook>(json.encodeToString(book))
        assertEquals(book, back)
        assertEquals(2, back.fails["a.example"]!!.n)
        assertEquals(setOf("a.example"), back.pruned(now).fails.keys)
        assertEquals(IconBook(), json.decodeFromString<IconBook>("{}"))
    }

    @Test fun the_oldest_saved_icons_go_when_there_are_too_many() {
        val files = (1..5).associate { "s$it.png" to it * 1000L }
        assertEquals(listOf("s1.png", "s2.png"), SiteIconChain.toPrune(files, keep = 3))
        assertTrue(SiteIconChain.toPrune(files, keep = 5).isEmpty())
    }

    @Test fun pictures_are_decoded_small() {
        assertEquals(1, SiteIconChain.sampleFor(64, 64))
        assertEquals(1, SiteIconChain.sampleFor(100, 100))
        assertEquals(2, SiteIconChain.sampleFor(128, 300))
        assertEquals(8, SiteIconChain.sampleFor(512, 512))
        assertEquals(64 to 64, SiteIconChain.fitTo(64, 64))
        assertEquals(64 to 32, SiteIconChain.fitTo(128, 64))
        assertEquals(32 to 64, SiteIconChain.fitTo(40, 80))
        assertEquals(16 to 16, SiteIconChain.fitTo(16, 16))
    }

    // ---- "blocked right now" ----

    @Test fun a_site_blocked_right_now_is_known_to_the_gate() {
        val yt = site("y", "YouTube", "youtube.com")
        val cfg = Config(items = listOf(yt))
        assertTrue(SiteIconGate.blockedNow(cfg, "youtube.com", t0, null))
        assertTrue(SiteIconGate.blockedNow(cfg, "m.youtube.com", t0, null))              // a sub-domain goes with it
        assertFalse(SiteIconGate.blockedNow(cfg, "example.com", t0, null))
        assertFalse(SiteIconGate.blockedNow(Config(), "youtube.com", t0, null))
        assertFalse(SiteIconGate.blockedNow(cfg.copy(items = listOf(yt.copy(disabled = true))), "youtube.com", t0, null))   // paused item
        assertTrue(SiteIconGate.blockedNow(Config(), "dns.google", t0, null))                  // a browser's own DNS server: the filter cuts it off
        assertFalse(SiteIconGate.blockedNow(Config(enabled = false), "dns.google", t0, null))  // Lockdown off: nothing is blocked
    }

    // ---- the setting ----

    @Test fun the_setting_is_on_by_default_and_old_configs_and_backups_keep_it() {
        val old = """{"items":[],"settings":{"theme":"dark"}}"""
        assertTrue(Store.decodeImport(old, Config())!!.settings.siteIcons)                        // an older file: on
        assertTrue(Config().settings.siteIcons)
        val off = Config(settings = Config().settings.copy(siteIcons = false))
        val json = Json { encodeDefaults = true; ignoreUnknownKeys = true }
        val back = Store.decodeImport(json.encodeToString(off), Config())!!
        assertFalse(back.settings.siteIcons)                                                       // a backup / import keeps off
        assertTrue(Store.decodeImport(json.encodeToString(Config()), off)!!.settings.siteIcons)   // ... and on
        assertTrue(json.encodeToString(off).contains("\"siteIcons\":false"))
    }
}
