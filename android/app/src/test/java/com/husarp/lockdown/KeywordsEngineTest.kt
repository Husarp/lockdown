package com.husarp.lockdown

import com.husarp.lockdown.engine.Keywords
import com.husarp.lockdown.engine.KeywordsCfg
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class KeywordsEngineTest {
    private val cfg = KeywordsCfg()   // defaults: adult EN/PL on

    @Test fun matches_whole_word_in_title() {
        assertEquals("boobs", Keywords.find(null, "big boobs here", cfg))
    }

    @Test fun whole_word_only_no_substring() {
        assertNull(Keywords.find(null, "data analysis report", cfg))   // "anal" must not match inside "analysis"
    }

    @Test fun wildcard_matches_longer() {
        assertEquals("porn*", Keywords.find(null, "pornhub best", cfg))   // porn* -> pornhub
    }

    @Test fun phrase_matches() {
        assertEquals("naked girls", Keywords.find(null, "naked girls gallery", cfg))
        assertNull(Keywords.find(null, "girls who are not naked", cfg))    // phrase must be contiguous
    }

    @Test fun accents_ignored_polish() {
        assertEquals("ruchac", Keywords.find(null, "chcę ruchać", cfg))
    }

    @Test fun address_is_checked() {
        assertEquals("xvideos", Keywords.find("xvideos.com", null, cfg))
    }

    @Test fun search_query_in_address_matches() {
        assertEquals("boobs", Keywords.find("https://www.google.com/search?q=boobs", null, cfg))
    }

    @Test fun site_exception_skips_the_site() {
        val c = cfg.copy(exceptions = listOf("reddit.com"))
        assertNull(Keywords.find("reddit.com", "nsfw thread", c))         // whole site excused
        assertEquals("nsfw", Keywords.find("other.com", "nsfw thread", c))
    }

    @Test fun word_exception_never_counts() {
        val c = cfg.copy(exceptions = listOf("nsfw"))
        assertNull(Keywords.find(null, "nsfw only", c))
    }

    @Test fun own_words_and_off_list() {
        assertEquals("swimsuit", Keywords.find(null, "swimsuit haul", cfg.copy(words = listOf("swimsuit"))))
        assertNull(Keywords.find(null, "big boobs", cfg.copy(off = listOf("boobs"))))   // ready word turned off
    }

    @Test fun disabled_finds_nothing() {
        assertNull(Keywords.find(null, "porn", cfg.copy(enabled = false)))
    }
}
