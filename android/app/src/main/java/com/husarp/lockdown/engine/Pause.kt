package com.husarp.lockdown.engine

import kotlinx.serialization.Serializable
import java.time.Duration
import java.time.LocalDateTime
import java.time.LocalTime

/** A running "Pause my blocks": from [started] to [until] (trusted local time, ISO); [silent] also silences alerts. */
@Serializable
data class BlockPause(val started: String, val until: String, val silent: Boolean = false)

/**
 * Pause my blocks, ported from the PC pause.py (0.84.8): every block from your own list (items, groups, modes,
 * categories - hours, time limits, opening limits, temporary and permanent blocks) stops for a while you choose,
 * then comes back by itself. Never lifted: the protection lists, the blocked words, SafeSearch / YouTube Restricted.
 * Time used meanwhile still counts toward the limits. Starting it is loosening (the challenge); ending it is free.
 *
 * It counts only from `started` to `until`: a clock that reads earlier than when it began does not stretch it, and
 * a pause can never run longer than [MAX_SPAN], whatever is written. It lives in the config, so a linked Island
 * helper gets it with the rules and pauses too. Pure.
 */
object Pause {
    const val REST_OF_DAY = "Rest of the day"
    /** label -> minutes (null: until the next reset time). */
    val DURATIONS: Map<String, Int?> = linkedMapOf("30 min" to 30, "1 h" to 60, "2 h" to 120, "4 h" to 240, REST_OF_DAY to null)
    val MAX_SPAN: Duration = Duration.ofDays(1)

    private fun at(v: String) = runCatching { LocalDateTime.parse(v) }.getOrNull()

    /** [p] while it is running at [now], else null. */
    fun state(p: BlockPause?, now: LocalDateTime): BlockPause? {
        p ?: return null
        val started = at(p.started) ?: return null
        val until = at(p.until) ?: return null
        val running = !now.isBefore(started) && now.isBefore(until) && Duration.between(started, until) <= MAX_SPAN
        return if (running) p else null
    }

    fun until(p: BlockPause?, now: LocalDateTime): LocalDateTime? = state(p, now)?.let { at(it.until) }

    /** While a pause that silences the alerts is running: its end, else null. */
    fun silentUntil(p: BlockPause?, now: LocalDateTime): LocalDateTime? = state(p, now)?.takeIf { it.silent }?.let { at(it.until) }

    /** When a pause started at [now] ends: after [minutes], or (null) at the next [reset] time - always within a day.
     *  Not the end of the running limit day: a changed reset time can stretch that up to two days. */
    fun endFor(minutes: Int?, now: LocalDateTime, reset: LocalTime): LocalDateTime {
        if (minutes == null) {
            val r = LocalDateTime.of(now.toLocalDate(), reset)
            return if (r.isAfter(now)) r else r.plusDays(1)
        }
        return now.plusMinutes(minutes.toLong())
    }

    /** A pause started at [now] (the caller has had the challenge passed). Replaces one already running. */
    fun start(now: LocalDateTime, minutes: Int?, silent: Boolean, reset: LocalTime): BlockPause =
        BlockPause(now.toString(), endFor(minutes, now, reset).toString(), silent)

    fun label(minutes: Int?): String = DURATIONS.entries.first { it.value == minutes }.key
}
