package com.husarp.lockdown

import com.husarp.lockdown.block.Enforce
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.engine.Browsers
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.Sites
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

/** The browsers' address bars the accessibility service reads, and DuckDuckGo's own ways to a site. */
class BrowsersTest {
    private val ddg = "com.duckduckgo.mobile.android"

    @Test fun every_browser_has_an_address_bar_to_read() {
        for ((pkg, ids) in Browsers.BARS) {
            assertTrue("$pkg has no address-bar id", ids.isNotEmpty())
            for (id in Browsers.barIds(pkg)) assertTrue(id, id.startsWith("$pkg:id/") && id.length > "$pkg:id/".length)
        }
        assertTrue(Browsers.isBrowser(ddg))
        assertTrue(Browsers.isBrowser("com.opera.browser"))
    }

    @Test fun duckduckgo_bar_ids_cover_top_bottom_and_custom_tabs() {
        val ids = Browsers.barIds(ddg)
        assertEquals("$ddg:id/omnibarTextInput", ids.first())       // the address bar, top or bottom
        assertTrue("$ddg:id/omnibarTextInputBottom" in ids)
        assertTrue("$ddg:id/customTabDomain" in ids)
        assertTrue("$ddg:id/customTabDomainOnly" in ids)
        assertEquals(listOf("com.android.chrome:id/url_bar"), Browsers.barIds("com.android.chrome"))
        assertTrue(Browsers.barIds("com.example.notabrowser").isEmpty())
    }

    @Test fun the_site_is_read_from_a_full_or_short_address() {
        assertEquals("example.com", Browsers.hostOf(ddg, "https://www.example.com/a?b=c"))
        assertEquals("example.com", Browsers.hostOf(ddg, "example.com"))           // DuckDuckGo's short address
        assertEquals("m.example.com", Browsers.hostOf("com.android.chrome", "m.example.com/x"))
        assertNull(Browsers.hostOf(ddg, "how to cook rice"))                     // a search
        assertNull(Browsers.hostOf(ddg, "rice"))
        assertNull(Browsers.hostOf(ddg, null))
    }

    @Test fun duck_player_is_youtube() {
        assertEquals("youtube.com", Browsers.hostOf(ddg, "duck://player/dQw4w9WgXcQ"))
        assertEquals("youtube.com", Browsers.hostOf(ddg, "Duck Player"))          // its short address
        assertNull(Browsers.hostOf("com.android.chrome", "Duck Player"))          // only DuckDuckGo shows it so
        assertEquals("youtube.com", Browsers.hostOf("com.android.chrome", "https://www.youtube-nocookie.com/embed/x"))
        assertEquals("youtube.com", Sites.alias("www.youtube-nocookie.com"))
        assertNull(Sites.alias("youtube.com"))
        assertNull(Sites.alias("notyoutube-nocookie.com"))
    }

    @Test fun a_blocked_youtube_blocks_its_privacy_player_in_the_site_filter_too() {
        val t0 = LocalDateTime.of(2026, 10, 9, 12, 0)
        val yt = Item("y", "YouTube", "youtube.com", ItemType.SITE, blockType = "dns", rules = listOf(Rule(RuleType.PERMANENT)))
        val cfg = Config(items = listOf(yt))
        assertEquals("y", Enforce.site(cfg, "www.youtube-nocookie.com", t0, null)?.itemId)
        assertNotNull(Enforce.site(cfg, "youtube.com", t0, null))
        assertNull(Enforce.site(Config(), "www.youtube-nocookie.com", t0, null))
        assertNull(Enforce.site(cfg, "example.com", t0, null))
    }
}
