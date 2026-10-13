package com.husarp.lockdown.engine

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

/**
 * The days Insights, Home, the widget and the screen-time goal count in, from trusted time (guard/TrustedTime.kt: the
 * phone's clock set forward or back doesn't move "today"), as the limits do. Android's usage stats themselves are
 * stamped by the phone's clock; only the days and ranges worked out here follow trusted time. Pure, so tested.
 */
object DayBounds {
    /** The date [nowMs] (trusted time) falls on in [zone]. */
    fun today(nowMs: Long, zone: ZoneId): LocalDate = Instant.ofEpochMilli(nowMs).atZone(zone).toLocalDate()

    /** The start of the day [daysAgo] days back (0 = today) of trusted time [nowMs], and its end (exclusive): the next
     *  midnight (a day with a clock change is 23 or 25 hours, not always 24), or for today [dataNowMs] - the newest
     *  moment the data can have: Android's usage stats are stamped by the phone's clock, so today's last app still
     *  open runs to the phone's "now" (trusted time's would add or drop the clock's error to it). */
    fun of(daysAgo: Int, nowMs: Long, zone: ZoneId, dataNowMs: Long = nowMs): Pair<Long, Long> {
        val day = today(nowMs, zone).minusDays(daysAgo.toLong())
        val start = day.atStartOfDay(zone).toInstant().toEpochMilli()
        val end = if (daysAgo == 0) dataNowMs else day.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
        return start to end
    }

    /** The [len] days ending [back] days before [today], oldest first (Insights' bars and ranges). */
    fun days(today: LocalDate, back: Int, len: Int): List<LocalDate> =
        (0 until len).map { today.minusDays((back + len - 1 - it).toLong()) }
}
