package com.husarp.lockdown.data

import android.content.Context
import com.husarp.lockdown.engine.Mode
import com.husarp.lockdown.engine.ModeActive
import com.husarp.lockdown.engine.ModeState
import com.husarp.lockdown.engine.Modes
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File
import java.time.LocalDateTime

/** Holds the mode list (built-ins + custom) and which one is running, and persists both. */
object ModesStore {
    private lateinit var listFile: File
    private lateinit var activeFile: File
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    private val _modes = MutableStateFlow(Modes.DEFAULT_MODES)
    val modes: StateFlow<List<Mode>> = _modes
    private val _active = MutableStateFlow<ModeActive?>(null)
    val active: StateFlow<ModeActive?> = _active

    fun init(ctx: Context) {
        listFile = File(ctx.filesDir, "modes.json")
        activeFile = File(ctx.filesDir, "mode_active.json")
        val saved = if (listFile.exists())
            runCatching { json.decodeFromString<List<Mode>>(listFile.readText()) }.getOrDefault(emptyList())
        else emptyList()
        _modes.value = Modes.merge(saved)
        if (activeFile.exists())
            _active.value = runCatching { json.decodeFromString<ModeActive?>(activeFile.readText()) }.getOrNull()
    }

    fun saveModes(list: List<Mode>) {
        _modes.value = Modes.merge(list)
        runCatching { listFile.writeText(json.encodeToString(_modes.value)) }
    }

    /** Start a mode by hand. [until] null = until stopped; [locked] only holds when there's an end. */
    fun start(id: String, until: LocalDateTime?, locked: Boolean = false, now: LocalDateTime = LocalDateTime.now()) {
        _active.value = ModeActive(id, now.toString(), until?.toString(), locked && until != null)
        persistActive()
    }

    /** Stop the hand-started mode. Returns false (and does nothing) if it's locked and [force] is false. */
    fun stop(force: Boolean = false, now: LocalDateTime = LocalDateTime.now()): Boolean {
        if (!Modes.canStop(current(now), force)) return false
        _active.value = null
        persistActive()
        return true
    }

    fun current(now: LocalDateTime = LocalDateTime.now()): ModeState? = Modes.active(now, _modes.value, _active.value)

    private fun persistActive() = runCatching {
        activeFile.writeText(json.encodeToString(_active.value))
    }
}
