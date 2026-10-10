package com.husarp.lockdown

import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.EmergencyCfg
import com.husarp.lockdown.data.Guardrails
import com.husarp.lockdown.data.GuardrailsEdit
import com.husarp.lockdown.data.ProtectionCfg
import com.husarp.lockdown.data.Settings
import com.husarp.lockdown.data.SettingsEdit
import com.husarp.lockdown.data.inSavedOrder
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.AntiBypassCfg
import com.husarp.lockdown.engine.BreakCfg
import com.husarp.lockdown.engine.EmergencyTier
import com.husarp.lockdown.engine.KeywordsCfg
import com.husarp.lockdown.engine.RemindersEngine
import com.husarp.lockdown.engine.SleepCfg
import com.husarp.lockdown.engine.Window
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The Save step: a page's draft is checked once as a whole against the saved settings, and Save writes only that
 *  page's settings. */
class DraftsTest {
    private val week = EmergencyTier("w", 3, "week", 5)
    private val base = Config(emergency = EmergencyCfg(enabled = true, tiers = listOf(week)))
    private fun gr(c: Config = base) = GuardrailsEdit.of(c)

    @Test fun turning_the_phrase_on_with_your_own_is_one_free_change() {
        // the reported bug: picking "Type a phrase" saved a random one at once, and your own then needed the challenge
        val new = gr().let { it.copy(ab = it.ab.copy(phrase = true, customPhrase = "my own words")) }
        assertFalse(new.looser(gr()))
        val saved = gr(new.applyTo(base))
        assertTrue(saved.copy(ab = saved.ab.copy(customPhrase = "other words")).looser(saved))   // once saved: the challenge
    }

    @Test fun one_loosening_anywhere_makes_the_whole_draft_need_the_challenge() {
        val on = gr(base.copy(antibypass = AntiBypassCfg(phrase = true)))
        val tighter = on.copy(ab = on.ab.copy(length = 120, waitMin = 5))
        assertFalse(tighter.looser(on))
        assertTrue(tighter.copy(tiers = listOf(week.copy(uses = 4))).looser(on))                // + one more use
        assertTrue(tighter.copy(guardrails = Guardrails(persist = false)).looser(on))
        assertTrue(tighter.copy(guardrails = Guardrails(trustedTime = false)).looser(on))
        val admin = gr(base.copy(guardrails = Guardrails(uninstallProtection = true)))
        assertTrue(admin.copy(guardrails = Guardrails(uninstallProtection = false)).looser(admin))
        assertFalse(on.copy(guardrails = Guardrails(uninstallProtection = true), emergencyOn = false).looser(on))   // tighter: free
    }

    @Test fun the_unlock_window_is_no_edit_and_save_keeps_it() {
        val open = base.copy(antibypass = AntiBypassCfg(phrase = true, unlockedFrom = "2026-10-09T10:00", unlockedUntil = "2026-10-09T10:05"))
        assertEquals(gr(base.copy(antibypass = AntiBypassCfg(phrase = true))), gr(open))       // a challenge passed meanwhile isn't a change
        val e = gr(open).let { it.copy(ab = it.ab.copy(length = 90)) }
        val saved = e.applyTo(open).antibypass
        assertEquals(90, saved.length)
        assertEquals("2026-10-09T10:05", saved.unlockedUntil)
    }

    @Test fun save_leaves_untouched_parts_as_they_are() {
        val old = Config(emergency = EmergencyCfg(minutes = 15))                        // from before kinds: tiers null
        val e = gr(old).copy(guardrails = Guardrails(persist = false))
        val out = e.applyTo(old)
        assertNull(out.emergency.tiers)
        assertEquals(old.antibypass, out.antibypass)
        assertFalse(out.guardrails.persist)
    }

    @Test fun hours_need_a_day_in_every_window() {
        val ab = AntiBypassCfg(hours = true, windows = listOf(Window(listOf(6), "18:00", "20:00"), Window(emptyList(), "08:00", "09:00")))
        assertTrue(gr().copy(ab = ab).problem() != null)
        assertNull(gr().copy(ab = ab.copy(windows = ab.windows.take(1))).problem())
        assertNull(gr().copy(ab = ab.copy(hours = false)).problem())                          // off: the windows don't matter
    }

    @Test fun settings_loosen_only_by_turning_protection_off() {
        val c = Config(settings = Settings(siteFilterOn = true, pauseBeforeOpen = true, pauseSec = 20), protection = ProtectionCfg(enabled = listOf("gambling")))
        val s = SettingsEdit.of(c)
        assertTrue(s.copy(siteFilter = false).looser(s))
        assertTrue(s.copy(protection = emptySet()).looser(s))
        assertTrue(s.copy(pauseBeforeOpen = false).looser(s))
        assertTrue(s.copy(pauseSec = 15).looser(s))
        assertTrue(s.copy(forceSafeSearch = false).looser(s))
        assertFalse(s.copy(protection = setOf("gambling", "adult"), pauseSec = 30, weeklyDigest = true, networkLog = false,
            bedtimeGrayscale = true, dailyGoalMin = 120).looser(s))
    }

    @Test fun settings_save_writes_only_its_own_fields() {
        val c = Config(settings = Settings(theme = "dark", siteFilterOn = true), protection = ProtectionCfg(enabled = listOf("b", "a"), allowed = listOf("x.com")))
        val e = SettingsEdit.of(c).copy(siteFilter = false, protection = setOf("a", "b", "c"), weeklyDigest = true)
        val out = e.applyTo(c)
        assertTrue(out.settings.siteFilterOn)                   // stopping the filter writes that, not Save
        assertEquals("dark", out.settings.theme)
        assertTrue(out.settings.weeklyDigest)
        assertEquals(listOf("b", "a", "c"), out.protection.enabled)
        assertEquals(listOf("x.com"), out.protection.allowed)
    }

    @Test fun bedtime_and_breaks_loosen_by_their_guard_or_a_shorter_wait() {
        val s = SleepCfg(on = true, guarded = true, dismissWait = 30)
        assertFalse(RemindersEngine.bedtimeLooser(s, s.copy(bedtime = "21:00", tiers = emptyList(), before = 0, dismissWait = 60)))
        assertTrue(RemindersEngine.bedtimeLooser(s, s.copy(guarded = false)))
        assertTrue(RemindersEngine.bedtimeLooser(s, s.copy(dismissWait = 10)))
        val b = BreakCfg(guarded = true)
        assertFalse(RemindersEngine.breaksLooser(b, b.copy(every = 90, strict = false)))
        assertTrue(RemindersEngine.breaksLooser(b, b.copy(guarded = false)))
        assertFalse(RemindersEngine.breaksLooser(BreakCfg(), BreakCfg(guarded = true)))
    }

    @Test fun blocked_words_loosen_by_anything_off_or_an_exception() {
        val k = KeywordsCfg(words = listOf("a", "b"), off = listOf("x"), exceptions = listOf("e.com"))
        assertFalse(AntiBypass.keywordsLooser(k, k.copy(words = listOf("a", "b", "c"), off = emptyList(), exceptions = emptyList(),
            lists = k.lists + ("other" to true))))
        assertTrue(AntiBypass.keywordsLooser(k, k.copy(enabled = false)))
        assertTrue(AntiBypass.keywordsLooser(k, k.copy(safeSearch = false)))
        assertTrue(AntiBypass.keywordsLooser(k, k.copy(youtube = false)))
        assertTrue(AntiBypass.keywordsLooser(k, k.copy(wordsOn = false)))
        assertTrue(AntiBypass.keywordsLooser(k, k.copy(lists = k.lists + ("adult_en" to false))))
        assertTrue(AntiBypass.keywordsLooser(k, k.copy(off = listOf("x", "porn*"))))
        assertTrue(AntiBypass.keywordsLooser(k, k.copy(words = listOf("a"))))
        assertTrue(AntiBypass.keywordsLooser(k, k.copy(exceptions = listOf("e.com", "f.com"))))
    }

    @Test fun taken_out_and_put_back_is_no_change() {
        assertEquals(listOf("b", "a"), inSavedOrder(listOf("b", "a"), listOf("a", "b")))
        assertEquals(listOf("a", "c"), inSavedOrder(listOf("b", "a"), listOf("c", "a")))
    }

    @Test fun the_settings_sections_with_unsaved_changes_get_their_dot() {
        val s = SettingsEdit.of(Config())
        assertEquals(emptySet<String>(), s.changedSections(s))
        assertEquals(setOf("filter"), s.copy(siteFilter = true).changedSections(s))
        assertEquals(setOf("filter"), s.copy(protection = setOf("gambling")).changedSections(s))
        assertEquals(setOf("filter"), s.copy(networkLog = !s.networkLog, forceSafeSearch = !s.forceSafeSearch).changedSections(s))
        assertEquals(setOf("limits"), s.copy(pauseSec = 20).changedSections(s))
        assertEquals(setOf("limits"), s.copy(dailyGoalMin = 180, pauseBeforeOpen = true).changedSections(s))
        assertEquals(setOf("extras"), s.copy(weeklyDigest = true).changedSections(s))
        assertEquals(setOf("filter", "limits", "extras"),
            s.copy(siteFilter = true, pauseSec = 20, bedtimeGrayscale = true).changedSections(s))
    }

    @Test fun the_guardrails_sections_with_unsaved_changes_get_their_dot() {
        val g = gr()
        assertEquals(emptySet<String>(), g.changedSections(g))
        assertEquals(setOf("challenge"), g.copy(ab = g.ab.copy(phrase = true)).changedSections(g))
        assertEquals(setOf("emergency"), g.copy(emergencyOn = false).changedSections(g))
        assertEquals(setOf("emergency"), g.copy(tiers = listOf(week.copy(minutes = 10))).changedSections(g))
        assertEquals(setOf("switches"), g.copy(guardrails = Guardrails(persist = false)).changedSections(g))
        assertEquals(setOf("challenge", "switches"),
            g.copy(ab = g.ab.copy(waitMin = 5), guardrails = Guardrails(trustedTime = false)).changedSections(g))
    }
}
