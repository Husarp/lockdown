package com.husarp.lockdown.engine

import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime

/** A blocked try: the block notice came up for an item ([target]: its app or first site). */
data class BlockedVisit(val ts: LocalDateTime, val itemId: String, val target: String)

/** One day's detail: per-minute screen time, switches and blocked tries. */
data class DayDetail(
    val rows: List<MinuteRow> = emptyList(),
    val switches: List<SwitchEvent> = emptyList(),
    val blocked: List<BlockedVisit> = emptyList(),
)

/** One day as daily totals: seconds and switches per (app, site), blocked tries per (item id, target). */
data class DayTotals(
    val seconds: Map<Pair<String, String>, Int> = emptyMap(),
    val switches: Map<Pair<String, String>, Int> = emptyMap(),
    val blocked: Map<Pair<String, String>, Int> = emptyMap(),
) {
    val total get() = seconds.values.sum()
}

/**
 * Lockdown's own screen-time history, like the PC's (activity / switch_events / block_events + retention.py): the
 * detail of each day is one text file for [DETAIL_DAYS] days, older days become daily totals kept for good. This is
 * the pure part - line formats, parsing, roll-up, which days may go, and the CSV. Statistics only: no limit counts
 * from here (those are in UsageStore).
 *
 * Detail lines (tab-separated): "m HH:MM app site seconds", "s HH:MM:SS app site" (a switch), "r HH:MM:SS app site"
 * (the same visit picks up again), "e HH:MM:SS" (the visit ended: screen off, home, a block), "b HH:MM:SS itemId target".
 * Daily lines: "a day app site seconds", "s day app site count", "b day itemId target count".
 */
object HistoryLog {
    const val DETAIL_DAYS = 32

    private fun clean(s: String) = s.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')
    private fun hms(t: LocalDateTime) = "%02d:%02d:%02d".format(t.hour, t.minute, t.second)

    fun minuteLine(r: MinuteRow) = "m\t%02d:%02d\t${clean(r.app)}\t${clean(r.site)}\t${r.seconds}".format(r.minute.hour, r.minute.minute)
    fun switchLine(e: SwitchEvent) = when {
        e.app.isEmpty() -> "e\t${hms(e.ts)}"
        else -> "${if (e.switch) "s" else "r"}\t${hms(e.ts)}\t${clean(e.app)}\t${clean(e.site)}"
    }
    fun blockedLine(b: BlockedVisit) = "b\t${hms(b.ts)}\t${clean(b.itemId)}\t${clean(b.target)}"

    /** A day's detail file. A line that can't be read is skipped. */
    fun parseDay(day: LocalDate, text: String): DayDetail {
        val rows = ArrayList<MinuteRow>(); val sw = ArrayList<SwitchEvent>(); val bl = ArrayList<BlockedVisit>()
        for (line in text.lineSequence()) {
            val f = line.split('\t')
            runCatching {
                val at = LocalDateTime.of(day, LocalTime.parse(f[1]))
                when (f[0]) {
                    "m" -> { val sec = f[4].toInt(); rows.add(MinuteRow(at, f[2], f[3], sec, sec)) }
                    "s" -> sw.add(SwitchEvent(at, f[2], f[3]))
                    "r" -> sw.add(SwitchEvent(at, f[2], f[3], switch = false))
                    "e" -> sw.add(SwitchEvent(at, "", "", switch = false))
                    "b" -> bl.add(BlockedVisit(at, f[2], f[3]))
                }
            }
        }
        return DayDetail(rows, sw, bl)
    }

    fun totals(d: DayDetail): DayTotals = DayTotals(
        d.rows.groupBy { it.app to it.site }.mapValues { e -> e.value.sumOf { it.seconds } },
        d.switches.filter { it.switch }.groupingBy { it.app to it.site }.eachCount(),
        d.blocked.groupingBy { it.itemId to it.target }.eachCount(),
    )

    fun rollUpLines(day: LocalDate, t: DayTotals): List<String> =
        t.seconds.map { (k, v) -> "a\t$day\t${clean(k.first)}\t${clean(k.second)}\t$v" } +
            t.switches.map { (k, v) -> "s\t$day\t${clean(k.first)}\t${clean(k.second)}\t$v" } +
            t.blocked.map { (k, v) -> "b\t$day\t${clean(k.first)}\t${clean(k.second)}\t$v" }

    /** The daily-totals file, by day (a day written twice adds up). */
    fun parseDaily(text: String): Map<LocalDate, DayTotals> {
        val sec = HashMap<LocalDate, HashMap<Pair<String, String>, Int>>()
        val sw = HashMap<LocalDate, HashMap<Pair<String, String>, Int>>()
        val bl = HashMap<LocalDate, HashMap<Pair<String, String>, Int>>()
        for (line in text.lineSequence()) {
            val f = line.split('\t')
            runCatching {
                val day = LocalDate.parse(f[1]); val k = f[2] to f[3]; val n = f[4].toInt()
                val into = when (f[0]) { "a" -> sec; "s" -> sw; "b" -> bl; else -> return@runCatching }
                into.getOrPut(day) { HashMap() }.merge(k, n, Int::plus)
            }
        }
        return (sec.keys + sw.keys + bl.keys).associateWith { DayTotals(sec[it] ?: emptyMap(), sw[it] ?: emptyMap(), bl[it] ?: emptyMap()) }
    }

    /** The first day whose detail is kept. */
    fun cutoff(today: LocalDate): LocalDate = today.minusDays(DETAIL_DAYS.toLong())

    /** The oldest of the DETAIL_DAYS + 1 newest days up to [today] that have detail, or null while there are fewer.
     *  A clock wrong forwards can't take recent detail: only days that really have data count (PC kept_from). */
    fun keptFrom(days: Collection<LocalDate>, today: LocalDate): LocalDate? {
        val upTo = days.filter { !it.isAfter(today) }.sortedDescending()
        return if (upTo.size > DETAIL_DAYS) upTo[DETAIL_DAYS] else null
    }

    /** The days with detail to roll up into daily totals now, oldest first. */
    fun toRollUp(days: Collection<LocalDate>, today: LocalDate): List<LocalDate> {
        val keep = keptFrom(days, today) ?: return emptyList()
        val end = minOf(cutoff(today), keep)
        return days.filter { it.isBefore(end) }.sorted()
    }

    /** Screen time per day and app / site -> CSV (PC backup.screen_time_csv): newest day last, biggest first. */
    fun csv(days: Map<LocalDate, Map<Pair<String, String>, Int>>, category: (String, String) -> String?): String {
        val sb = StringBuilder("date,app,site,minutes,active minutes,category\n")
        for (day in days.keys.sorted()) for ((k, sec) in days[day]!!.entries.sortedByDescending { it.value }) {
            val min = "%.1f".format(java.util.Locale.ROOT, sec / 60.0)
            sb.append(listOf(day.toString(), k.first, k.second, min, min, category(k.first, k.second) ?: "").joinToString(",") { csvField(it) }).append('\n')
        }
        return sb.toString()
    }

    private fun csvField(s: String) = if (s.any { it == ',' || it == '"' || it == '\n' }) "\"" + s.replace("\"", "\"\"") + "\"" else s
}

/** Adds up the seconds spent in each (app, site) within the running minute; hands back the finished minute's rows
 *  when a new minute starts. */
class MinuteAcc {
    private var minute: LocalDateTime? = null
    private val secs = LinkedHashMap<Pair<String, String>, Double>()

    fun add(now: LocalDateTime, app: String, site: String, seconds: Double): List<MinuteRow> {
        val m = now.withSecond(0).withNano(0)
        val done = if (minute != null && minute != m) flush() else emptyList()
        minute = m
        secs.merge(app to site, seconds, Double::plus)
        return done
    }

    /** The running minute's rows (whole seconds, at most 60 in all), and start afresh. */
    fun flush(): List<MinuteRow> {
        val m = minute ?: return emptyList()
        var room = 60
        val out = secs.mapNotNull { (k, s) ->
            val n = minOf(room, Math.round(s).toInt()); room -= n
            if (n > 0) MinuteRow(m, k.first, k.second, n, n) else null
        }
        secs.clear(); minute = null
        return out
    }
}

/** A switch is coming to something else in front (a site, or an app). The home screen is passed through, not a
 *  switch: going from an app to the home screen and back isn't one. But the visit stops while home is in front
 *  (or the screen is off, or a block is up): that writes its end, and coming back writes it picking up again. */
class SwitchTracker(private val home: String?) {
    private var last: Pair<String, String>? = null
    private var running = false                       // a visit is running

    fun seen(now: LocalDateTime, app: String, site: String): SwitchEvent? {
        if (app == home || app.isEmpty()) return end(now)
        val target = if (site.isNotEmpty()) "site" to site else "app" to app
        if (target == last) {
            if (running) return null
            running = true
            return SwitchEvent(now, app, site, switch = false)
        }
        last = target; running = true
        return SwitchEvent(now, app, site)
    }

    /** Nothing is being used now: the running visit ends (once). */
    fun end(now: LocalDateTime): SwitchEvent? {
        if (!running) return null
        running = false
        return SwitchEvent(now, "", "", switch = false)
    }
}
