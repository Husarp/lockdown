package com.husarp.lockdown.data

import android.content.Context
import com.husarp.lockdown.engine.Active
import com.husarp.lockdown.engine.UsageCounter
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File
import java.time.LocalDateTime

/**
 * Persists the rule-engine usage counters (seconds and opens per owner/bucket) across restarts, so a limit
 * keeps counting even after a reboot or the service being killed. Writes are throttled - the counters live in
 * memory and are flushed at most every [FLUSH_SEC] seconds.
 */
object UsageStore {
    private lateinit var file: File
    private val json = Json { ignoreUnknownKeys = true }
    private var lastFlush = 0L

    lateinit var counter: UsageCounter
        private set

    fun init(ctx: Context) {
        file = File(ctx.filesDir, "usage.json")
        val map: MutableMap<String, Int> =
            if (file.exists()) runCatching { json.decodeFromString<Map<String, Int>>(file.readText()).toMutableMap() }
                .getOrDefault(HashMap())
            else HashMap()
        counter = UsageCounter(map)
    }

    /** Record a foreground tick and flush if due. */
    fun record(actives: List<Active>, now: LocalDateTime = LocalDateTime.now()) {
        counter.record(actives, now)
        maybeFlush()
    }

    fun prune(now: LocalDateTime = LocalDateTime.now()) {
        counter.prune(now)
        flush()
    }

    private fun maybeFlush() {
        val now = System.currentTimeMillis()
        if (now - lastFlush >= FLUSH_SEC * 1000L) flush()
    }

    fun flush() {
        lastFlush = System.currentTimeMillis()
        runCatching { file.writeText(json.encodeToString(counter.counters)) }
    }

    private const val FLUSH_SEC = 20L
}
