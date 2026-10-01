package com.husarp.lockdown.engine

import java.time.LocalDateTime

/**
 * Emergency unlock, ported from the PC emergency.py: unblock picked items for a while. One unlock (of one or
 * more items) counts as one use; uses are limited per day or per week (using the LimitClock periods). Pure.
 */
object Emergency {
    val MINUTE_OPTIONS = listOf(5, 10, 15, 20, 30, 45, 60)

    data class Uses(val left: Int, val allowed: Int, val reset: LocalDateTime)

    /** (uses left, uses allowed, when the count resets) for the current [per] ("day" | "week") period. */
    fun usesLeft(started: List<LocalDateTime>, now: LocalDateTime, per: String, allowed: Int,
                 clock: LimitClock = LimitClock.DEFAULT): Uses {
        val (key, reset) = clock.period(per, now)
        val used = started.count { !it.isAfter(now) && clock.period(per, it).first == key }
        return Uses(maxOf(0, allowed - used), allowed, reset)
    }

    /** When the unlock would end if allowed now, or null if it's off / no uses are left. */
    fun unlockUntil(started: List<LocalDateTime>, now: LocalDateTime, enabled: Boolean, minutes: Int,
                    per: String, allowed: Int, clock: LimitClock = LimitClock.DEFAULT): LocalDateTime? {
        if (!enabled || usesLeft(started, now, per, allowed, clock).left <= 0) return null
        return now.plusMinutes(minutes.toLong())
    }
}
