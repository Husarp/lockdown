package com.husarp.lockdown.usage

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Process
import android.provider.Settings
import com.husarp.lockdown.engine.AppSpan
import com.husarp.lockdown.engine.IslandHourRow
import com.husarp.lockdown.engine.IslandUse
import com.husarp.lockdown.engine.IslandUseRow
import com.husarp.lockdown.guard.TrustedTime

/** One app's usage over a period: total foreground time and how many times it was opened. */
data class AppUse(val pkg: String, val label: String, val totalMs: Long, val opens: Int)

/** A day's usage: per-app totals, the grand total, and 24 hourly buckets for the timeline. */
data class DayUse(val apps: List<AppUse>, val totalMs: Long, val hourly: LongArray)

object Usage {

    /** Has the user granted "usage access" in Settings? (Left at its default, the permission itself decides.) */
    fun hasAccess(ctx: Context): Boolean {
        val ops = ctx.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode = if (Build.VERSION.SDK_INT >= 29) ops.unsafeCheckOpNoThrow(
            AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx.packageName,
        ) else @Suppress("DEPRECATION") ops.checkOpNoThrow(
            AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx.packageName,
        )
        return com.husarp.lockdown.engine.PermCheck.usageAllowed(mode,
            ctx.checkCallingOrSelfPermission(android.Manifest.permission.PACKAGE_USAGE_STATS) == android.content.pm.PackageManager.PERMISSION_GRANTED)
    }

    fun accessIntent() = Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)

    /** Foreground time and opens per app between [start] and [end], computed from raw events. A worker passes
     *  [stopped] (its isStopped), so a job Android stops quits reading at once. */
    fun range(ctx: Context, start: Long, end: Long, stopped: () -> Boolean = { false }): DayUse {
        val usm = ctx.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val events = usm.queryEvents(start, end)
        val total = HashMap<String, Long>()
        val opens = HashMap<String, Int>()
        val resumedAt = HashMap<String, Long>()
        val hourly = LongArray(24)
        val e = UsageEvents.Event()
        while (events.hasNextEvent() && !stopped()) {
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

    /** Each stretch an app was in the foreground between [start] and [end] (epoch ms), from the raw events - what the
     *  activity log turns into "opened" rows. One query; the caller runs it off the main thread. */
    fun spans(ctx: Context, start: Long, end: Long): List<AppSpan> {
        val usm = ctx.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val events = usm.queryEvents(start, end)
        val out = ArrayList<AppSpan>()
        val resumedAt = HashMap<String, Long>()
        val e = UsageEvents.Event()
        while (events.hasNextEvent()) {
            events.getNextEvent(e)
            val pkg = e.packageName ?: continue
            when (e.eventType) {
                UsageEvents.Event.ACTIVITY_RESUMED -> resumedAt[pkg] = e.timeStamp
                UsageEvents.Event.ACTIVITY_PAUSED, UsageEvents.Event.ACTIVITY_STOPPED ->
                    resumedAt.remove(pkg)?.let { if (e.timeStamp > it) out.add(AppSpan(pkg, it, e.timeStamp)) }
            }
        }
        for ((pkg, from) in resumedAt) if (end > from) out.add(AppSpan(pkg, from, end))
        return out
    }

    private fun addSpan(from: Long, to: Long, pkg: String, total: HashMap<String, Long>, hourly: LongArray) {
        if (to <= from) return
        total[pkg] = (total[pkg] ?: 0) + (to - from)
        spreadHours(from, to, hourly, java.time.ZoneId.systemDefault())
    }

    /** Start-of-day for a day [daysAgo] days back (0 = today), and the exclusive end - days of trusted time
     *  (engine/DayBounds.kt); today ends at the phone's "now", the clock the usage stats are stamped by. */
    fun dayBounds(ctx: Context, daysAgo: Int): Pair<Long, Long> =
        com.husarp.lockdown.engine.DayBounds.of(daysAgo, TrustedTime.now(ctx), TrustedTime.zone(), System.currentTimeMillis())

    /** Start of the period spanning the last [days] days up to now. */
    fun lastDaysStart(ctx: Context, days: Int): Long = dayBounds(ctx, days - 1).first

    /** Total foreground ms for each of the last [days] days (oldest first). One query, bucketed by day. */
    fun dailyTotals(ctx: Context, days: Int): List<Long> {
        val start = lastDaysStart(ctx, days)
        val end = System.currentTimeMillis()          // (the usage stats' own clock)
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

    /** Helper: this profile's foreground seconds per app and day over the last [IslandUse.DAYS] days, and per hour
     *  for today and yesterday (one query). The spans of the Activity log's days go into [spans] when given. */
    fun islandRows(ctx: Context, spans: MutableList<AppSpan>? = null): Pair<List<IslandUseRow>, List<IslandHourRow>> {
        // the days are trusted time's (as main's Insights asks for them); the spans are stamped by the phone's clock
        val zone = TrustedTime.zone()
        val today = com.husarp.lockdown.engine.DayBounds.today(TrustedTime.now(ctx), zone)
        val end = System.currentTimeMillis()
        val start = today.minusDays(IslandUse.DAYS - 1L).atStartOfDay(zone).toInstant().toEpochMilli()
        val hourFrom = today.minusDays(IslandUse.HOUR_DAYS - 1L).atStartOfDay(zone).toInstant().toEpochMilli()
        val spansFrom = today.minusDays(com.husarp.lockdown.engine.ActivityLog.DAYS - 1L).atStartOfDay(zone).toInstant().toEpochMilli()
        val usm = ctx.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val events = usm.queryEvents(start, end)
        val totals = HashMap<Pair<String, String>, Long>()
        val hours = HashMap<Pair<String, Int>, Long>()
        val resumedAt = HashMap<String, Long>()
        fun span(pkg: String, from: Long, to: Long) {
            IslandUse.addSpan(totals, pkg, from, to, zone)
            IslandUse.addHourSpan(hours, from, to, hourFrom, zone)
            if (spans != null && from >= spansFrom && to > from) spans.add(AppSpan(pkg, from, to))
        }
        val e = UsageEvents.Event()
        while (events.hasNextEvent()) {
            events.getNextEvent(e)
            val pkg = e.packageName ?: continue
            when (e.eventType) {
                UsageEvents.Event.ACTIVITY_RESUMED -> resumedAt[pkg] = e.timeStamp
                UsageEvents.Event.ACTIVITY_PAUSED, UsageEvents.Event.ACTIVITY_STOPPED ->
                    resumedAt.remove(pkg)?.let { span(pkg, it, e.timeStamp) }
            }
        }
        for ((pkg, from) in resumedAt) span(pkg, from, end)
        val pm = ctx.packageManager
        return IslandUse.rows(totals) { pkg -> runCatching { pm.getApplicationInfo(pkg, 0).loadLabel(pm).toString() }.getOrDefault(pkg) } to
            IslandUse.hourRows(hours)
    }
}

/** Spreads [from]..[to] over the 24 hour-of-day buckets of [hourly] (summed across days), split at local hour
 *  boundaries. Every step moves forward to the next local hour, so a span across midnight or a clock change ends
 *  (the old loop kept the first day's start and stood still at midnight: a worker spun the CPU at 100% for hours). */
internal fun spreadHours(from: Long, to: Long, hourly: LongArray, zone: java.time.ZoneId) {
    var t = from
    while (t < to) {
        val z = java.time.Instant.ofEpochMilli(t).atZone(zone)
        val end = minOf(to, z.truncatedTo(java.time.temporal.ChronoUnit.HOURS).plusHours(1).toInstant().toEpochMilli())
        hourly[z.hour] += end - t
        t = end
    }
}
