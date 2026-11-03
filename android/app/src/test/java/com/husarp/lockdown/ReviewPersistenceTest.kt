package com.husarp.lockdown

import com.husarp.lockdown.data.*
import com.husarp.lockdown.engine.*
import com.husarp.lockdown.link.persistHelperConfig
import java.io.File
import org.junit.Assert.*
import org.junit.Test

class ReviewPersistenceTest {
    private inline fun files(block: (File) -> Unit) {
        val dir = kotlin.io.path.createTempDirectory("review-persistence").toFile()
        try { Store.awaitWrites(); block(dir) } finally { Store.awaitWrites(); dir.deleteRecursively() }
    }

    @Test fun unreadable_settings_are_kept_once_and_setup_can_be_saved() = files { dir ->
        val file = File(dir, "config.json").apply { writeText("unreadable original") }
        Store.init(file)
        val kept = dir.listFiles()!!.single { it.name.startsWith("config-unreadable-") }
        assertEquals("unreadable original", kept.readText())
        assertTrue(Store.error.value!!.contains("set up again"))
        Store.init(file) // restarting before the first save must not make another copy
        Store.update { it.copy(enabled = false) }; Store.awaitWrites()
        assertFalse(Store.config.enabled)
        Store.init(file)
        assertFalse(Store.config.enabled)
        assertEquals(listOf(kept), dir.listFiles()!!.filter { it.name.startsWith("config-unreadable-") })
        assertEquals("unreadable original", kept.readText())
    }

    @Test fun helper_only_acknowledges_a_config_that_reached_disk_and_retries_write_failure() = files { dir ->
        val file = File(dir, "config.json").apply { writeText("broken") }
        Store.init(file)
        val blocker = File(dir, "config.json.tmp").apply { mkdir() }
        val incoming = Config(enabled = false)
        assertFalse(persistHelperConfig(incoming))
        assertTrue(Store.config.enabled)
        blocker.delete()
        assertTrue(persistHelperConfig(incoming))
        Store.init(file)
        assertFalse(Store.config.enabled)
    }

    @Test fun failed_modes_sync_keeps_previous_modes_and_active_and_can_retry() = files { dir ->
        ModesStore.init(dir)
        ModesStore.start("work", null)
        val oldModes = ModesStore.modes.value
        val oldActive = ModesStore.active.value
        val text = ModesStore.exportJson(listOf(Mode("custom1", "Incoming")), ModeActive("custom1", "2026-11-03T10:00"))
        val blocked = File(dir, "modes.json.tmp").apply { mkdir() }
        assertFalse(ModesStore.replace(text))
        assertEquals(oldModes, ModesStore.modes.value)
        assertEquals(oldActive, ModesStore.active.value)
        ModesStore.init(dir)
        assertEquals(oldActive, ModesStore.active.value)
        blocked.delete()
        assertTrue(ModesStore.replace(text))
        assertEquals("custom1", ModesStore.active.value!!.id)
    }

    @Test fun failed_active_sync_keeps_both_files_and_live_state() = files { dir ->
        ModesStore.init(dir)
        ModesStore.start("work", null)
        val modes = ModesStore.modes.value
        val active = ModesStore.active.value
        File(dir, "mode_active.json.tmp").mkdir()
        assertFalse(ModesStore.replace(ModesStore.exportJson(listOf(Mode("custom1", "Incoming")), null)))
        assertEquals(modes, ModesStore.modes.value)
        assertEquals(active, ModesStore.active.value)
        ModesStore.init(dir)
        assertEquals(modes, ModesStore.modes.value)
        assertEquals(active, ModesStore.active.value)
    }

    @Test fun failed_config_import_keeps_modes_and_config_on_disk_and_in_memory() = files { dir ->
        val file = File(dir, "config.json").apply { writeText("{}") }
        Store.init(file); ModesStore.init(dir)
        assertTrue(ModesStore.saveModes(listOf(Mode("old", "Keep"))))
        val oldModes = ModesStore.modes.value
        val oldConfig = Store.config
        File(dir, "config.json.tmp").mkdir()
        assertFalse(Store.importJson(Store.encodeBackup(Config(enabled = false), listOf(Mode("new", "Replace")))))
        assertEquals(oldModes, ModesStore.modes.value)
        assertEquals(oldConfig, Store.config)
        ModesStore.init(dir)
        assertEquals(oldModes, ModesStore.modes.value)
        assertEquals("{}", file.readText())
    }

    @Test fun failed_second_commit_rolls_back_the_first_file() = files { dir ->
        val a = File(dir, "a").apply { writeText("old a") }
        val b = File(dir, "b").apply { writeText("old b") }
        val result = runCatching { FileWrites.replace(listOf(a to "new a", b to "new b")) { src, dst ->
            if (src.name == "b.tmp") false else src.renameTo(dst)
        } }
        assertTrue(result.isFailure)
        assertEquals("old a", a.readText())
        assertEquals("old b", b.readText())
    }
}
