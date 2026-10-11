package com.husarp.lockdown

import com.husarp.lockdown.usage.spreadHours
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDateTime
import java.time.ZoneId

/** The weekly digest worker spun the CPU at 100% for hours: a span across midnight never ended (0.5.15). */
class UsageSpanTest {
    private val zone = ZoneId.of("Europe/Warsaw")
    private fun ms(s: String) = LocalDateTime.parse(s).atZone(zone).toInstant().toEpochMilli()
    private val h = 3_600_000L

    @Test(timeout = 2000) fun spanAcrossMidnightEnds() {
        val hourly = LongArray(24)
        spreadHours(ms("2026-10-05T23:30"), ms("2026-10-06T01:15"), hourly, zone)
        assertEquals(h / 2, hourly[23]); assertEquals(h, hourly[0]); assertEquals(h / 4, hourly[1])
        assertEquals(h + 3 * h / 4, hourly.sum())
    }

    @Test(timeout = 2000) fun wholeWeekEnds() {
        val hourly = LongArray(24)
        val from = ms("2026-10-01T00:00"); val to = ms("2026-10-08T00:00")
        spreadHours(from, to, hourly, zone)
        assertEquals(to - from, hourly.sum())
        hourly.forEach { assertEquals(7 * h, it) }
    }

    @Test(timeout = 2000) fun clockChangeDaysEnd() {
        // 25-hour day (clocks back) and 23-hour day (clocks forward)
        for ((a, b) in listOf("2026-10-24T22:00" to "2026-10-26T02:00", "2026-03-28T22:00" to "2026-03-30T02:00")) {
            val hourly = LongArray(24)
            spreadHours(ms(a), ms(b), hourly, zone)
            assertEquals(ms(b) - ms(a), hourly.sum())
        }
    }

    @Test(timeout = 2000) fun emptyOrBackwardSpanIsNothing() {
        val hourly = LongArray(24)
        spreadHours(ms("2026-10-05T10:00"), ms("2026-10-05T10:00"), hourly, zone)
        spreadHours(ms("2026-10-05T11:00"), ms("2026-10-05T10:00"), hourly, zone)
        assertEquals(0L, hourly.sum())
    }
}
