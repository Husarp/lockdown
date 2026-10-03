package com.husarp.lockdown

import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.AntiBypassCfg
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
}
