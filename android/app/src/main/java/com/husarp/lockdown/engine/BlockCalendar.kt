package com.husarp.lockdown.engine

/**
 * The week's block calendar (PC blockcal.py): when each kind of block is on, per weekday. Pure - no clock, no
 * saved state. Like the PC, only what maps to a time of day gets a span: always-blocked rules (all day), blocked or
 * allowed hours (allowed hours block outside them), and - the phone's extras - modes with a schedule and bedtime.
 * Time and opening limits and temporary blocks have no clock, so they come back as [BlockCalendar.Week.noClock].
 * Minutes are 0..1440 from midnight; a window belongs to the day it starts on and spills into the next morning.
 */
object BlockCalendar {
    const val DAY = 1440

    /** Order = lane order in the view and the legend. */
    enum class Kind { ALWAYS, LIMIT, HOURS, TEMPORARY, MODE, BEDTIME }

    /** [labels]: what is blocked in this stretch (item / mode names, sorted). */
    data class Span(val start: Int, val end: Int, val kind: Kind, val labels: List<String>)

    data class Day(val weekday: Int, val spans: List<Span>)

    /** [noClock]: (kind, label) of limits and temporary blocks - on, but not tied to a time of day. */
    data class Week(val days: List<Day>, val noClock: List<Pair<Kind, String>>)

    /** Minutes on [weekday] (0 = Monday) that one window covers, with the overnight spill (PC _window_on). */
    fun windowOn(win: Window, weekday: Int): List<Pair<Int, Int>> {
        val start = mins(win.start)
        val end = mins(win.end)
        val days = win.days.filter { it in 0..6 }
        if (start < end) return if (weekday in days) listOf(start to end) else emptyList()
        val out = ArrayList<Pair<Int, Int>>()
        if (weekday in days) out.add(start to DAY)
        if ((weekday + 6) % 7 in days && end > 0) out.add(0 to end)   // start == end: 24 h from start
        return out
    }

    /** The minutes a schedule blocks on [weekday]: its windows (block) or what lies outside them (allow). */
    fun scheduleOn(s: Schedule, weekday: Int): List<Pair<Int, Int>> {
        val wins = s.windows.flatMap { windowOn(it, weekday) }
        return merge(if (s.mode == SchedMode.BLOCK) wins else complement(wins))
    }

    fun build(items: List<Item>, groups: List<Group>, modes: List<Mode>, sleep: SleepCfg,
              now: java.time.LocalDateTime = java.time.LocalDateTime.now()): Week {
        // kind -> weekday -> (interval, label)
        val parts = Array(7) { ArrayList<Triple<Kind, Pair<Int, Int>, String>>() }
        val noClock = ArrayList<Pair<Kind, String>>()
        for (item in items) {
            val rules = Rules.effectiveRules(item, groups).map { it.rule }
            if (rules.isEmpty()) continue
            if (rules.any { it.type == RuleType.PERMANENT }) {
                for (d in 0..6) parts[d].add(Triple(Kind.ALWAYS, 0 to DAY, item.name))
                continue    // all day already: its other rules add nothing to the week
            }
            for (r in rules) if (r.type == RuleType.SCHEDULED && r.schedule != null) {
                for (d in 0..6) scheduleOn(r.schedule, d).forEach { parts[d].add(Triple(Kind.HOURS, it, item.name)) }
            }
            if (rules.any { it.type == RuleType.TIME_LIMIT || it.type == RuleType.SWITCH_LIMIT }) noClock.add(Kind.LIMIT to item.name)
            if (rules.any { it.type == RuleType.TEMPORARY && runCatching { now.isBefore(java.time.LocalDateTime.parse(it.tempUntil)) }.getOrDefault(false) }) noClock.add(Kind.TEMPORARY to item.name)
        }
        for (m in modes) {
            val s = m.schedule ?: continue
            // Relax-like modes block nothing extra
            if (m.categories.isEmpty() && m.items.isEmpty() && m.groups.isEmpty() && m.extra.isEmpty()) continue
            for (d in 0..6) scheduleOn(s, d).forEach { parts[d].add(Triple(Kind.MODE, it, m.name)) }
        }
        if (sleep.on) {
            val b = runCatching { mins(sleep.bedtime) }.getOrNull()
            val w = runCatching { mins(sleep.wake) }.getOrNull()
            if (b != null && w != null) {
                val win = Window((0..6).toList(), sleep.bedtime, sleep.wake)
                for (d in 0..6) windowOn(win, d).forEach { parts[d].add(Triple(Kind.BEDTIME, it, "Bedtime")) }
            }
        }
        val days = (0..6).map { d -> Day(d, Kind.entries.flatMap { k -> spansOf(k, parts[d].filter { it.first == k }.map { it.second to it.third }) }) }
        return Week(days, noClock.distinct().sortedWith(compareBy({ it.first }, { it.second.lowercase() })))
    }

    /** One kind's pieces cut at every edge, so each stretch names exactly who is blocked in it; neighbours with the
     *  same names are joined. */
    private fun spansOf(kind: Kind, pieces: List<Pair<Pair<Int, Int>, String>>): List<Span> {
        val live = pieces.filter { it.first.second > it.first.first }
        if (live.isEmpty()) return emptyList()
        val edges = live.flatMap { listOf(it.first.first, it.first.second) }.distinct().sorted()
        val out = ArrayList<Span>()
        for (i in 0 until edges.size - 1) {
            val a = edges[i]
            val b = edges[i + 1]
            val names = live.filter { it.first.first <= a && it.first.second >= b }.map { it.second }.distinct().sortedBy { it.lowercase() }
            if (names.isEmpty()) continue
            val last = out.lastOrNull()
            if (last != null && last.end == a && last.labels == names) out[out.size - 1] = last.copy(end = b)
            else out.add(Span(a, b, kind, names))
        }
        return out
    }

    private fun mins(hhmm: String): Int = Rules.parseHhmm(hhmm).let { it.hour * 60 + it.minute }

    private fun merge(iv: List<Pair<Int, Int>>): List<Pair<Int, Int>> {
        val out = ArrayList<Pair<Int, Int>>()
        for ((a, b) in iv.sortedBy { it.first }) {
            if (b <= a) continue
            val last = out.lastOrNull()
            if (last != null && a <= last.second) out[out.size - 1] = last.first to maxOf(last.second, b) else out.add(a to b)
        }
        return out
    }

    private fun complement(iv: List<Pair<Int, Int>>): List<Pair<Int, Int>> {
        val out = ArrayList<Pair<Int, Int>>()
        var cur = 0
        for ((a, b) in merge(iv)) {
            if (a > cur) out.add(cur to a)
            cur = maxOf(cur, b)
        }
        if (cur < DAY) out.add(cur to DAY)
        return out
    }
}
