package com.husarp.lockdown

import com.husarp.lockdown.engine.DayBounds
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId

class DayBoundsTest {
    private val warsaw = ZoneId.of("Europe/Warsaw")
    private fun ms(t: String) = LocalDateTime.parse(t).atZone(warsaw).toInstant().toEpochMilli()

    @Test fun today_is_the_trusted_date_not_the_phone_date() {
        // trusted time says 23:30 on the 13th; the phone's clock (set forward) would say the 15th - only trusted time is used
        val trusted = ms("2026-10-13T23:30")
        assertEquals(LocalDate.parse("2026-10-13"), DayBounds.today(trusted, warsaw))
    }

    @Test fun today_runs_from_midnight_to_now() {
        val now = ms("2026-10-13T10:15")
        assertEquals(ms("2026-10-13T00:00") to now, DayBounds.of(0, now, warsaw))
    }

    @Test fun today_starts_at_trusted_midnight_and_runs_to_the_usage_stats_clock() {
        // the phone's clock set two days forward: today still starts at trusted midnight (not a fresh "day" two days on)
        // and runs to the newest stamp the phone's usage stats can carry, so time used since the change counts today
        val trusted = ms("2026-10-13T10:15")
        val phone = ms("2026-10-15T10:15")
        assertEquals(ms("2026-10-13T00:00") to phone, DayBounds.of(0, trusted, warsaw, phone))
        // an earlier day doesn't care about the phone's clock
        assertEquals(ms("2026-10-12T00:00") to ms("2026-10-13T00:00"), DayBounds.of(1, trusted, warsaw, phone))
    }

    @Test fun an_earlier_day_runs_midnight_to_midnight_also_across_a_clock_change() {
        val now = ms("2026-10-27T09:00")
        assertEquals(ms("2026-10-26T00:00") to ms("2026-10-27T00:00"), DayBounds.of(1, now, warsaw))
        // 25 Oct 2026: summer time ends in Poland, the day is 25 hours long
        val (s, e) = DayBounds.of(2, now, warsaw)
        assertEquals(ms("2026-10-25T00:00"), s)
        assertEquals(25 * 3_600_000L, e - s)
    }

    @Test fun the_days_of_a_range_oldest_first() {
        val today = LocalDate.parse("2026-10-13")
        assertEquals(listOf("2026-10-07", "2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13").map(LocalDate::parse),
            DayBounds.days(today, 0, 7))
        assertEquals(listOf(LocalDate.parse("2026-10-12")), DayBounds.days(today, 1, 1))     // Yesterday
    }
}
