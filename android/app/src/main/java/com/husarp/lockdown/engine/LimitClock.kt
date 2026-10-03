package com.husarp.lockdown.engine

import kotlinx.serialization.Serializable
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
        return naturalDay(now)
    }

    /** (start, end) of the limit day containing [now] by [resetTime] alone, ignoring a carried (stretched) day. */
    fun naturalDay(now: LocalDateTime): Pair<LocalDateTime, LocalDateTime> {
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

        private fun at(s: String?) = s?.let { runCatching { LocalDateTime.parse(it) }.getOrNull() }

        /** The clock for reset time [time], with what a change of it carries (the running day stretched at most
         *  two days from its start, a second change can't stack). */
        fun of(time: LocalTime, carry: ResetCarry?): LimitClock {
            val start = at(carry?.dayStart)
            val until = at(carry?.switch)?.let { u -> start?.let { minOf(u, it.plusDays(2)) } ?: u }
            val holds = carry?.holds.orEmpty().mapNotNull { (k, h) -> at(h.until)?.let { k to (h.key to it) } }.toMap()
            return LimitClock(time, start, until, holds)
        }

        /** An earlier reset time ends a limit day sooner than it would have - a way to get limits back early, so
         *  starting it now goes through the challenge. A later one only makes a day longer (PC reset_is_looser). */
        fun resetLooser(old: LocalTime, new: LocalTime) = new.isBefore(old)

        /** A reset moved across 12:00 (either way) changes which weekday the coming days count as (a day from 12:00
         *  or later is named after the next date), which can skip a weekday's limit or count one twice - so it
         *  goes through the challenge, even the "from when the day ends" way. */
        fun crossesNoon(old: LocalTime, new: LocalTime) = old.isBefore(LocalTime.NOON) != new.isBefore(LocalTime.NOON)

        /** What changing the reset to [new] carries (PC change_reset). The running day is never cut short: a later
         *  time keeps it running to that time (at most a day past its own end); an earlier one leaves it alone and
         *  takes over when it ends. The running week and month never end before they would have. Free. */
        fun change(clock: LimitClock, new: LocalTime, now: LocalDateTime): ResetCarry {
            val (start, end) = clock.day(now)
            val switch = maxOf(end, minOf(LocalDateTime.of(end.toLocalDate(), new), start.plusDays(2)))
            return ResetCarry(start.toString(), switch.toString(), holds(clock, now))
        }

        /** The new reset time from this moment: the running limit day ends now and a fresh one starts, so its
         *  limits start over - only behind the challenge (PC apply_reset_now). The week and month are still held. */
        fun startNow(clock: LimitClock, now: LocalDateTime) = ResetCarry(holds = holds(clock, now))

        private fun holds(clock: LimitClock, now: LocalDateTime) =
            listOf("week", "month").associateWith { clock.period(it, now).let { (k, u) -> PeriodHold(k, u.toString()) } }
    }
}

/** A changed reset time: the day running then lasts from [dayStart] to [switch]; [holds] keep the running week and
 *  month ("week"/"month" -> its key and end) from ending early. Kept in the config, so the Island copy has it too. */
@Serializable
data class ResetCarry(val dayStart: String? = null, val switch: String? = null, val holds: Map<String, PeriodHold> = emptyMap())

@Serializable
data class PeriodHold(val key: String, val until: String)
