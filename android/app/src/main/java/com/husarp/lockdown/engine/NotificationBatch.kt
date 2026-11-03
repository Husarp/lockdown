package com.husarp.lockdown.engine

/** All detail in one ten-second burst, identical messages at most once in ten minutes.
 * The caller uses a stable notification id and monotonic milliseconds. Changed warnings still pass. */
class NotificationBatch {
    private val seen = LinkedHashMap<String, Long>()
    private val lines = LinkedHashSet<String>()
    private var last: Long? = null

    fun merge(messages: List<String>, now: Long): String? {
        seen.entries.removeIf { now - it.value >= 600_000 }
        val fresh = messages.flatMap { it.lines() }.map { it.trim() }.filter { it.isNotEmpty() && it !in seen }.distinct()
        if (fresh.isEmpty()) return null
        if (last == null || now - last!! > 10_000) { lines.clear(); last = now }
        for (text in fresh) { seen[text] = now; lines.add(text) }
        return lines.joinToString("\n")
    }
}
