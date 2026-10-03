package com.husarp.lockdown.usage

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Process
import android.provider.Settings
import java.util.Calendar

/** One app's usage over a period: total foreground time and how many times it was opened. */
data class AppUse(val pkg: String, val label: String, val totalMs: Long, val opens: Int)

/** A day's usage: per-app totals, the grand total, and 24 hourly buckets for the timeline. */
data class DayUse(val apps: List<AppUse>, val totalMs: Long, val hourly: LongArray)

object Usage {

    /** Has the user granted "usage access" in Settings? */
    fun hasAccess(ctx: Context): Boolean {
        val ops = ctx.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode = if (Build.VERSION.SDK_INT >= 29) ops.unsafeCheckOpNoThrow(
            AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx.packageName,
        ) else @Suppress("DEPRECATION") ops.checkOpNoThrow(
            AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx.packageName,
        )
        return mode == AppOpsManager.MODE_ALLOWED
    }

    fun accessIntent() = Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)

    /** Foreground time and opens per app between [start] and [end], computed from raw events. */
    fun range(ctx: Context, start: Long, end: Long): DayUse {
        val usm = ctx.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val events = usm.queryEvents(start, end)
        val total = HashMap<String, Long>()
        val opens = HashMap<String, Int>()
        val resumedAt = HashMap<String, Long>()
        val hourly = LongArray(24)
        val e = UsageEvents.Event()
        while (events.hasNextEvent()) {
            events.getNextEvent(e)
            val pkg = e.packageName ?: continue
            when (e.eventType) {
                UsageEvents.Event.ACTIVITY_RESUMED -> {
                    resumedAt[pkg] = e.timeStamp
                    opens[pkg] = (opens[pkg] ?: 0) + 1
                }
                UsageEvents.Event.ACTIVITY_PAUSED, UsageEvents.Event.ACTIVITY_STOPPED -> {
                    val from = resumedAt.remove(pkg) ?: continue
                    addSpan(from, e.timeStamp, pkg, total, hourly)
                }
            }
        }
        // apps still in the foreground when the window ended
        for ((pkg, from) in resumedAt) addSpan(from, end, pkg, total, hourly)

        val pm = ctx.packageManager
        val apps = total.entries.map { (pkg, ms) ->
            val label = runCatching { pm.getApplicationInfo(pkg, 0).loadLabel(pm).toString() }.getOrDefault(pkg)
            AppUse(pkg, label, ms, opens[pkg] ?: 0)
        }.filter { it.totalMs >= 1000 }.sortedByDescending { it.totalMs }
        return DayUse(apps, apps.sumOf { it.totalMs }, hourly)
    }

    private fun addSpan(from: Long, to: Long, pkg: String, total: HashMap<String, Long>, hourly: LongArray) {
        if (to <= from) return
        total[pkg] = (total[pkg] ?: 0) + (to - from)
        // spread the span across the hour buckets it touches (approximate, good enough for a bar chart)
        val dayStart = startOfDay(from)
        var t = from
        while (t < to) {
            val h = (((t - dayStart) / 3_600_000L).toInt()).coerceIn(0, 23)
            val hourEnd = dayStart + (h + 1) * 3_600_000L
            val chunkEnd = minOf(to, hourEnd)
            hourly[h] += chunkEnd - t
            t = chunkEnd
        }
    }

    private fun startOfDay(ms: Long): Long = Calendar.getInstance().apply {
        timeInMillis = ms
        set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
    }.timeInMillis

    /** Start-of-day for a day [daysAgo] days back (0 = today), and the exclusive end. */
    fun dayBounds(daysAgo: Int): Pair<Long, Long> {
        val cal = Calendar.getInstance().apply {
            add(Calendar.DAY_OF_YEAR, -daysAgo)
            set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
        }
        val start = cal.timeInMillis
        val end = if (daysAgo == 0) System.currentTimeMillis() else start + 86_400_000L
        return start to end
    }

    /** Start of the period spanning the last [days] days up to now. */
    fun lastDaysStart(days: Int): Long = dayBounds(days - 1).first

    /** Total foreground ms for each of the last [days] days (oldest first). One query, bucketed by day. */
    fun dailyTotals(ctx: Context, days: Int): List<Long> {
        val cal = Calendar.getInstance().apply {
            add(Calendar.DAY_OF_YEAR, -(days - 1))
            set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
        }
        val start = cal.timeInMillis
        val end = System.currentTimeMillis()
        val usm = ctx.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val events = usm.queryEvents(start, end)
        val totals = LongArray(days)
        val resumedAt = HashMap<String, Long>()
        val e = UsageEvents.Event()
        fun add(from: Long, to: Long) {
            if (to <= from) return
            val idx = (((from - start) / 86_400_000L).toInt()).coerceIn(0, days - 1)
            totals[idx] += (to - from)
        }
        while (events.hasNextEvent()) {
            events.getNextEvent(e)
            val pkg = e.packageName ?: continue
            when (e.eventType) {
                UsageEvents.Event.ACTIVITY_RESUMED -> resumedAt[pkg] = e.timeStamp
                UsageEvents.Event.ACTIVITY_PAUSED, UsageEvents.Event.ACTIVITY_STOPPED -> {
                    val f = resumedAt.remove(pkg) ?: continue; add(f, e.timeStamp)
                }
            }
        }
        for ((_, f) in resumedAt) add(f, end)
        return totals.toList()
    }
}
