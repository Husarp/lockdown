package com.husarp.lockdown.engine

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.util.Locale

/** The "All apps" page's sort orders (remembered; Home's card and Insights' Top apps always go by time). */
enum class AppSort(val label: String) { TIME("Most time"), OPENS("Most opens"), RECENT("Recently opened") }

/** Which apps the lists show (remembered; Home's card, Insights' Top apps and the "All apps" page all follow it).
 *  [PERSONAL] / [ISLAND] are offered only with Island linked. */
enum class AppKind(val label: String) {
    ALL("All apps"), INSTALLED("Only apps I installed"), PERSONAL("Personal only"), ISLAND("Island only");

    fun matches(a: AppStat) = when (this) {
        ALL -> true
        INSTALLED -> !a.system
        PERSONAL -> a.personal
        ISLAND -> a.island
    }
}

/** One row of the app lists: [ms] of use in the range, [opens] (visits, as the Activity log counts them) and the
 *  latest one's start [lastMs] (epoch ms, 0 = none known); [system]: came with the phone (or is its launcher or
 *  Settings); [personal] / [island]: used in the main profile / in Island (an app used in both is both). A site row
 *  ([site] set, sites instead of browsers) is its browser's kind. */
data class AppStat(
    val pkg: String, val label: String, val ms: Long, val opens: Int = 0, val lastMs: Long = 0L,
    val system: Boolean = false, val personal: Boolean = true, val island: Boolean = false,
    val site: String? = null, val via: String? = null,
)

/** Pure parts of the app lists: who counts as a system app, opens per app, the sort, the filter and the bars. */
object AppList {
    // ApplicationInfo.FLAG_SYSTEM / FLAG_UPDATED_SYSTEM_APP (kept here so the engine stays free of Android)
    const val FLAG_SYSTEM = 1
    const val FLAG_UPDATED_SYSTEM_APP = 0x80

    /** An app is "system" when Android says so ([flags] of its ApplicationInfo; null: not installed in this profile,
     *  e.g. an Island-only app) or it is one of the [always] ones (the home screen, Settings). */
    fun system(pkg: String, flags: Int?, always: Set<String>): Boolean =
        pkg in always || (flags != null && flags and (FLAG_SYSTEM or FLAG_UPDATED_SYSTEM_APP) != 0)

    /** Opens and the latest open per key, from main's [visits] (only those of at least [ActivityLog.MIN_VISIT_SEC], as the
     *  log lists them), Island's [islandOpens] and the [sites] visited (Activity log rows), all started in [fromMs]..[toMs). */
    fun opens(visits: List<AppVisit>, islandOpens: List<IslandOpen>, sites: List<Pair<String, Long>>, fromMs: Long, toMs: Long): Map<String, Pair<Int, Long>> {
        val out = HashMap<String, Pair<Int, Long>>()
        fun add(key: String, at: Long) {
            if (at < fromMs || at >= toMs) return
            val o = out[key]
            out[key] = ((o?.first ?: 0) + 1) to maxOf(o?.second ?: 0L, at)
        }
        for (v in visits) if (v.ms / 1000 >= ActivityLog.MIN_VISIT_SEC) add(v.pkg, v.fromMs)
        for (o in islandOpens) add(o.pkg, o.at)
        for ((host, at) in sites) add(host, at)
        return out
    }

    /** The rows: [rows] (biggest first, as Insights builds them) with their kind - used in the main profile when in
     *  [mainPkgs] or not in [islandPkgs]; Island when in [islandPkgs]; system when in [system] - and their [opens]. */
    fun stats(rows: List<TopRow>, mainPkgs: Set<String>, islandPkgs: Set<String>, system: Set<String>,
              opens: Map<String, Pair<Int, Long>> = emptyMap()): List<AppStat> = rows.map { r ->
        val o = opens[r.site ?: r.pkg]
        AppStat(r.pkg, r.label, r.ms, o?.first ?: 0, o?.second ?: 0L, system = r.pkg in system,
            personal = r.pkg in mainPkgs || r.pkg !in islandPkgs, island = r.pkg in islandPkgs, site = r.site, via = r.via)
    }

    /** What [kind] really is: a kind not [offered] (Island unlinked since) is all apps. */
    fun effective(kind: AppKind, offered: List<AppKind>): AppKind = if (kind in offered) kind else AppKind.ALL

    /** The kinds to offer: Personal and Island only with Island linked. */
    fun kinds(islandLinked: Boolean): List<AppKind> =
        if (islandLinked) AppKind.entries.toList() else listOf(AppKind.ALL, AppKind.INSTALLED)

    fun filter(list: List<AppStat>, kind: AppKind): List<AppStat> = list.filter { kind.matches(it) }

    /** Most time; most opens (then time); latest open first (then time; never opened last). Ties by name. */
    fun sort(list: List<AppStat>, sort: AppSort): List<AppStat> = when (sort) {
        AppSort.TIME -> list.sortedWith(compareByDescending<AppStat> { it.ms }.thenBy { it.label.lowercase() })
        AppSort.OPENS -> list.sortedWith(compareByDescending<AppStat> { it.opens }.thenByDescending { it.ms }.thenBy { it.label.lowercase() })
        AppSort.RECENT -> list.sortedWith(compareByDescending<AppStat> { it.lastMs }.thenByDescending { it.ms }.thenBy { it.label.lowercase() })
    }

    /** A row's bar: ITS OWN time on the list's common scale ([maxMs], the biggest row's), whatever limit it has (apps
     *  sharing a group limit share the "min left", not the bar); a sliver at least, so a short row still shows one. */
    fun bar(ms: Long, maxMs: Long): Float = if (maxMs <= 0L) 0.02f else (ms.toFloat() / maxMs).coerceIn(0.02f, 1f)

    /** "14:05" today, "Yesterday 14:05", else "Mon 5 Oct"; "" when never opened. */
    fun lastText(lastMs: Long, today: LocalDate, zone: ZoneId): String {
        if (lastMs <= 0L) return ""
        val t = Instant.ofEpochMilli(lastMs).atZone(zone).toLocalDateTime()
        val hm = "%02d:%02d".format(Locale.ROOT, t.hour, t.minute)
        return when (t.toLocalDate()) {
            today -> hm
            today.minusDays(1) -> "Yesterday $hm"
            else -> ActivityLog.dayLabel(t.toLocalDate(), today)
        }
    }
}
