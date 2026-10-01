package com.husarp.lockdown.data

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File

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

    /** Change the config and persist it. */
    fun update(block: (Config) -> Config) {
        _state.value = block(_state.value)
        persist()
    }

    private fun persist() = runCatching { file.writeText(json.encodeToString(_state.value)) }

    /** The whole config as JSON (for exporting / syncing to the other profile). */
    fun exportJson(): String = json.encodeToString(_state.value)

    /** Replace the whole config from exported JSON. Returns false if it can't be parsed. */
    fun importJson(text: String): Boolean =
        runCatching { _state.value = json.decodeFromString<Config>(text); persist(); true }.getOrDefault(false)

    val config get() = _state.value
}
