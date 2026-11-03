package com.husarp.lockdown

import com.husarp.lockdown.data.*
import com.husarp.lockdown.engine.*
import com.husarp.lockdown.vpn.Dns
import java.io.File
import org.junit.Assert.*
import org.junit.Test

class PhoneSettingsTest {
    private inline fun files(block: (File) -> Unit) {
        val dir = kotlin.io.path.createTempDirectory("phone-settings").toFile()
        try { block(dir) } finally { Store.awaitWrites(); dir.deleteRecursively() }
    }

    @Test fun dns_switches_are_independent_of_each_other_and_word_matching() {
        val cfg = Config(keywords = KeywordsCfg(enabled = false, safeSearch = false, youtube = true))
        assertNull(Dns.safeHost("www.google.com", cfg))
        assertEquals("restrictmoderate.youtube.com", Dns.safeHost("m.youtube.com", cfg))
        val searchOnly = cfg.copy(keywords = cfg.keywords.copy(safeSearch = true, youtube = false))
        assertEquals("forcesafesearch.google.com", Dns.safeHost("www.google.com", searchOnly))
        assertNull(Dns.safeHost("www.youtube.com", searchOnly))
    }

    @Test fun master_off_and_filter_safe_switch_off_disable_both_restrictions() {
        for (host in listOf("google.com", "www.bing.com", "duckduckgo.com", "www.youtube-nocookie.com")) {
            assertNotNull(Dns.safeHost(host, Config()))
            assertNull(Dns.safeHost(host, Config(enabled = false)))
            assertNull(Dns.safeHost(host, Config(settings = Settings(forceSafeSearch = false))))
        }
    }

    @Test fun unreadable_copy_survives_setup_and_valid_import() = files { dir ->
        val f = File(dir, "config.json").apply { writeText("broken settings") }
        Store.init(f)
        assertNotNull(Store.error.value)
        val backup = dir.listFiles()!!.single { it.name.startsWith("config-unreadable-") }
        Store.update { it.copy(enabled = false) }; Store.awaitWrites()
        Store.init(f)
        assertFalse(Store.config.enabled)
        assertFalse(Store.importJson("broken again"))
        assertTrue(Store.importJson("{\"enabled\":false}"))
        assertNull(Store.error.value)
        assertFalse(Store.config.enabled)
        assertEquals("broken settings", backup.readText())
    }

    @Test fun background_save_failure_is_visible_and_old_file_survives() = files { dir ->
        val f = File(dir, "config.json").apply { writeText("{}") }
        Store.init(f)
        File(dir, "config.json.tmp").mkdir() // deterministic write failure, even when tests run as root
        Store.update { it.copy(enabled = false) }; Store.awaitWrites()
        assertTrue(Store.error.value!!.contains("Couldn't save settings"))
        assertEquals("{}", f.readText())
    }

    @Test fun all_modes_including_preset_edits_and_schedules_round_trip_and_legacy_keeps_them() = files { dir ->
        Store.init(File(dir, "config.json")); ModesStore.init(dir)
        val modes = Modes.merge(listOf(
            Modes.DEFAULT_MODES.first().copy(name = "Edited work", schedule = Schedule()),
            Mode("custom1", "My mode", schedule = Schedule(), items = listOf("app1"))))
        assertTrue(ModesStore.saveModes(modes))
        val backup = Store.exportJson()
        assertTrue(ModesStore.saveModes(emptyList()))
        assertTrue(Store.importJson(backup))
        assertEquals(modes, ModesStore.modes.value)
        ModesStore.init(dir)
        assertEquals(modes, ModesStore.modes.value)
        assertTrue(Store.importJson("{}"))
        assertEquals(modes, ModesStore.modes.value)
        assertNull(Store.decodeImport("{\"modes\":\"bad\"}", Config()))
    }

    @Test fun backup_cannot_supply_a_challenge_unlock() {
        val backup = Store.encodeBackup(Config(antibypass = AntiBypassCfg(unlockedUntil = "2099-01-01T00:00")), emptyList())
        assertNull(Store.decodeImport(backup, Config())!!.antibypass.unlockedUntil)
    }
}
