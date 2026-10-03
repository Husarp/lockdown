package com.husarp.lockdown.engine

import java.time.Duration
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.YearMonth

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

    /** Drop buckets that can no longer matter, so the store stays small: days, weeks, months and opening counts
     *  whose period ended more than [KEEP_DAYS] days ago, and allowance windows that have ended. */
    fun prune(now: LocalDateTime) {
        val cutoff = now.toLocalDate().minusDays(KEEP_DAYS)
        val it = counters.entries.iterator()
        while (it.hasNext()) {
            val bucket = it.next().key.substringAfter('\u0000')
            val kind = bucket.substringBefore(':')
            val stale = when (kind) {
                "day", "week", "month", "sw" -> periodEnd(bucket.substringAfter(':'))?.isBefore(cutoff) ?: false
                "op" -> periodEnd(bucket.removePrefix("op:").substringAfter(':'))?.isBefore(cutoff) ?: false
                "win" -> runCatching {
                    LocalDateTime.parse(bucket.removePrefix("win:").substringAfter(':')).isBefore(now)
                }.getOrDefault(false)
                else -> false
            }
            if (stale) it.remove()
        }
    }

    /** When a period key ends: "2026-09-28" / "2026-09-28T03:00" (a day), "w2026-09-28" (a week), "m2026-09". */
    private fun periodEnd(key: String): LocalDate? = runCatching {
        when {
            key.startsWith("w") -> LocalDate.parse(key.substring(1, 11)).plusDays(8)
            key.startsWith("m") -> YearMonth.parse(key.substring(1, 8)).plusMonths(1).atDay(2)
            else -> LocalDate.parse(key.substring(0, 10)).plusDays(2)    // a limit day may run into the next
        }
    }.getOrNull()

    companion object {
        const val MAX_TICK_SEC = 120L
        const val KEEP_DAYS = 45L
    }
}
