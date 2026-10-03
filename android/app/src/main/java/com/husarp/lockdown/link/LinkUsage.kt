package com.husarp.lockdown.link

import com.husarp.lockdown.engine.LimitClock
import java.time.LocalDateTime

/**
 * Shared limits across the two profiles. The helper runs the same config, so its counters use the same
 * (owner, bucket) keys as main's. It decides on base (main's last snapshot) + the batch in flight + its own
 * pending time; main adds each batch once (by seq) and sends back its counters for the running periods.
 * Crash windows are ordered to count twice rather than lose time.
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

    /** Main has every batch up to [ack]: the one out is done. */
    fun acked(inflight: Batch?, ack: Long): Batch? = if (inflight != null && ack >= inflight.seq) null else inflight

    /** A new pairing: the batch out goes back into pending (sent again under the new seq). */
    fun foldBack(pending: MutableMap<String, Int>, inflight: Batch?) {
        inflight?.deltas?.forEach { (k, v) -> pending[k] = (pending[k] ?: 0) + v }
    }
}
