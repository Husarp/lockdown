package com.husarp.lockdown

import com.husarp.lockdown.block.Keywords as KeywordsGate
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.ProtectionCfg
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.Keywords
import com.husarp.lockdown.engine.KeywordsCfg
import com.husarp.lockdown.link.LinkProtocol
import com.husarp.lockdown.link.StateBody
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** "Blocked words don't apply here" on an allowed site: the word check, the challenge, old and new files. */
class AllowedWordsTest {
    private val kw = KeywordsCfg()
    private val bad = "https://dictionary.example.com/define/porn"
    private val skip = setOf("dictionary.example.com")
    private val json = Json { encodeDefaults = true }

    @Test fun a_skipped_site_and_its_subdomains_are_not_checked_but_other_sites_are() {
        assertEquals("porn*", Keywords.find(bad, "porn - Dictionary", kw))
        assertNull(Keywords.find(bad, "porn - Dictionary", kw, skip))
        assertNull(Keywords.find("https://en.dictionary.example.com/define/porn", "porn", kw, skip))      // a subdomain
        assertEquals("porn*", Keywords.find("https://example.com/porn", "", kw, skip))                    // the parent
        assertEquals("porn*", Keywords.find("https://notdictionary.example.com/porn", "", kw, skip))      // not a look-alike
    }

    @Test fun it_works_like_a_keyword_exception_and_the_exceptions_still_work() {
        assertNull(Keywords.find(bad, "", kw.copy(exceptions = listOf("dictionary.example.com"))))
        assertEquals("porn*", Keywords.find(bad, "", kw.copy(exceptions = listOf("dictionary.example.com"), exceptionsOn = false)))
        assertNull(Keywords.find(bad, "", kw.copy(exceptions = listOf("x.com"), exceptionsOn = false), skip))   // independent of them
    }

    @Test fun the_browser_check_uses_the_allowed_list() {
        val cfg = Config(protection = ProtectionCfg(allowed = listOf("dictionary.example.com"), allowedWords = listOf("dictionary.example.com")))
        assertNull(KeywordsGate.hit(cfg, bad))
        assertEquals("porn*", KeywordsGate.hit(cfg, "https://example.com/porn"))
        val off = cfg.copy(protection = cfg.protection.copy(allowedWords = emptyList()))       // allowed, but words still count
        assertEquals("porn*", KeywordsGate.hit(off, bad))
    }

    @Test fun a_name_left_after_its_site_is_not_allowed_any_more_counts_for_nothing() {
        val p = ProtectionCfg(allowed = listOf("A.com"), allowedWords = listOf("a.com", "gone.com"))
        assertEquals(setOf("a.com"), p.wordsSkip())
        assertEquals(emptySet<String>(), ProtectionCfg(allowed = listOf("a.com")).wordsSkip())          // older: none
    }

    @Test fun only_turning_it_on_needs_the_challenge() {
        val en = listOf("adult")
        val none = emptyList<String>()
        // on for an allowed site that had it off: loosens
        assertTrue(AntiBypass.protectionLooser(en, listOf("a.com"), none, en, listOf("a.com"), listOf("a.com")))
        // off: free; unchanged: free
        assertFalse(AntiBypass.protectionLooser(en, listOf("a.com"), listOf("a.com"), en, listOf("a.com"), none))
        assertFalse(AntiBypass.protectionLooser(en, listOf("a.com"), listOf("a.com"), en, listOf("a.com"), listOf("a.com")))
        // a new allowed site (with or without it): the site itself loosens
        assertTrue(AntiBypass.protectionLooser(en, none, none, en, listOf("a.com"), none))
        assertTrue(AntiBypass.protectionLooser(en, none, none, en, listOf("a.com"), listOf("a.com")))
        // removing the site (and its switch) is free; a switch for a site that is not allowed does nothing
        assertFalse(AntiBypass.protectionLooser(en, listOf("a.com"), listOf("a.com"), en, none, none))
        assertFalse(AntiBypass.protectionLooser(en, listOf("a.com"), none, en, listOf("a.com"), listOf("zzz.com")))
        // the old four-argument check is unchanged
        assertFalse(AntiBypass.protectionLooser(en, listOf("a.com"), en, listOf("a.com")))
    }

    @Test fun an_old_file_reads_with_every_entry_off_and_a_new_one_round_trips() {
        val old = """{"protection":{"enabled":["adult"],"allowed":["old.com"]}}"""
        val got = Store.decodeImport(old, Config())!!
        assertEquals(listOf("old.com"), got.protection.allowed)
        assertEquals(emptyList<String>(), got.protection.allowedWords)
        assertEquals(emptySet<String>(), got.protection.wordsSkip())
        val c = Config(protection = ProtectionCfg(allowed = listOf("a.com", "b.com"), allowedWords = listOf("b.com")))
        val back = Store.decodeImport(json.encodeToString(c), Config())!!
        assertEquals(listOf("b.com"), back.protection.allowedWords)
    }

    @Test fun the_helper_reads_older_and_newer_configs() {
        // main -> helper carries the config JSON (StateBody.cfg): a helper that already knows the field reads an older config...
        val older = LinkProtocol.json.decodeFromString<StateBody>(
            LinkProtocol.json.encodeToString(StateBody(cfg = """{"protection":{"allowed":["old.com"]}}""")))
        assertEquals(emptyList<String>(), Store.decodeImport(older.cfg!!, Config())!!.protection.allowedWords)
        // ...and an older helper skips the field it doesn't know (ignoreUnknownKeys)
        val newer = json.encodeToString(Config(protection = ProtectionCfg(allowed = listOf("a.com"), allowedWords = listOf("a.com"))))
        val lenient = Json { ignoreUnknownKeys = true }
        assertEquals(listOf("a.com"), lenient.decodeFromString<OldProtection>(
            lenient.parseToJsonElement(newer).let { (it as kotlinx.serialization.json.JsonObject)["protection"].toString() }).allowed)
    }

    @kotlinx.serialization.Serializable
    private data class OldProtection(val enabled: List<String> = emptyList(), val allowed: List<String> = emptyList())
}
