package com.husarp.lockdown.engine

import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime

/**
 * When limit periods start and end (ported from rules.py LimitClock). A limit day starts at [resetTime]
 * (default midnight); a week starts Monday, a month on the 1st, at that time. Changing the reset time never
 * cuts the running day short: [carryStart]/[carryUntil] stretch the running day, [holds] keep the running
 * week/month from ending early.
 */
class LimitClock(
    val resetTime: LocalTime = LocalTime.MIDNIGHT,
    private val carryStart: LocalDateTime? = null,
    private val carryUntil: LocalDateTime? = null,
    private val holds: Map<String, Pair<String, LocalDateTime>> = emptyMap(),
) {
    /** (start, end) of the limit day containing [now]. */
    fun day(now: LocalDateTime): Pair<LocalDateTime, LocalDateTime> {
        if (carryUntil != null && carryStart != null && !now.isBefore(carryStart) && now.isBefore(carryUntil)) {
            return carryStart to carryUntil
        }
        var start = LocalDateTime.of(now.toLocalDate(), resetTime)
        if (start.isAfter(now)) start = start.minusDays(1)
        return start to start.plusDays(1)
    }

    /** (key, end) of the day/week/month period containing [now], honouring any hold on a changed reset. */
    fun period(kind: String, now: LocalDateTime): Pair<String, LocalDateTime> {
        val (key, end) = naturalPeriod(kind, now)
        holds[kind]?.let { (holdKey, until) ->
            if (now.isBefore(until)) {
                return if (key != holdKey) holdKey to until else key to maxOf(end, until)
            }
        }
        return key to end
    }

    private fun naturalPeriod(kind: String, now: LocalDateTime): Pair<String, LocalDateTime> {
        val (start, end) = day(now)
        if (kind == "day") {
            val key = if (start.toLocalTime() == LocalTime.MIDNIGHT) start.toLocalDate().toString()
            else start.format(KEY_FMT)
            return key to end
        }
        val d: LocalDate = start.plusHours(12).toLocalDate()   // a day belongs to the date most of it falls on
        val following: LocalDate
        val key: String
        if (kind == "week") {
            val first = d.minusDays((d.dayOfWeek.value - 1).toLong())   // Monday
            following = first.plusDays(7)
            key = "w$first"
        } else {
            val first = d.withDayOfMonth(1)
            following = first.plusMonths(1)
            key = "m%04d-%02d".format(d.year, d.monthValue)
        }
        var boundary = LocalDateTime.of(following, resetTime)
        if (!resetTime.isBefore(LocalTime.NOON)) boundary = boundary.minusDays(1)   // late reset: evening before
        return key to maxOf(end, boundary)
    }

    companion object {
        private val KEY_FMT = java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm")
        val DEFAULT = LimitClock()
    }
}
