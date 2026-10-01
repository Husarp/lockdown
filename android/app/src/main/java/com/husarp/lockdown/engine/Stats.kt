package com.husarp.lockdown.engine

import java.time.Duration
import java.time.LocalDate
import java.time.LocalDateTime

/**
 * Screen-time numbers for the Insights screen, ported from the PC stats.py. Pure computation over a per-minute
 * activity log and a switch log, so it's fully testable; the service collects the rows on-device.
 *
 * Definitions - active time: minutes in front with input in the last 5 min (idle = in front, no input);
 * session: time without a 5+ minute break; longest focus: the longest run in one app without switching away;
 * visit: from switching to something until switching away (short visit = under 30 s).
 */

/** One minute of activity: the app/site most in front that minute, seconds present and seconds active. */
data class MinuteRow(val minute: LocalDateTime, val app: String, val site: String, val seconds: Int, val active: Int)

/** A switch to a new foreground app/site. */
data class SwitchEvent(val ts: LocalDateTime, val app: String, val site: String)

object Stats {
    const val SESSION_GAP_MIN = 5L
    const val SHORT_VISIT_SEC = 30
    const val DEFAULT_VISIT_SEC = 5 * 60

    // ---------- categories ----------

    /** Saved choice ("app:<pkg>" / "site:<host>"); else blocked things are distracting, everything else neutral. */
    fun categoryOf(kind: String, name: String, categories: Map<String, String>, items: List<Item>): String {
        categories["$kind:$name"]?.let { return it }
        for (item in items) {
            if (kind == "app" && item.type == ItemType.APP && item.target.lowercase() == name) return "distracting"
            if (kind == "site" && item.type == ItemType.SITE &&
                item.target.lowercase().split(" ").any { name == it || name.endsWith(".$it") }) return "distracting"
        }
        return "neutral"
    }

    // ---------- totals ----------

    /** (active seconds, seconds present). */
    fun totals(rows: List<MinuteRow>): Pair<Int, Int> = rows.sumOf { it.active } to rows.sumOf { it.seconds }

    fun perApp(rows: List<MinuteRow>): Map<String, Int> =
        rows.groupBy { it.app }.mapValues { e -> e.value.sumOf { it.active } }

    fun perSite(rows: List<MinuteRow>): Map<String, Int> =
        rows.filter { it.site.isNotEmpty() }.groupBy { it.site }.mapValues { e -> e.value.sumOf { it.active } }

    fun perDay(rows: List<MinuteRow>): Map<LocalDate, Int> =
        rows.groupBy { it.minute.toLocalDate() }.mapValues { e -> e.value.sumOf { it.active } }

    private data class MinAgg(var seconds: Int = 0, var active: Int = 0, var top: Pair<String, String>? = null, var topActive: Int = -1)

    private fun byMinute(rows: List<MinuteRow>): Map<LocalDateTime, MinAgg> {
        val out = LinkedHashMap<LocalDateTime, MinAgg>()
        for (r in rows) {
            val m = out.getOrPut(r.minute) { MinAgg() }
            m.seconds += r.seconds; m.active += r.active
            if (r.active > m.topActive) { m.top = r.app to r.site; m.topActive = r.active }
        }
        return out
    }

    /** (start, end) of each stretch of active minutes without a 5+ minute break. */
    fun sessions(rows: List<MinuteRow>): List<Pair<LocalDateTime, LocalDateTime>> {
        val out = ArrayList<Pair<LocalDateTime, LocalDateTime>>()
        for (minute in byMinute(rows).filterValues { it.active > 0 }.keys.sorted()) {
            val last = out.lastOrNull()
            if (last != null && Duration.between(last.second, minute).seconds <= SESSION_GAP_MIN * 60)
                out[out.size - 1] = last.first to minute.plusMinutes(1)
            else out.add(minute to minute.plusMinutes(1))
        }
        return out
    }

    /** (seconds, app, start) of the longest run of consecutive active minutes in the same app. */
    fun longestFocus(rows: List<MinuteRow>): Triple<Int, String, LocalDateTime>? {
        var best: Triple<Int, String, LocalDateTime>? = null
        var runSec = 0; var runApp: String? = null; var runStart: LocalDateTime? = null; var runLast: LocalDateTime? = null
        for ((minute, v) in byMinute(rows).toSortedMap()) {
            val app = if (v.active > 0) v.top?.first else null
            if (app != null && app == runApp && runLast != null && Duration.between(runLast, minute).toMinutes() == 1L) {
                runSec += 60; runLast = minute
            } else if (app != null) {
                runSec = 60; runApp = app; runStart = minute; runLast = minute
            } else { runApp = null; runLast = null }
            if (runApp != null && (best == null || runSec > best!!.first)) best = Triple(runSec, runApp!!, runStart!!)
        }
        return best
    }

    /** Segments (start minute of day, length in minutes, kind) for [day]; kind is a category or "idle". */
    fun timeline(rows: List<MinuteRow>, day: LocalDate, category: (String, String) -> String): List<Triple<Int, Int, String>> {
        val segs = ArrayList<IntArray3>()
        for ((minute, v) in byMinute(rows).toSortedMap()) {
            if (minute.toLocalDate() != day) continue
            val kind = if (v.active > 0) category(v.top!!.first, v.top!!.second) else "idle"
            val start = minute.hour * 60 + minute.minute
            val last = segs.lastOrNull()
            if (last != null && last.kind == kind && last.start + last.len == start) last.len += 1
            else segs.add(IntArray3(start, 1, kind))
        }
        return segs.map { Triple(it.start, it.len, it.kind) }
    }

    private class IntArray3(val start: Int, var len: Int, val kind: String)

    /** Active minutes per (day, hour). */
    fun hourlyMinutes(rows: List<MinuteRow>, days: List<LocalDate>): List<List<Double>> {
        val active = HashMap<Pair<LocalDate, Int>, Int>()
        for (r in rows) {
            val k = r.minute.toLocalDate() to r.minute.hour
            active[k] = (active[k] ?: 0) + r.active
        }
        return days.map { d -> (0..23).map { h -> (active[d to h] ?: 0) / 60.0 } }
    }

    /** Level 0-4 of active minutes per (day, hour). */
    fun heatmap(rows: List<MinuteRow>, days: List<LocalDate>): List<List<Int>> =
        hourlyMinutes(rows, days).map { row -> row.map { m -> when { m < 1 -> 0; m < 15 -> 1; m < 30 -> 2; m < 45 -> 3; else -> 4 } } }

    // ---------- switches / visits ----------

    private fun targetOf(e: SwitchEvent): Pair<String, String> =
        if (e.site.isNotEmpty()) "site" to e.site else "app" to e.app

    /** (target, seconds) for each visit (until the next switch; the last until [now], at most 30 min). */
    fun visits(events: List<SwitchEvent>, now: LocalDateTime): List<Pair<Pair<String, String>, Double>> =
        events.mapIndexed { i, e ->
            val end = if (i + 1 < events.size) events[i + 1].ts else minOf(now, e.ts.plusMinutes(30))
            targetOf(e) to maxOf(0.0, Duration.between(e.ts, end).seconds.toDouble())
        }

    data class SwitchSummary(
        val count: Int,
        val perHour: Map<Int, Int>,
        val short: Int,
        val targets: List<Triple<Pair<String, String>, Int, Double>>,   // (target, visits, avg seconds)
        val avgVisit: Double,
    )

    fun switchSummary(events: List<SwitchEvent>, now: LocalDateTime): SwitchSummary {
        val vs = visits(events, now)
        val byTarget = LinkedHashMap<Pair<String, String>, MutableList<Double>>()
        for ((target, sec) in vs) byTarget.getOrPut(target) { ArrayList() }.add(sec)
        val ranked = byTarget.entries.sortedByDescending { it.value.size }
            .map { Triple(it.key, it.value.size, it.value.average()) }
        return SwitchSummary(
            count = events.size,
            perHour = events.groupingBy { it.ts.hour }.eachCount(),
            short = vs.count { it.second < SHORT_VISIT_SEC },
            targets = ranked,
            avgVisit = if (vs.isEmpty()) 0.0 else vs.sumOf { it.second } / vs.size,
        )
    }

    /** How a most-switched-to entry reads: "checking", "focused" or "mixed". */
    fun visitStyle(count: Int, avgSec: Double): String = when {
        avgSec < 60 && count >= 10 -> "checking"
        avgSec >= 3 * 60 -> "focused"
        else -> "mixed"
    }

    // ---------- streaks / goal ----------

    /** Days in a row (back from today) with active screen time within the daily goal; today counts while it still is. */
    fun goalStreak(activeSecByDay: Map<LocalDate, Int>, days: List<LocalDate>, goalSec: Int?): Int {
        if (goalSec == null || goalSec <= 0) return 0
        var n = 0
        for (d in days) { if ((activeSecByDay[d] ?: 0) > goalSec) break; n++ }
        return n
    }

    /** Days in a row (back from today) without an emergency unlock. */
    fun noUnlockStreak(days: List<LocalDate>, unlockDays: Set<LocalDate>): Int {
        var n = 0
        for (d in days) { if (d in unlockDays) break; n++ }
        return n
    }

    // ---------- text ----------

    /** "4 h 12 m" / "52 m" / "0 m". */
    fun hm(seconds: Int): String {
        val minutes = seconds / 60
        val h = minutes / 60; val m = minutes % 60
        return if (h > 0) "$h h %02d m".format(m) else "$m m"
    }

    /** "1 m 46 s" / "42 s". */
    fun ms(seconds: Int): String {
        val m = seconds / 60; val s = seconds % 60
        return if (m > 0) "$m m %02d s".format(s) else "$s s"
    }
}
