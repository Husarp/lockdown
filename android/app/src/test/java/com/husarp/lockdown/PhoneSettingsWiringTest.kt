package com.husarp.lockdown

import java.io.File
import org.junit.Assert.*
import org.junit.Test

/** Wiring checks for callbacks that cannot be exercised by the plain JVM Android stubs. */
class PhoneSettingsWiringTest {
    private fun source(path: String) = File("src/main/java/com/husarp/lockdown/$path").readText()
    @Test fun protection_exception_save_reloads_filter() {
        val save = source("screens/BlockEditors.kt").substringAfter("val old = Store.config.protection").substringBefore("d.clear()")
        assertTrue(save.contains("if (new != old) Protection.load(ctx)"))
    }
    @Test fun vpn_reads_independent_switches() {
        assertTrue(source("vpn/LockdownVpn.kt").contains("Dns.safeHost(host, cfg)"))
        val dns = source("vpn/Dns.kt")
        assertTrue(dns.contains("cfg.keywords.safeSearch")); assertTrue(dns.contains("cfg.keywords.youtube"))
    }
    @Test fun corrupt_config_is_preserved_and_errors_are_observable() {
        val store = source("data/Store.kt")
        assertTrue(store.contains("config-unreadable-")); assertTrue(store.contains("val error: StateFlow<String?>"))
    }
    @Test fun backup_includes_modes_and_import_restores_them() {
        val store = source("data/Store.kt")
        assertTrue(store.contains("ModesStore.modes.value")); assertTrue(store.contains("ModesStore.stageModes"))
    }
    @Test fun export_reports_write_failure() {
        val callback = source("screens/SettingsScreen.kt").substringAfter("val exportCfg =").substringBefore("val importCfg =")
        assertTrue(callback.contains("Couldn't export settings"))
    }
    @Test fun master_off_disables_safe_answers() {
        assertTrue(source("vpn/Dns.kt").contains("if (!cfg.enabled"))
    }
}
