package com.husarp.lockdown

import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.HomeCard
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.AntiBypassCfg
import com.husarp.lockdown.engine.ChallengeStep
import com.husarp.lockdown.engine.ReminderTier
import com.husarp.lockdown.engine.SleepCfg
import com.husarp.lockdown.link.LinkProtocol
import com.husarp.lockdown.link.applyConfig
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import java.time.LocalDateTime
import org.junit.Test

class StoreImportTest {
    private val t0 = LocalDateTime.of(2026, 10, 3, 12, 0)
    private val json = Json { encodeDefaults = true }

    @Test fun unreadable_file_is_rejected() {
        assertNull(Store.decodeImport("not json", Config()))
    }

    @Test fun imported_file_cannot_bring_its_own_unlock_window() {
        // A hand-edited export claiming an unlock until 2099 must not leave the challenge open.
        val forged = Config(enabled = false, antibypass = AntiBypassCfg(phrase = true,
            unlockedFrom = t0.toString(), unlockedUntil = LocalDateTime.of(2099, 1, 1, 0, 0).toString()))
        val current = Config(antibypass = AntiBypassCfg(phrase = true))
        val got = Store.decodeImport(json.encodeToString(forged), current)!!
        assertFalse(got.enabled)                                       // the rest of the file is taken as-is
        assertNull(got.antibypass.unlockedUntil)
        assertEquals("phrase", AntiBypass.status(got.antibypass, t0.plusDays(1)))
    }

    @Test fun current_unlock_window_is_kept() {
        val until = t0.plusMinutes(AntiBypass.UNLOCK_MIN).toString()
        val current = Config(antibypass = AntiBypassCfg(phrase = true, unlockedFrom = t0.toString(), unlockedUntil = until))
        val got = Store.decodeImport(json.encodeToString(Config()), current)!!
        assertEquals(until, got.antibypass.unlockedUntil)
    }

    @Test fun an_old_config_reads_with_the_new_looks_unset_and_they_survive_a_round_trip() {
        val old = """{"items":[],"groups":[{"id":"g","name":"Social","memberIds":["a"]}],"settings":{"theme":"dark"}}"""
        val got = Store.decodeImport(old, Config())!!
        assertNull(got.groups.single().icon)
        assertNull(got.groups.single().color)
        assertNull(got.settings.home)
        assertEquals("dark", got.settings.theme)
        val c = got.copy(groups = got.groups.map { it.copy(icon = "moon", color = "INDIGO") },
            settings = got.settings.copy(home = listOf(HomeCard("apps"), HomeCard("blocked", on = false))))
        val back = Store.decodeImport(json.encodeToString(c), Config())!!
        assertEquals("moon", back.groups.single().icon)
        assertEquals("INDIGO", back.groups.single().color)
        assertEquals(c.settings.home, back.settings.home)
        // an older copy (the Island helper before this version) ignores what it doesn't know
        val lenient = Json { ignoreUnknownKeys = true }
        assertEquals("Social", lenient.decodeFromString<OldGroup>(json.encodeToString(c.groups.single())).name)
    }

    @Test fun an_old_config_reads_its_challenge_as_one_phrase_step_and_lists_reach_the_island_copy() {
        val old = """{"antibypass":{"phrase":true,"length":90,"complex":true},"sleep":{"on":true,"guarded":true,"tiers":[{"from":"23:00","every":15}]}}"""
        val got = Store.decodeImport(old, Config())!!
        assertEquals(listOf(ChallengeStep("s0", "phrase", 90, 1)), got.antibypass.stepList)      // nothing changes until it is edited
        assertFalse(got.sleep.challenge)
        assertNull(got.sleep.tiers.single().challengeSteps)
        val c = Config(antibypass = AntiBypassCfg(phrase = true, steps = listOf(ChallengeStep("a", "math", 2, 5), ChallengeStep("b", "memory", 1, 2))),
            sleep = SleepCfg(on = true, challenge = true, challengeGrid = true, challengeSteps = listOf(ChallengeStep("g", "sort", 0, 1)),
                tiers = listOf(ReminderTier("23:00", everySec = 900, challengeSteps = listOf(ChallengeStep("t", "math", 0, 2))))))
        val imported = Store.decodeImport(json.encodeToString(c), Config())!!
        assertEquals(c.antibypass, imported.antibypass); assertEquals(c.sleep, imported.sleep)
        // the Island helper gets main's config as JSON and applies it as it is
        val copy = LinkProtocol.json.decodeFromString<Config>(LinkProtocol.json.encodeToString(c))
        val applied = applyConfig(copy, Config())
        assertEquals(c.antibypass.steps, applied.antibypass.steps); assertEquals(c.sleep, applied.sleep)
    }

    @kotlinx.serialization.Serializable
    private data class OldGroup(val id: String, val name: String, val memberIds: List<String> = emptyList())
}
