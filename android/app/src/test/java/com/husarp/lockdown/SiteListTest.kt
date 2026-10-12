package com.husarp.lockdown

import com.husarp.lockdown.engine.ListedSite
import com.husarp.lockdown.engine.SiteIndex
import com.husarp.lockdown.engine.SiteList
import com.husarp.lockdown.engine.SiteSearch
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class SiteListTest {
    /** The real bundled list (the unit tests run in the app module's folder). */
    private val real: SiteIndex by lazy { SiteList.parse(File("src/main/assets/sites/sites.json").readText()) }

    private fun names(q: String, limit: Int = 8) = real.suggest(q, limit).map { it.name }

    @Test fun the_bundled_list_parses_in_order_with_main_hosts_first() {
        assertTrue(real.sites.size >= 400)
        assertEquals((1..real.sites.size).toList(), real.sites.map { it.rank })      // rank = 1-based place in the file
        assertEquals("YouTube", real.sites.first().name)
        assertEquals("youtube.com", real.sites.first().main)
        assertTrue(real.sites.all { it.name.isNotBlank() && it.domains.isNotEmpty() && it.category.isNotBlank() })
        assertTrue(real.sites.all { s -> s.domains.all { it == it.lowercase() && ' ' !in it } })
    }

    @Test fun typing_the_start_of_a_name_finds_it_first() {
        assertEquals("YouTube", names("you").first())
        assertEquals("YouTube", names("yout").first())
        assertEquals("Reddit", names("redd").first())
        assertEquals("Allegro", names("allegro").first())
    }

    @Test fun a_domain_start_finds_a_site_whose_name_differs() {
        assertEquals("Twitter/X", names("x.c").first())
    }

    @Test fun small_typos_and_swapped_letters_still_find_it() {
        assertEquals("YouTube", names("youtub").first())
        assertTrue("YouTube" in names("yuotube"))              // swapped neighbours
        assertTrue("Reddit" in names("redit"))
        assertEquals("Allegro", names("alegro").first())
        assertTrue(names("zzqqxx").isEmpty())
    }

    @Test fun capitals_accents_and_the_polish_l_do_not_matter() {
        assertEquals(names("allegro"), names("ALLEGRO"))
        assertEquals("lodz", SiteSearch.normalize("Łódź"))
        assertEquals("cafe", SiteSearch.normalize("Café"))
    }

    @Test fun the_limit_and_blank_text_are_respected() {
        assertTrue(real.suggest("a", 8).size <= 8)
        assertEquals(3, real.suggest("o", 3).size)
        assertTrue(real.suggest("").isEmpty())
        assertTrue(real.suggest("   ").isEmpty())
    }

    @Test fun ranking_goes_by_typos_then_name_start_then_domain_then_word_then_popularity() {
        val idx = SiteIndex(listOf(
            ListedSite("Zeta Games", listOf("zz.com"), rank = 1),                    // a word of the name starts with "gam"
            ListedSite("Other", listOf("games.org"), rank = 2),                      // a domain starts with it
            ListedSite("Gamer", listOf("gamer.net"), rank = 3),                      // the name starts with it
            ListedSite("Gamestop", listOf("gamestop.com"), rank = 4),                // the name starts with it, less popular
            ListedSite("Plain", listOf("plain.com"), rank = 5),
        ))
        assertEquals(listOf("Gamer", "Gamestop", "Other", "Zeta Games"), idx.suggest("gam").map { it.name })
        // an exact word beats a word found only with a typo, whatever the popularity
        val typo = SiteIndex(listOf(ListedSite("Redit", listOf("redit.io"), rank = 1), ListedSite("Reddit", listOf("reddit.com"), rank = 2)))
        assertEquals(listOf("Reddit", "Redit"), typo.suggest("reddit").map { it.name })
    }

    @Test fun every_typed_word_must_be_found() {
        val idx = SiteIndex(listOf(ListedSite("Lethal Company", listOf("lethal.example"), rank = 1), ListedSite("Lethal Weapon", listOf("weapon.example"), rank = 2)))
        assertEquals(listOf("Lethal Company"), idx.suggest("lethal comp").map { it.name })
        assertEquals(listOf("Lethal Company"), idx.suggest("lethl compn").map { it.name })
    }

    @Test fun siteOf_finds_a_host_or_a_parent_domain() {
        assertEquals("YouTube", real.siteOf("youtube.com")?.name)
        assertEquals("YouTube", real.siteOf("www.youtube.com")?.name)
        assertEquals("YouTube", real.siteOf("music.youtube.com")?.name)        // a subdomain: the parent domain
        assertEquals("YouTube", real.siteOf("M.YouTube.com")?.name)
        assertEquals("YouTube", real.siteOf("https://www.youtu.be/abc")?.name)
        assertEquals("Reddit", real.siteOf("old.reddit.com")?.name)
        assertNull(real.siteOf("notasite-zz.example"))
        assertNull(real.siteOf("com"))
        assertNull(real.siteOf(""))
    }

    @Test fun a_picked_site_carries_all_its_hosts_and_its_name() {
        val yt = real.suggest("youtube").first()
        assertEquals("youtube.com m.youtube.com youtu.be googlevideo.com", yt.target)
        assertEquals("YouTube", yt.name)
        assertEquals("youtube.com", yt.main)
    }

    @Test fun a_typed_host_that_is_one_of_a_sites_own_gets_all_its_hosts() {
        assertEquals("YouTube", real.siteTyped(listOf("youtu.be"))?.name)
        assertEquals("YouTube", real.siteTyped(listOf("other.example", "https://www.youtube.com/x"))?.name)
        assertNull(real.siteTyped(listOf("a.youtube.com")))                    // only a subdomain: not the whole site
        assertNull(real.siteTyped(listOf("nothing-listed.example")))
        assertNull(real.siteTyped(emptyList()))
    }

    @Test fun a_shared_host_belongs_to_the_more_popular_site() {
        val idx = SiteIndex(listOf(ListedSite("First", listOf("a.com", "shared.com"), rank = 1), ListedSite("Second", listOf("b.com", "shared.com"), rank = 2)))
        assertEquals("First", idx.siteOf("shared.com")?.name)
    }

    @Test fun parse_ignores_unknown_fields_and_skips_sites_without_hosts() {
        val idx = SiteList.parse("""{"version":1,"categories":["A"],"future":1,"sites":[{"name":"One","domains":["one.com"],"category":"A","extra":true},{"name":"None","domains":[],"category":"A"},{"name":"Two","domains":["two.com","t.co"],"category":"A","icon":"two.png"}]}""")
        assertEquals(listOf("One", "Two"), idx.sites.map { it.name })
        assertEquals(listOf(1, 2), idx.sites.map { it.rank })
        assertEquals("two.png", idx.sites[1].icon)
        assertNotNull(idx.siteOf("t.co"))
    }

    @Test fun distance_counts_edits_and_a_swap_as_one() {
        assertEquals(0, SiteSearch.distance("reddit", "reddit", 2))
        assertEquals(1, SiteSearch.distance("redit", "reddit", 2))
        assertEquals(1, SiteSearch.distance("rdedit", "reddit", 2))
        assertEquals(3, SiteSearch.distance("abcdef", "uvwxyz", 2))            // over the limit: limit + 1
        assertEquals(null, SiteSearch.score("xyz", "youtube"))                 // 3 letters: no typos allowed
        assertEquals(0, SiteSearch.score("youtub", "youtube"))                 // found inside the text
        assertEquals(1, SiteSearch.score("yuotub", "youtube"))                 // a swap
    }
}
