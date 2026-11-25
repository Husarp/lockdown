package com.husarp.lockdown.engine

import kotlinx.serialization.Serializable
import java.time.Duration
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.YearMonth

/** One item currently in the foreground, with its counted rules (item + groups). */
data class Active(val item: Item, val counted: List<EffRule>, val launched: Boolean = false,
                  val emergencyEligible: Boolean = true)

/** One sampled counter contribution. A null end denotes a discrete opening at [start]. */
@Serializable
data class QuotaContribution(val key: String, val sources: List<String>, val start: String, val end: String? = null)

/** Each source can feed a shared bucket independently. An empty source ID has no item emergency. */
class QuotaExemptions(pauses: List<BlockPause> = emptyList(), uses: List<EmergencyUse> = emptyList()) {
    private val global = pauses.mapNotNull(Pause::interval)
    private val byItem = uses.flatMap { use ->
        val start = parse(use.at)
        val end = parse(use.until)
        if (start == null || end == null || end <= start || end > start.plusMinutes(Emergency.MAX_MINUTES.toLong()))
            emptyList()
        else use.items.map { it to (start to end) }
    }.groupBy({ it.first }, { it.second })

    private fun List<Pair<LocalDateTime, LocalDateTime>>.covers(at: LocalDateTime) =
        any { (start, end) -> at >= start && at < end }

    fun payable(sources: List<String>, at: LocalDateTime): Boolean =
        !global.covers(at) && sources.any { !byItem[it].orEmpty().covers(at) }

    fun boundaries(sources: List<String>, start: LocalDateTime, end: LocalDateTime): List<LocalDateTime> =
        (global + sources.flatMap { byItem[it].orEmpty() })
            .flatMap { listOf(it.first, it.second) }.filter { it > start && it < end }

    fun clip(entry: QuotaContribution): List<QuotaContribution> {
        val start = parse(entry.start) ?: return emptyList()
        if (entry.end == null) return if (payable(entry.sources, start)) listOf(entry) else emptyList()
        val end = parse(entry.end) ?: return emptyList()
        if (end <= start) return emptyList()
        val points = (listOf(start, end) + boundaries(entry.sources, start, end)).distinct().sorted()
        return points.zipWithNext().mapNotNull { (a, b) ->
            if (payable(entry.sources, a)) entry.copy(start = a.toString(), end = b.toString()) else null
        }
    }

    companion object {
        internal fun parse(value: String): LocalDateTime? = runCatching { LocalDateTime.parse(value) }.getOrNull()
    }
}

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
    internal val carry: MutableMap<String, Long> = HashMap(),
) {
    /** Goes up each time a counter changes (time or an opening counted, a bucket pruned, [touch]): the store writes
     *  the file only when this moved since its last write - an idle phone used to rewrite it unchanged every 20 s. */
    var changes = 0L
        private set

    /** The counters were changed from outside (the Island helper's time added). */
    fun touch() { changes++ }

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
    fun record(actives: List<Active>, now: LocalDateTime, exemptions: QuotaExemptions = QuotaExemptions(),
               resetEdges: List<LocalDateTime> = emptyList(),
               countable: (Item, LocalDateTime) -> Boolean = { _, _ -> true },
               journal: ((QuotaContribution) -> Unit)? = null) {
        val start = lastTick
        lastTick = now
        fun source(a: Active) = if (a.emergencyEligible) a.item.id else ""
        fun emit(targets: Map<Pair<String, String>, Set<String>>, at: LocalDateTime, end: LocalDateTime? = null) {
            for ((target, ids) in targets) {
                val bucket = key(target.first, target.second)
                if (journal != null) journal(QuotaContribution(bucket, ids.sorted(), at.toString(), end?.toString()))
                else {
                    val amount = if (end == null) 1 else wholeSeconds(carry, bucket, Duration.between(at, end).toNanos())
                    if (amount > 0) {
                        counters[bucket] = (counters[bucket] ?: 0) + amount
                        changes++
                    }
                }
            }
        }
        if (start != null && now > start && Duration.between(start, now) <= Duration.ofSeconds(MAX_TICK_SEC)) {
            val edges = (Rules.usageEdges(start, now, clock, resetEdges) +
                exemptions.boundaries(actives.map(::source), start, now)).distinct().sorted()
            for ((lo, hi) in edges.zipWithNext()) {
                val targets = HashMap<Pair<String, String>, MutableSet<String>>()
                for (a in actives) {
                    val id = source(a)
                    if (!exemptions.payable(listOf(id), lo) || !countable(a.item, lo)) continue
                    for (target in Rules.usageTargets(a.item, a.counted, lo, clock))
                        targets.getOrPut(target) { HashSet() }.add(id)
                }
                emit(targets, lo, hi)
            }
        }
        val seen = HashSet<String>()
        for (a in actives) {
            val id = source(a)
            val payable = exemptions.payable(listOf(id), now)
            if (payable && !countable(a.item, now)) continue
            if (payable) {
                if (a.item.id !in front) {
                    emit(Rules.switchTargets(a.item, a.counted, now, clock).associateWith { setOf(id) }, now)
                }
                val away = lastSeen[a.item.id]?.let { Duration.between(it, now).seconds }
                emit(Rules.visitTargets(a.item, a.counted, now, a.launched, away, clock).associateWith { setOf(id) }, now)
            }
            seen.add(a.item.id)
            lastSeen[a.item.id] = now
        }
        front = seen
    }

    private fun key(owner: String, bucket: String) = "$owner\u0000$bucket"

    /** Drop buckets that can no longer matter, so the store stays small: days, weeks, months and opening counts
     *  whose period ended more than [KEEP_DAYS] days ago, and allowance windows that have ended. */
    fun prune(now: LocalDateTime) {
        val cutoff = now.toLocalDate().minusDays(KEEP_DAYS)
        fun stale(key: String): Boolean {
            val bucket = key.substringAfter('\u0000')
            val kind = bucket.substringBefore(':')
            return when (kind) {
                "day", "week", "month", "sw" -> periodEnd(bucket.substringAfter(':'))?.isBefore(cutoff) ?: false
                "op" -> periodEnd(bucket.removePrefix("op:").substringAfter(':'))?.isBefore(cutoff) ?: false
                "win" -> runCatching {
                    LocalDateTime.parse(bucket.removePrefix("win:").substringAfter(':')).isBefore(now)
                }.getOrDefault(false)
                else -> false
            }
        }
        if (counters.keys.removeAll(::stale) or carry.keys.removeAll(::stale)) changes++
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
        internal fun wholeSeconds(carry: MutableMap<String, Long>, key: String, nanos: Long): Int {
            val total = (carry[key] ?: 0) + nanos
            val remainder = total % 1_000_000_000
            if (remainder > 0) carry[key] = remainder else carry.remove(key)
            return (total / 1_000_000_000).toInt()
        }

        const val MAX_TICK_SEC = 120L
        const val KEEP_DAYS = 45L
    }
}
