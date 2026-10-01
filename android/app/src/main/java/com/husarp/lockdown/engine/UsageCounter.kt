package com.husarp.lockdown.engine

import java.time.Duration
import java.time.LocalDateTime

/** One item currently in the foreground, with its counted rules (item + groups). */
data class Active(val item: Item, val counted: List<EffRule>, val launched: Boolean = false)

/**
 * Records foreground time and opens into the (owner, bucket) counters the rules read - the phone's version of
 * the PC usage tracker (monitor/usage.py). Pure logic, no Android: feed it foreground snapshots with [record]
 * and it accrues time, and detects switches (SWITCH-mode limits) and new visits (VISIT-mode limits).
 *
 * [counters] maps "owner\u0000bucket" to seconds (time limits / allowance) or a count (opening limits).
 */
class UsageCounter(
    val counters: MutableMap<String, Int> = HashMap(),
    var clock: LimitClock = LimitClock.DEFAULT,
) {
    private var lastTick: LocalDateTime? = null
    private var front: Set<String> = emptySet()               // item ids in the foreground last tick (switches)
    private val lastSeen = HashMap<String, LocalDateTime>()   // item id -> last in use (site visit gaps)

    /** Reader for the rule engine. */
    val usage: Usage = { owner, bucket -> counters[key(owner, bucket)] ?: 0 }

    /**
     * A tick: [actives] are the items in the foreground right now (usually the app and, in a browser, its site).
     * Accrues the time since the previous tick and records any switches / new visits. Call with an empty list
     * when nothing tracked is in front (home screen, screen off) to close out the gap without counting it.
     */
    fun record(actives: List<Active>, now: LocalDateTime) {
        addTime(actives, now)
        val nowFront = actives.mapTo(HashSet()) { it.item.id }
        for (a in actives) {
            if (a.item.id !in front) addAll(Rules.switchTargets(a.item, a.counted, now, clock), 1)   // switched to it
            val away = lastSeen[a.item.id]?.let { Duration.between(it, now).seconds }
            addAll(Rules.visitTargets(a.item, a.counted, now, a.launched, away, clock), 1)            // launch / return
            lastSeen[a.item.id] = now
        }
        front = nowFront
    }

    private fun addTime(actives: List<Active>, now: LocalDateTime) {
        val last = lastTick
        lastTick = now
        if (last == null) return
        val elapsed = Duration.between(last, now).seconds
        if (elapsed < 1 || elapsed > MAX_TICK_SEC) return         // gap (asleep / service paused): don't count
        val sec = elapsed.toInt()
        for (a in actives) addAll(Rules.usageTargets(a.item, a.counted, now, clock), sec)
    }

    private fun addAll(targets: Set<Pair<String, String>>, amount: Int) {
        for ((owner, bucket) in targets) add(owner, bucket, amount)
    }

    private fun add(owner: String, bucket: String, amount: Int) {
        if (amount == 0) return
        val k = key(owner, bucket)
        counters[k] = (counters[k] ?: 0) + amount
    }

    private fun key(owner: String, bucket: String) = "$owner\u0000$bucket"

    /** Drop buckets that can no longer matter, so the store stays small. */
    fun prune(now: LocalDateTime) {
        val cutoff = now.toLocalDate().minusDays(45).toString()
        val it = counters.entries.iterator()
        while (it.hasNext()) {
            val bucket = it.next().key.substringAfter('\u0000')
            val stale = when {
                bucket.startsWith("day:") || bucket.startsWith("sw:") -> bucket.substringAfter(':') < cutoff
                bucket.startsWith("win:") -> runCatching {
                    LocalDateTime.parse(bucket.removePrefix("win:").substringAfter(':')).isBefore(now)
                }.getOrDefault(false)
                else -> false                                     // week/month/op buckets: keep (tiny, self-expire)
            }
            if (stale) it.remove()
        }
    }

    companion object {
        const val MAX_TICK_SEC = 120L
    }
}
