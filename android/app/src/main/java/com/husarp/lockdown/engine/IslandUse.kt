package com.husarp.lockdown.engine

import kotlinx.serialization.Serializable
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

/** One app's foreground seconds on one day in the Island profile, as the helper reports it to main. */
@Serializable
data class IslandUseRow(val day: String, val pkg: String, val label: String, val sec: Int)

/** An app's use over a range, for the Insights "Top apps". */
data class AppTime(val pkg: String, val label: String, val ms: Long)

/**
 * Screen time spent in Island apps, so Insights count it (statistics only; no limit counts from here). The helper
 * reads its own usage events per day and sends the rows over the link (SyncBody.use, optional: an older helper
 * sends none, an older main ignores the field); main keeps them and merges them into its own per-app totals.
 */
object IslandUse {
    const val DAYS = 31                    // how many days back the helper sends
    const val KEEP_DAYS = 400L             // how long main keeps them
    const val MAX_ROWS = 6000
    private const val MAX_SEC = 86_400

    /** Adds the span [fromMs]..[toMs] of [pkg] to [out] (day, pkg -> ms), split at local midnight. */
    fun addSpan(out: MutableMap<Pair<String, String>, Long>, pkg: String, fromMs: Long, toMs: Long, zone: ZoneId) {
        var t = fromMs
        while (t < toMs) {
            val day = Instant.ofEpochMilli(t).atZone(zone).toLocalDate()
            val end = minOf(toMs, day.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli())
            out.merge(day.toString() to pkg, end - t, Long::plus)
            t = end
        }
    }

    /** Rows from day/app totals; apps under a second are left out. */
    fun rows(totals: Map<Pair<String, String>, Long>, label: (String) -> String): List<IslandUseRow> =
        totals.mapNotNull { (k, ms) -> if (ms >= 1000) IslandUseRow(k.first, k.second, label(k.second), (ms / 1000).toInt()) else null }
            .sortedWith(compareBy({ it.day }, { -it.sec }))

    /** What main accepts from the link: real days, sane seconds, not too many (any paired helper could send anything). */
    fun clean(rows: List<IslandUseRow>): List<IslandUseRow> = rows.filter {
        it.pkg.isNotEmpty() && it.pkg.length <= 200 && it.sec in 1..MAX_SEC && runCatching { LocalDate.parse(it.day) }.isSuccess
    }.map { it.copy(label = it.label.take(80)) }.take(MAX_ROWS)

    /** [incoming] replaces every day it has rows for (the helper's view of a day is complete); other days stay;
     *  days before [today] - [KEEP_DAYS] go. */
    fun merge(old: List<IslandUseRow>, incoming: List<IslandUseRow>, today: LocalDate): List<IslandUseRow> {
        val days = incoming.map { it.day }.toSet()
        val from = today.minusDays(KEEP_DAYS).toString()
        return (old.filter { it.day !in days } + incoming).filter { it.day >= from }
    }

    /** Island's time per app in [from]..[to]. */
    fun total(rows: List<IslandUseRow>, from: LocalDate, to: LocalDate): Map<String, AppTime> {
        val a = from.toString(); val b = to.toString()
        val out = LinkedHashMap<String, AppTime>()
        for (r in rows) if (r.day in a..b) {
            val o = out[r.pkg]
            out[r.pkg] = AppTime(r.pkg, o?.label ?: r.label, (o?.ms ?: 0) + r.sec * 1000L)
        }
        return out
    }

    /** Main's apps plus Island's, one entry per package (the same app in both adds up); an app only in Island is
     *  labelled "Label (Island)". Biggest first. */
    fun topApps(main: List<AppTime>, island: Map<String, AppTime>): List<AppTime> {
        val mine = main.associateBy { it.pkg }
        val both = main.map { m -> island[m.pkg]?.let { m.copy(ms = m.ms + it.ms) } ?: m }
        val only = island.values.filter { it.pkg !in mine }.map { it.copy(label = "${it.label} (Island)") }
        return (both + only).sortedByDescending { it.ms }
    }
}
