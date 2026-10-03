package com.husarp.lockdown.data

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.updateAndGet
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File
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

    fun init(ctx: Context) {
        file = File(ctx.filesDir, "config.json")
        if (file.exists()) runCatching { _state.value = json.decodeFromString<Config>(file.readText()) }
    }

    // Saves run on one background thread (a toggle used to write the whole multi-MB config on the UI thread),
    // one after another, always the newest state, through a temp file so a crash mid-write can't corrupt it.
    private val writer = Executors.newSingleThreadExecutor()
    private val dirty = AtomicBoolean(false)

    /** Change the config and persist it. Atomic: the UI and the VPN thread can't overwrite each other's change. */
    fun update(block: (Config) -> Config) {
        _state.updateAndGet(block)
        persist()
    }

    private fun persist() {
        if (!dirty.compareAndSet(false, true)) return          // a save is already queued; it writes the newest state
        writer.execute {
            dirty.set(false)
            runCatching {
                val tmp = File(file.path + ".tmp")
                tmp.writeText(json.encodeToString(_state.value))
                if (!tmp.renameTo(file)) file.writeText(tmp.readText())
            }
        }
    }

    /** The whole config as JSON (for exporting / syncing to the other profile). */
    fun exportJson(): String = json.encodeToString(_state.value)

    /** Replace the whole config from exported JSON. Returns false if it can't be parsed. */
    fun importJson(text: String): Boolean =
        runCatching { _state.value = json.decodeFromString<Config>(text); persist(); true }.getOrDefault(false)

    val config get() = _state.value
}
