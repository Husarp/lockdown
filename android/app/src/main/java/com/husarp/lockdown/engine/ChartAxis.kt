package com.husarp.lockdown.engine

import java.time.LocalDate

/** Insights' bar chart: the labels on their own row under the bars - only every few bars, so each fits on one line
 *  under its bar at phone width (a label per bar wrapped one digit per line among the bars). "" = no label. */
object ChartAxis {
    const val MAX_DAY_LABELS = 6

    /** Today / Yesterday: 24 hour bars, "00" "06" "12" "18" under theirs. */
    fun hourLabels(): List<String> = (0..23).map { if (it % 6 == 0) "%02d".format(it) else "" }

    /** One bar per day of [dates] (oldest first, the last is the newest): up to 7 days each its weekday's letter; more,
     *  the day of the month every [step] days counted back from the newest, so the newest day always has one. */
    fun dayLabels(dates: List<LocalDate>): List<String> {
        if (dates.size <= 7) return dates.map { "MTWTFSS"[it.dayOfWeek.value - 1].toString() }
        val step = step(dates.size)
        return dates.mapIndexed { i, d -> if ((dates.size - 1 - i) % step == 0) "${d.dayOfMonth}" else "" }
    }

    /** Every how many bars a day gets a label: at most [MAX_DAY_LABELS] of them (30 days: every 5th). */
    fun step(bars: Int): Int = if (bars <= 7) 1 else (bars + MAX_DAY_LABELS - 1) / MAX_DAY_LABELS
}
