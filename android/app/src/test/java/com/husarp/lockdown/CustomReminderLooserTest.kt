package com.husarp.lockdown

import com.husarp.lockdown.data.Config
import com.husarp.lockdown.engine.CustomCfg
import com.husarp.lockdown.engine.RemindersEngine
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Which edits of a reminder of yours need the challenge on Save: only an Important one, only when it asks for less. */
class CustomReminderLooserTest {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val g = CustomCfg("a", text = "Water", kind = "interval", everySec = 1800, guarded = true, snooze = 5, maxSnooze = 3)
    private fun loosens(old: CustomCfg, new: CustomCfg) = RemindersEngine.customEditLoosens(old, new)

    @Test fun a_longer_snooze_on_an_important_reminder_needs_the_challenge_a_shorter_one_is_free() {
        assertTrue(loosens(g, g.copy(snooze = 10)))
        assertFalse(loosens(g, g.copy(snooze = 1)))
        assertFalse(loosens(g, g.copy(snooze = 5)))
        assertTrue(loosens(g, g.copy(maxSnooze = 4)))
        assertFalse(loosens(g, g.copy(maxSnooze = 1)))
    }

    @Test fun the_did_you_do_it_delay_is_free_either_way() {
        val c = g.copy(check = 10)
        assertFalse(loosens(c, c.copy(check = 30)))
        assertFalse(loosens(c, c.copy(check = 0)))
        assertFalse(loosens(g, g.copy(check = 15)))
    }

    @Test fun a_new_or_narrower_hours_window_on_an_every_reminder_needs_the_challenge() {
        val all = g.copy(hours = false, window = listOf("10:00", "18:00"))
        assertTrue(loosens(all, all.copy(hours = true)))                                    // newly-set window: fewer reminders
        val w = g.copy(hours = true, window = listOf("08:00", "20:00"))
        assertTrue(loosens(w, w.copy(window = listOf("09:00", "20:00"))))                   // starts later
        assertTrue(loosens(w, w.copy(window = listOf("08:00", "19:00"))))                   // ends sooner
        assertTrue(loosens(w, w.copy(window = listOf("20:00", "08:00"))))                   // a different part of the day
        assertFalse(loosens(w, w.copy(window = listOf("07:00", "21:00"))))                  // wider
        assertFalse(loosens(w, w.copy(window = listOf("08:00", "20:00"))))
        assertFalse(loosens(w, w.copy(hours = false)))                                      // hours off = all day
    }

    @Test fun a_window_past_midnight_is_compared_by_the_minutes_it_covers() {
        val night = g.copy(hours = true, window = listOf("22:00", "06:00"))
        assertFalse(loosens(night, night.copy(window = listOf("21:00", "07:00"))))
        assertTrue(loosens(night, night.copy(window = listOf("23:00", "06:00"))))
        assertFalse(loosens(night, night.copy(window = listOf("22:00", "06:00"))))
    }

    @Test fun the_other_rules_match_the_pc() {
        assertTrue(loosens(g, g.copy(everySec = 3600)))                                     // asks less often
        assertFalse(loosens(g, g.copy(everySec = 900)))
        assertTrue(loosens(g, g.copy(days = listOf(0, 1, 2, 3, 4))))                        // a day dropped
        assertFalse(loosens(g.copy(days = listOf(0, 1)), g.copy(days = listOf(0, 1, 2))))
        assertTrue(loosens(g, g.copy(kind = "times")))                                      // another kind
        val t = g.copy(kind = "times", times = listOf("09:00", "12:00"))
        assertTrue(loosens(t, t.copy(times = listOf("09:00"))))
        assertFalse(loosens(t, t.copy(times = listOf("09:00", "12:00", "15:00"))))
        val r = g.copy(kind = "random", window = listOf("10:00", "18:00"))
        assertTrue(loosens(r, r.copy(window = listOf("11:00", "18:00"))))
        assertFalse(loosens(r, r.copy(window = listOf("09:00", "19:00"))))
        assertTrue(loosens(g, g.copy(perDay = 3)))                                          // a new daily cap
        assertTrue(loosens(g.copy(perDay = 3), g.copy(perDay = 2)))
        assertFalse(loosens(g.copy(perDay = 3), g.copy(perDay = 4)))
        assertFalse(loosens(g.copy(perDay = 3), g.copy(perDay = 0)))
    }

    @Test fun turning_off_or_dropping_the_guard_still_does() {
        assertTrue(loosens(g, g.copy(on = false)))
        assertTrue(loosens(g, g.copy(guarded = false)))
        assertFalse(loosens(g.copy(on = false), g.copy(on = true)))
    }

    @Test fun an_ordinary_reminder_is_edited_freely() {
        val c = g.copy(guarded = false, hours = true)
        assertFalse(loosens(c, c.copy(snooze = 30, window = listOf("12:00", "13:00"), everySec = 7200, days = listOf(0), on = false)))
        assertTrue(loosens(g, g.copy(snooze = 5, guarded = false)))                         // but un-flagging an Important one is not
    }

    @Test fun snooze_check_hours_and_window_survive_the_config_file() {
        val c = Config(customs = listOf(CustomCfg("a", kind = "interval", everySec = 600, hours = true, window = listOf("08:30", "21:00"), snooze = 15, check = 30)))
        val back = json.decodeFromString<Config>(json.encodeToString(c)).customs.single()
        assertEquals(15, back.snooze); assertEquals(30, back.check)
        assertTrue(back.hours); assertEquals(listOf("08:30", "21:00"), back.window)
    }
}
