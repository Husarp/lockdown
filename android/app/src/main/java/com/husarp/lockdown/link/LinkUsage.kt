package com.husarp.lockdown.link

import com.husarp.lockdown.engine.LimitClock
import com.husarp.lockdown.engine.QuotaContribution
import com.husarp.lockdown.engine.QuotaExemptions
import com.husarp.lockdown.engine.UsageCounter
import java.time.Duration
import java.time.LocalDateTime

/**
 * Shared limits across the two profiles. The helper runs the same config, so its counters use the same
 * (owner, bucket) keys as main's. It decides on base (main's last snapshot) + the batch in flight + its own
 * pending time; main adds each batch once (by seq) and sends back its counters for the running periods.
 * An authenticated batch and its counted-through sequence are persisted together on receipt.
 */
object LinkUsage {
    /** Main's counters that still matter now: this day / week / month, today's switches, opening counts of the
     *  running periods and allowance windows that haven't ended. */
    fun current(counters: Map<String, Int>, now: LocalDateTime, clock: LimitClock): Map<String, Int> {
        val day = clock.period("day", now).first
        val week = clock.period("week", now).first
        val month = clock.period("month", now).first
        val date = now.toLocalDate().toString()
        val keep = setOf("day:$day", "day:$date", "week:$week", "month:$month", "sw:$date")
        return counters.filter { (k, _) ->
            val b = k.substringAfter('\u0000')
            when {
                b in keep -> true
                // endsWith: a custom reset's day key has a ':' in it ("2026-09-28T03:00")
                b.startsWith("op:") -> b.endsWith(":$day") || b.endsWith(":$week") || b.endsWith(":$month")
                b.startsWith("win:") -> runCatching { LocalDateTime.parse(b.removePrefix("win:").substringAfter(':')).isAfter(now) }.getOrDefault(false)
                else -> false
            }
        }
    }

    /** What the helper adds to its own counter for (owner, bucket): main's snapshot and the batch in flight. */
    fun extra(base: Map<String, Int>, inflight: Batch?, owner: String, bucket: String): Int {
        val k = "$owner\u0000$bucket"
        return (base[k] ?: 0) + (inflight?.deltas?.get(k) ?: 0)
    }

    fun sum(vararg maps: Map<String, Int>?): HashMap<String, Int> {
        val out = HashMap<String, Int>()
        for (m in maps) m?.forEach { (k, v) -> out[k] = (out[k] ?: 0) + v }
        return out
    }

    /** With no batch out, moves [pending] into a new one. Returns (the batch out, the next seq). */
    fun formBatch(pending: MutableMap<String, Int>, inflight: Batch?, nextSeq: Long): Pair<Batch?, Long> {
        if (inflight != null || pending.isEmpty()) return inflight to nextSeq
        val b = Batch(nextSeq, HashMap(pending))
        pending.clear()
        return b to nextSeq + 1
    }

    /** Keep the oldest samples first and bound a signed frame, including when it follows a long outage. */
    const val BATCH_SAMPLES = 256

    /** Combine adjacent timed samples while retaining the contributors and both edges. */
    fun append(journal: MutableList<QuotaContribution>, entry: QuotaContribution) {
        val index = journal.indexOfLast { it.key == entry.key }
        val last = journal.getOrNull(index)
        if (last != null && last.end != null && last.end == entry.start && entry.end != null &&
            last.sources == entry.sources && Duration.between(LocalDateTime.parse(last.start),
                LocalDateTime.parse(entry.end)) <= Duration.ofHours(1)) {
            journal[index] = last.copy(end = entry.end)
        } else journal.add(entry)
    }

    /** Apply only payable samples, keeping the subsecond remainder for this stream and bucket. */
    fun tally(entries: List<QuotaContribution>, exemptions: QuotaExemptions,
              carry: MutableMap<String, Long> = HashMap()): Map<String, Int> {
        val deltas = HashMap<String, Int>()
        for (entry in entries) {
            if (entry.sources.isEmpty() || entry.key.length > 250 ||
                LinkProtocol.cleanDeltas(mapOf(entry.key to 1)).isEmpty()) continue
            for (paid in exemptions.clip(entry)) {
                val end = paid.end
                val amount = if (end == null) 1 else {
                    val duration = Duration.between(LocalDateTime.parse(paid.start), LocalDateTime.parse(end))
                    if (duration > Duration.ofHours(1)) continue
                    UsageCounter.wholeSeconds(carry, paid.key, duration.toNanos())
                }
                if (amount > 0) deltas[paid.key] = (deltas[paid.key] ?: 0) + amount
            }
        }
        return deltas
    }

    /** Main has every batch up to [ack]: the one out is done. */
    fun acked(inflight: Batch?, ack: Long): Batch? = if (inflight != null && ack >= inflight.seq) null else inflight

    /** A new pairing: the batch out goes back into pending (sent again under the new seq). */
    fun foldBack(pending: MutableMap<String, Int>, inflight: Batch?) {
        inflight?.deltas?.forEach { (k, v) -> pending[k] = (pending[k] ?: 0) + v }
    }
}
