package com.husarp.lockdown.engine

import java.time.LocalDate

/** One row of Insights' top list: an app, or - with sites instead of browsers - a site ([site] set, [pkg] the
 *  browser it was read in, [via] that browser's name for the subtitle line). */
data class TopRow(val pkg: String, val label: String, val ms: Long, val site: String? = null, val via: String? = null)

/**
 * Sites in place of browsers in Insights' top apps and the CSV. The time per site is what the history recorded while a
 * browser was in front (the address bar's host, FrontSplit); the browser's own row keeps what no site was read for
 * (tabs, a new tab page, a hidden bar). Off: everything as it was, a browser is one app. Pure.
 */
object SiteTop {
    /** A browser row with less than this left after its sites is hidden. */
    const val HIDE_BROWSER_UNDER_MS = 30_000L
    private const val MIN_SITE_MS = 1000L

    /** Browser package -> site -> ms, from the history's minute rows (rows of other apps, and rows with no site, are skipped). */
    fun msByBrowser(rows: Iterable<MinuteRow>, isBrowser: (String) -> Boolean = Browsers::isBrowser): Map<String, Map<String, Long>> {
        val out = HashMap<String, HashMap<String, Long>>()
        for (r in rows) if (r.site.isNotEmpty() && isBrowser(r.app)) out.getOrPut(r.app) { HashMap() }.merge(r.site, r.seconds * 1000L, Long::plus)
        return out
    }

    /** The top list, biggest first: [apps] as they are when [on] is false (or there are no [sites]); else each browser
     *  with sites becomes its sites plus what is left of its own time. A browser that has sites but no app row (no
     *  usage access) still shows them; [labelOf] names it. */
    fun rows(apps: List<AppTime>, sites: Map<String, Map<String, Long>>, on: Boolean, labelOf: (String) -> String): List<TopRow> {
        if (!on || sites.isEmpty()) return apps.map { TopRow(it.pkg, it.label, it.ms) }
        val out = ArrayList<TopRow>()
        val done = HashSet<String>()
        fun add(pkg: String, name: String) {
            val mine = sites[pkg].orEmpty().filterValues { it >= MIN_SITE_MS }
            for ((host, ms) in mine) out.add(TopRow(pkg, host, ms, site = host, via = name))
            done.add(pkg)
        }
        for (a in apps) {
            val s = sites[a.pkg].orEmpty().filterValues { it >= MIN_SITE_MS }
            if (s.isEmpty()) { out.add(TopRow(a.pkg, a.label, a.ms)); continue }
            add(a.pkg, a.label)
            val left = a.ms - s.values.sum()
            if (left >= HIDE_BROWSER_UNDER_MS) out.add(TopRow(a.pkg, a.label, left))
        }
        for (pkg in sites.keys) if (pkg !in done) add(pkg, labelOf(pkg))
        return out.sortedByDescending { it.ms }
    }

    /** Screen time per day and (app, site) with each site folded into its app: the CSV when sites are not shown instead of browsers. */
    fun withoutSites(days: Map<LocalDate, Map<Pair<String, String>, Int>>): Map<LocalDate, Map<Pair<String, String>, Int>> =
        days.mapValues { (_, m) ->
            val out = LinkedHashMap<Pair<String, String>, Int>()
            for ((k, sec) in m) out.merge(k.first to "", sec, Int::plus)
            out
        }
}
