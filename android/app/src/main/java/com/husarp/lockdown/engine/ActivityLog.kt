package com.husarp.lockdown.engine

import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.util.Locale

/** A stretch of one app in the foreground, from Android's usage events (epoch ms). */
data class AppSpan(val pkg: String, val fromMs: Long, val toMs: Long)

enum class LogKind { APP, SITE, BLOCK, EMERGENCY, BYPASS }

/** A way round the blocks that Lockdown records for the log itself (data/BypassLog.kt); [label] names the row. */
enum class Bypass(val label: String) { PAUSE("Blocks paused"), CHALLENGE("Challenge passed"), OFF("Lockdown switched off") }

/** The activity log's filter chips. Emergency uses are about blocks, so they sit under Blocks too; "Bypass used"
 *  is every way round them: emergency unlocks, pauses, challenges passed, Lockdown switched off (like the PC). */
enum class LogFilter(val label: String) {
    ALL("All"), APPS("Apps"), SITES("Sites"), BLOCKS("Blocks"), BYPASS("Bypass used");

    fun matches(kind: LogKind) = when (this) {
        ALL -> true
        APPS -> kind == LogKind.APP
        SITES -> kind == LogKind.SITE
        BLOCKS -> kind == LogKind.BLOCK || kind == LogKind.EMERGENCY
        BYPASS -> kind == LogKind.EMERGENCY || kind == LogKind.BYPASS
    }
}

/** One line of the log. [name] is the app, site or blocked thing; [pkg] is set when it is an app (for its icon);
 *  [via] is the browser a site was seen in; [seconds] how long (0: no length to show). */
data class LogRow(val at: LocalDateTime, val kind: LogKind, val name: String, val pkg: String = "", val via: String = "", val seconds: Int = 0) {
    /** "Opened · 12 min" / "Visited in Chrome · 5 min" / "Blocked" / "Emergency used · 20 min" / "Bypass used · 2 h 00 min". */
    val what: String get() {
        val len = if (seconds > 0) " · ${ActivityLog.span(seconds)}" else ""
        return when (kind) {
            LogKind.APP -> "Opened$len"
            LogKind.SITE -> "Visited in $via$len"
            LogKind.BLOCK -> "Blocked"
            LogKind.EMERGENCY -> "Emergency used$len"
            LogKind.BYPASS -> "Bypass used$len"
        }
    }
}

/** The list as it is drawn: a day heading, then that day's rows. */
sealed interface LogItem {
    data class Day(val day: LocalDate) : LogItem
    data class Entry(val row: LogRow) : LogItem
}

/**
 * The "Activity log" page: what was used in the last [DAYS] days, newest first - apps opened (Android's usage events),
 * sites visited in browsers (Lockdown's own history, the browser's address bar), blocks shown (the history's blocked
 * tries), emergency uses (the config) and the other bypasses (a small record of its own: pauses, challenges passed,
 * Lockdown switched off). Pure: the screen collects the raw pieces.
 */
object ActivityLog {
    const val DAYS = 7
    const val MAX_ROWS = 500              // shown at most
    const val MAX_PER_SOURCE = 400        // kept from each source (so a filter still has its newest)
    const val MIN_VISIT_SEC = 5           // a shorter visit is a flicker, not use
    const val APP_GAP_SEC = 60L           // an app back within this long is the same visit (its screens change)
    const val MAX_VISIT_SEC = 3 * 3600L   // a visit with no event for hours is a missing end, not use

    /** "40 s" / "12 min" / "1 h 05 min". */
    fun span(seconds: Int): String = when {
        seconds < 60 -> "$seconds s"
        seconds < 3600 -> "${seconds / 60} min"
        else -> "%d h %02d min".format(Locale.ROOT, seconds / 3600, seconds % 3600 / 60)
    }

    private fun local(ms: Long, zone: ZoneId) = LocalDateTime.ofInstant(Instant.ofEpochMilli(ms), zone)

    /** Apps opened: [spans] of one app that follow each other closer than [APP_GAP_SEC] are one visit, its time the
     *  foreground time added up. [skip] leaves out Lockdown itself, the system bar and the home screen. */
    fun appRows(spans: List<AppSpan>, zone: ZoneId, label: (String) -> String, skip: (String) -> Boolean = { false }): List<LogRow> {
        val out = ArrayList<LogRow>()
        for ((pkg, mine) in spans.filter { it.toMs > it.fromMs && !skip(it.pkg) }.groupBy { it.pkg }) {
            var start = 0L; var end = 0L; var ms = 0L; var open = false
            fun close() {
                if (open && ms / 1000 >= MIN_VISIT_SEC) out.add(LogRow(local(start, zone), LogKind.APP, label(pkg), pkg, seconds = (ms / 1000).toInt()))
                open = false
            }
            for (s in mine.sortedBy { it.fromMs }) {
                if (open && s.fromMs - end <= APP_GAP_SEC * 1000) { end = maxOf(end, s.toMs); ms += s.toMs - s.fromMs }
                else { close(); start = s.fromMs; end = s.toMs; ms = s.toMs - s.fromMs; open = true }
            }
            close()
        }
        return newest(out)
    }

    /** Sites visited: each switch event that has a site starts a visit, which lasts until the next event (another site
     *  or app, or the end of the visit: screen off, home, a block) - at most [MAX_VISIT_SEC], and not past [now].
     *  [events] are in time order, oldest first. [label] names the browser. */
    fun siteRows(events: List<SwitchEvent>, now: LocalDateTime, label: (String) -> String): List<LogRow> {
        val out = ArrayList<LogRow>()
        for ((i, e) in events.withIndex()) {
            if (e.app.isEmpty() || e.site.isEmpty()) continue
            val next = events.getOrNull(i + 1)?.ts ?: now
            val end = minOf(next, now, e.ts.plusSeconds(MAX_VISIT_SEC))
            val sec = Duration.between(e.ts, end).seconds.toInt()
            if (sec >= MIN_VISIT_SEC) out.add(LogRow(e.ts, LogKind.SITE, e.site, via = label(e.app), seconds = sec))
        }
        return newest(out)
    }

    /** Blocks shown. [asApp] tells whether a blocked try's item / target is an app (to show its name and icon); then
     *  [label] names it. Anything else is shown by its target (a site, a blocked word). */
    fun blockRows(blocked: List<BlockedVisit>, asApp: (BlockedVisit) -> String?, label: (String) -> String): List<LogRow> =
        newest(blocked.map { b ->
            val pkg = asApp(b)
            if (pkg != null) LogRow(b.ts, LogKind.BLOCK, label(pkg), pkg) else LogRow(b.ts, LogKind.BLOCK, b.target)
        })

    /** Emergency uses started on or after [from]; the length is how long it freed things (0 for an old single one). */
    fun emergencyRows(uses: List<EmergencyUse>, from: LocalDate): List<LogRow> =
        newest(uses.mapNotNull { u ->
            val at = runCatching { LocalDateTime.parse(u.at) }.getOrNull() ?: return@mapNotNull null
            if (at.toLocalDate().isBefore(from)) return@mapNotNull null
            val until = runCatching { LocalDateTime.parse(u.until) }.getOrNull()
            LogRow(at, LogKind.EMERGENCY, "Emergency unlock", seconds = until?.let { Duration.between(at, it).seconds.toInt() }?.coerceAtLeast(0) ?: 0)
        })

    const val BYPASS_KEEP = 500           // bypass record lines kept (newest)

    /** The bypass record ([text]: one "time<TAB>kind<TAB>seconds" line each, oldest first) with one more line for
     *  [kind] at [at] ([seconds]: how long it frees things, 0 for none), the newest [BYPASS_KEEP] kept. */
    fun bypassAdd(text: String, at: LocalDateTime, kind: Bypass, seconds: Int = 0): String =
        (text.lines().filter { it.isNotBlank() } + "$at\t${kind.name}\t$seconds").takeLast(BYPASS_KEEP).joinToString("\n")

    /** The bypass record's lines from [from] on as rows; an unreadable line is left out. */
    fun bypassRows(text: String, from: LocalDate): List<LogRow> =
        newest(text.lines().mapNotNull { line ->
            val p = line.split('\t')
            val at = runCatching { LocalDateTime.parse(p[0]) }.getOrNull() ?: return@mapNotNull null
            val kind = Bypass.entries.firstOrNull { it.name == p.getOrNull(1) } ?: return@mapNotNull null
            if (at.toLocalDate().isBefore(from)) return@mapNotNull null
            LogRow(at, LogKind.BYPASS, kind.label, seconds = p.getOrNull(2)?.toIntOrNull()?.coerceAtLeast(0) ?: 0)
        })

    private fun newest(rows: List<LogRow>): List<LogRow> = rows.sortedByDescending { it.at }.take(MAX_PER_SOURCE)

    /** Everything together, newest first, only the last [DAYS] days up to [today]. */
    fun merge(today: LocalDate, vararg sources: List<LogRow>): List<LogRow> {
        val from = today.minusDays(DAYS - 1L)
        return sources.flatMap { it }.filter { !it.at.toLocalDate().isBefore(from) && !it.at.toLocalDate().isAfter(today) }.sortedByDescending { it.at }
    }

    /** What a filter chip shows: the newest [MAX_ROWS] of its kinds. */
    fun view(rows: List<LogRow>, filter: LogFilter): List<LogRow> = rows.filter { filter.matches(it.kind) }.take(MAX_ROWS)

    /** [rows] (newest first) as a list with a heading before each day. */
    fun flat(rows: List<LogRow>): List<LogItem> {
        val out = ArrayList<LogItem>(rows.size + DAYS)
        var day: LocalDate? = null
        for (r in rows) {
            val d = r.at.toLocalDate()
            if (d != day) { out.add(LogItem.Day(d)); day = d }
            out.add(LogItem.Entry(r))
        }
        return out
    }

    /** "Today" / "Yesterday" / "Mon 5 Oct". */
    fun dayLabel(day: LocalDate, today: LocalDate): String = when (day) {
        today -> "Today"
        today.minusDays(1) -> "Yesterday"
        else -> "%s %d %s".format(Locale.ENGLISH, day.dayOfWeek.getDisplayName(java.time.format.TextStyle.SHORT, Locale.ENGLISH),
            day.dayOfMonth, day.month.getDisplayName(java.time.format.TextStyle.SHORT, Locale.ENGLISH))
    }
}
