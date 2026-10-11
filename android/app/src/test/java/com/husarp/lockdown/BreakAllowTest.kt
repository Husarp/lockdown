package com.husarp.lockdown

import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.BreakAllow
import com.husarp.lockdown.engine.BreakCfg
import com.husarp.lockdown.engine.RemindersEngine
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BreakAllowTest {
    private val phone = setOf("com.example.launcher", "com.example.clock", "com.example.keyboard")   // resolved on the phone
    private val none = emptyList<String>()

    @Test fun built_in_apps_are_allowed() {
        for (p in listOf("com.android.settings", "com.android.systemui", "com.google.android.permissioncontroller",
            "com.android.permissioncontroller", "com.android.packageinstaller", "com.android.emergency",
            "com.android.incallui", "com.android.phone", "com.example.launcher", "com.example.clock", "com.example.keyboard"))
            assertTrue(p, BreakAllow.allowed(p, phone, none, inCall = false))
    }

    @Test fun an_unknown_front_is_allowed_and_another_app_is_not() {
        assertTrue(BreakAllow.allowed(null, phone, none, false))
        assertTrue(BreakAllow.allowed("", phone, none, false))
        assertFalse(BreakAllow.allowed("com.game.fun", phone, none, false))
    }

    @Test fun the_user_list_is_allowed() {
        assertTrue(BreakAllow.allowed("com.notes.app", phone, listOf("com.notes.app"), false))
        assertFalse(BreakAllow.allowed("com.other.app", phone, listOf("com.notes.app"), false))
    }

    @Test fun a_call_allows_anything() {
        assertTrue(BreakAllow.allowed("com.video.call", phone, none, inCall = true))
    }

    @Test fun before_the_dismiss_the_cover_is_always_on() {
        assertTrue(BreakAllow.covered(false, "com.example.launcher", phone, none, false))
        assertTrue(BreakAllow.covered(false, "com.game.fun", phone, none, false))
    }

    @Test fun after_the_dismiss_only_a_not_allowed_app_brings_the_cover_back() {
        val user = listOf("com.notes.app")
        assertFalse(BreakAllow.covered(true, "com.example.launcher", phone, user, false))    // home screen
        assertFalse(BreakAllow.covered(true, "com.notes.app", phone, user, false))           // on the list
        assertFalse(BreakAllow.covered(true, "com.android.settings", phone, user, false))    // built in
        assertFalse(BreakAllow.covered(true, "", phone, user, false))                        // not known yet
        assertTrue(BreakAllow.covered(true, "com.game.fun", phone, user, false))             // anything else
        assertFalse(BreakAllow.covered(true, "com.game.fun", phone, user, inCall = true))    // but not over a call
    }

    @Test fun adding_an_app_needs_the_challenge_and_removing_is_free() {
        val old = BreakCfg(allowed = listOf("a", "b"))
        assertTrue(AntiBypass.breakAppsLooser(old.allowed, listOf("a", "b", "c")))
        assertTrue(AntiBypass.breakAppsLooser(old.allowed, listOf("c")))                      // swapped one for another
        assertTrue(AntiBypass.breakAppsLooser(emptyList(), listOf("a")))
        assertFalse(AntiBypass.breakAppsLooser(old.allowed, listOf("a")))                     // removed
        assertFalse(AntiBypass.breakAppsLooser(old.allowed, emptyList()))
        assertFalse(AntiBypass.breakAppsLooser(old.allowed, listOf("b", "a", "a", " ")))      // same apps (repeats / blanks aside)
        // and the Breaks page judges it with the rest of the draft
        assertTrue(RemindersEngine.breaksLooser(old, old.copy(allowed = old.allowed + "c")))
        assertFalse(RemindersEngine.breaksLooser(old, old.copy(allowed = listOf("a"), every = 90)))
    }

    @Test fun the_saved_list_has_no_blanks_or_repeats() {
        assertEquals(listOf("a", "b"), BreakAllow.clean(listOf(" a ", "", "b", "a")))
    }

    @Test fun the_keyboard_package_comes_from_its_id() {
        assertEquals("com.google.android.inputmethod.latin",
            BreakAllow.keyboardPackage("com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME"))
        assertEquals(null, BreakAllow.keyboardPackage(null))
        assertEquals(null, BreakAllow.keyboardPackage(""))
    }

    @Test fun an_old_config_loads_with_an_empty_list_and_a_new_one_round_trips() {
        val old = """{"breaks":{"on":true,"every":30,"length":5,"strict":true}}"""
        val got = Store.decodeImport(old, Config())!!
        assertTrue(got.breaks.strict)
        assertEquals(emptyList<String>(), got.breaks.allowed)
        val json = Json { encodeDefaults = true; ignoreUnknownKeys = true }
        val c = got.copy(breaks = got.breaks.copy(allowed = listOf("com.notes.app")))
        assertEquals(listOf("com.notes.app"), Store.decodeImport(json.encodeToString(c), Config())!!.breaks.allowed)
        // the Island copy (an older build) ignores the field it doesn't know
        assertTrue(json.decodeFromString<com.husarp.lockdown.engine.BreakCfg>("""{"strict":true,"allowed":["x"],"future":1}""").strict)
    }
}
