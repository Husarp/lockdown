package com.husarp.lockdown.data

import android.content.Context
import android.content.Intent
import com.husarp.lockdown.engine.BlockedVisit
import com.husarp.lockdown.engine.DayDetail
import com.husarp.lockdown.engine.DayTotals
import com.husarp.lockdown.engine.HistoryLog
import com.husarp.lockdown.engine.MinuteAcc
import com.husarp.lockdown.engine.SwitchTracker
import java.io.File
import java.time.LocalDate
import java.time.LocalDateTime

/**
 * Lockdown's own screen-time history (engine/HistoryLog.kt): what's in front each minute, switches and blocked
 * tries, written by the accessibility service of the main copy (an Island app in front is counted as main sees it).
 * A day's detail is one file for 32 days, then it becomes daily totals kept for good, so Insights doesn't depend on
 * how long Android keeps its usage data. Statistics only - no limit counts from here.
 */
object History {
    private var dir: File? = null
    private val acc = MinuteAcc()
    private var switches: SwitchTracker? = null

    @Synchronized fun init(ctx: Context) {
        if (dir != null) return
        dir = File(ctx.filesDir, "history").apply { mkdirs() }
        val home = runCatching {
            ctx.packageManager.resolveActivity(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME), 0)?.activityInfo?.packageName
        }.getOrNull()
        switches = SwitchTracker(home)
    }

    private fun dayFile(day: LocalDate) = File(dir, "d-$day.tsv")
    private fun dailyFile() = File(dir, "daily.tsv")

    private fun append(f: File, lines: List<String>) {
        if (lines.isNotEmpty()) runCatching { f.appendText(lines.joinToString("\n", postfix = "\n")) }
    }

    /** [seconds] of [app] (and [site], in a browser) in front, up to [now]. */
    @Synchronized fun record(now: LocalDateTime, app: String, site: String, seconds: Double) {
        dir ?: return
        val done = acc.add(now, app, site, seconds)
        if (done.isNotEmpty()) append(dayFile(done[0].minute.toLocalDate()), done.map(HistoryLog::minuteLine))
        switches?.seen(now, app, site)?.let { append(dayFile(now.toLocalDate()), listOf(HistoryLog.switchLine(it))) }
    }

    /** Nothing of the phone's is being used (screen off, a block up, Lockdown itself in front): the visit ends. */
    @Synchronized fun idle(now: LocalDateTime) {
        dir ?: return
        switches?.end(now)?.let { append(dayFile(now.toLocalDate()), listOf(HistoryLog.switchLine(it))) }
    }

    /** The block notice came up for an item. */
    @Synchronized fun blocked(now: LocalDateTime, itemId: String, target: String) {
        dir ?: return
        append(dayFile(now.toLocalDate()), listOf(HistoryLog.blockedLine(BlockedVisit(now, itemId, target))))
    }

    /** Writes the running minute out (before reading, so the last minute shows). */
    @Synchronized fun flush() {
        dir ?: return
        val done = acc.flush()
        if (done.isNotEmpty()) append(dayFile(done[0].minute.toLocalDate()), done.map(HistoryLog::minuteLine))
    }

    private fun detailDays(): List<LocalDate> =
        dir?.listFiles()?.mapNotNull { f -> f.name.removePrefix("d-").removeSuffix(".tsv").takeIf { f.name.startsWith("d-") }
            ?.let { runCatching { LocalDate.parse(it) }.getOrNull() } } ?: emptyList()

    /** Once a day: detail older than 32 days becomes daily totals (the day's totals are written, then its file goes). */
    @Synchronized fun retain(today: LocalDate) {
        dir ?: return
        for (day in HistoryLog.toRollUp(detailDays(), today)) {
            val f = dayFile(day)
            val lines = HistoryLog.rollUpLines(day, HistoryLog.totals(HistoryLog.parseDay(day, f.readText())))
            append(dailyFile(), lines)
            f.delete()
        }
    }

    /** Detail for each day in [from]..[to] that has it. */
    fun detail(from: LocalDate, to: LocalDate): Map<LocalDate, DayDetail> {
        flush()
        val out = LinkedHashMap<LocalDate, DayDetail>()
        var d = from
        while (!d.isAfter(to)) {
            val f = dir?.let { dayFile(d) }
            if (f != null && f.exists()) out[d] = HistoryLog.parseDay(d, runCatching { f.readText() }.getOrDefault(""))
            d = d.plusDays(1)
        }
        return out
    }

    /** Daily totals for every day there is: the rolled-up days and the days still in detail. */
    fun allTotals(): Map<LocalDate, DayTotals> {
        flush()
        val out = HashMap(runCatching { HistoryLog.parseDaily(dailyFile().readText()) }.getOrDefault(emptyMap()))
        for (day in detailDays()) {
            val t = HistoryLog.totals(HistoryLog.parseDay(day, runCatching { dayFile(day).readText() }.getOrDefault("")))
            out[day] = out[day]?.let { o -> DayTotals(
                (o.seconds.keys + t.seconds.keys).associateWith { (o.seconds[it] ?: 0) + (t.seconds[it] ?: 0) },
                (o.switches.keys + t.switches.keys).associateWith { (o.switches[it] ?: 0) + (t.switches[it] ?: 0) },
                (o.blocked.keys + t.blocked.keys).associateWith { (o.blocked[it] ?: 0) + (t.blocked[it] ?: 0) },
            ) } ?: t
        }
        return out
    }
}
