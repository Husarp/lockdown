package com.husarp.lockdown.data

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.updateAndGet
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.encodeToJsonElement
import com.husarp.lockdown.engine.Mode
import java.io.File
import java.nio.file.Files
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** The single source of truth: the Config JSON, loaded once and observed by the UI. */
object Store {
    private lateinit var file: File
    // Not pretty-printed: with thousands of imported items the config is multi-MB; compact JSON is ~3× smaller
    // and faster to serialize on each save. Still human-readable enough and loads the same.
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val _state = MutableStateFlow(Config())
    val state: StateFlow<Config> = _state

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error
    private var unreadableSource: File? = null
    fun clearError() { _error.value = null }

    fun init(ctx: Context) = init(File(ctx.filesDir, "config.json"))

    /** File overload also keeps recovery and I/O tests independent of Android. */
    internal fun init(configFile: File) {
        file = configFile
        unreadableSource = null
        _state.value = Config()
        _error.value = null
        if (!file.exists()) {
            file.parentFile?.listFiles()?.firstOrNull { it.name.startsWith("config-unreadable-") }?.let {
                _error.value = "Settings couldn't be read. A copy is kept as ${it.name}. Import a backup or set up again."
            }
            return
        }
        runCatching {
            val read = json.decodeFromString<Config>(file.readText())
            val c = ImportedSites.migrate(read)
            _state.value = c
            if (c !== read) persist()
        }.onFailure {
            unreadableSource = file
            val kept = runCatching { keepUnreadable()!! }.getOrElse { error ->
                _error.value = "Settings couldn't be read or kept aside: ${error.message}. Import a backup to recover."
                return
            }
            _error.value = "Settings couldn't be read. A copy is kept as $kept. Import a backup or set up again."

        }
    }

    // Saves run on one background thread (a toggle used to write the whole multi-MB config on the UI thread),
    // one after another, always the newest state, through a temp file so a crash mid-write can't corrupt it.
    private val writer = Executors.newSingleThreadExecutor()
    private val dirty = AtomicBoolean(false)
    private val diskLock = FileWrites.lock
    internal fun awaitWrites() { writer.submit {}.get(10, java.util.concurrent.TimeUnit.SECONDS) }

    /** Change the config and persist it. Atomic: the UI and the VPN thread can't overwrite each other's change. */
    fun update(block: (Config) -> Config) {
        synchronized(diskLock) { _state.updateAndGet(block); persist() }
    }

    private fun persist() {
        if (!dirty.compareAndSet(false, true)) return          // a save is already queued; it writes the newest state
        writer.execute {
            dirty.set(false)
            runCatching { synchronized(diskLock) { writeConfig(file, _state.value) } }
                .onFailure { _error.value = "Couldn't save settings: ${it.message ?: "write failed"}" }

        }
    }

    /** The whole config as JSON (for exporting / syncing to the other profile). */
    fun exportJson(): String = synchronized(diskLock) {
        encodeBackup(_state.value, ModesStore.modes.value)
    }

    internal fun encodeBackup(config: Config, modes: List<Mode>): String = buildJsonObject {
        (json.encodeToJsonElement(config) as JsonObject).forEach { (key, value) -> put(key, value) }
        put("modes", json.encodeToJsonElement(modes))
    }.toString()

    internal fun decodeModes(text: String): List<Mode>? =
        (json.parseToJsonElement(text) as JsonObject)["modes"]?.let { json.decodeFromJsonElement<List<Mode>>(it) }

    /** Move the unreadable live file aside once. If storage is unavailable, retry before the next write. */
    private fun keepUnreadable(): String? {
        val source = unreadableSource ?: return null
        val backup = File(source.parentFile, "config-unreadable-${System.currentTimeMillis()}-${java.util.UUID.randomUUID()}.json")
        Files.move(source.toPath(), backup.toPath()) // no REPLACE_EXISTING: a kept copy is never overwritten
        unreadableSource = null
        return backup.name
    }

    internal fun writeConfig(target: File, config: Config) {
        keepUnreadable()
        FileWrites.replace(listOf(target to json.encodeToString(config)))
    }

    /** A helper acknowledges only after the replacement reaches disk; failures keep the previous live rules. */
    internal fun updatePersisted(block: (Config) -> Config): Boolean = synchronized(diskLock) {
        runCatching {
            val updated = block(_state.value)
            writeConfig(file, updated)
            _state.value = updated
            _error.value = null
            true
        }.getOrElse {
            _error.value = "Couldn't save settings: ${it.message ?: "write failed"}"
            false
        }
    }

    /** Replace config and modes together. Publish neither until both have been persisted. */
    fun importJson(text: String): Boolean = synchronized(diskLock) {
        val imported = decodeImport(text, _state.value) ?: return false
        val modes = runCatching { decodeModes(text) }.getOrElse { return false }
        runCatching {
            keepUnreadable()
            val modeWrite = modes?.let { ModesStore.stageModes(it) }
            FileWrites.replace(listOfNotNull(modeWrite, file to json.encodeToString(imported)))
            if (modes != null) ModesStore.publishModes(modes)
            _state.value = imported
            _error.value = null
            true
        }.getOrElse {
            _error.value = "Couldn't save imported settings: ${it.message ?: "write failed"}"
            false
        }
    }

    /**
     * Parse an imported config, or null if it can't be read. The current challenge unlock window is kept: a file
     * can't bring its own (e.g. one ending years from now) and so skip every later challenge. A running "Pause my
     * blocks" is kept too (as on the PC): a file neither brings one in nor ends the one running. So is the emergency
     * use history (PC parity): a file brings in emergency kinds, but can't reset the uses or bring in an unlock.
     */
    fun decodeImport(text: String, current: Config): Config? =
        runCatching {
            decodeModes(text) // reject malformed mode data before changing either store
            ImportedSites.migrate(json.decodeFromString<Config>(text))
        }.getOrNull()?.let { c ->
            c.copy(antibypass = c.antibypass.copy(unlockedFrom = current.antibypass.unlockedFrom, unlockedUntil = current.antibypass.unlockedUntil),
                pause = current.pause, emergencyUses = current.emergencyUses, unlocks = current.unlocks,
                unlockUntil = current.unlockUntil, unlockItems = current.unlockItems, alertsPausedUntil = current.alertsPausedUntil)
        }

    val config get() = _state.value
}
