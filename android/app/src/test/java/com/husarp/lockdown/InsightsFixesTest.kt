package com.husarp.lockdown

import com.husarp.lockdown.engine.ActivityLog
import com.husarp.lockdown.engine.ChartAxis
import com.husarp.lockdown.engine.DayDetail
import com.husarp.lockdown.engine.DayTotals
import com.husarp.lockdown.engine.HomeApp
import com.husarp.lockdown.engine.IslandSight
import com.husarp.lockdown.engine.LogFilter
import com.husarp.lockdown.engine.LogKind
import com.husarp.lockdown.engine.LogRange
import com.husarp.lockdown.engine.LogRow
import com.husarp.lockdown.engine.MinuteRow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

/** Requests 2026-10-13: the Insights chart's labels, the launcher left out of screen time, the log's time range, Home's Island banner. */
class InsightsFixesTest {
    private val today = LocalDate.of(2026, 10, 13)          // a Tuesday

    // ---- chart labels ----

    @Test fun thirtyDaysLabelEveryFifthDayCountedBackFromToday() {
        val dates = (29 downTo 0).map { today.minusDays(it.toLong()) }
        val labels = ChartAxis.dayLabels(dates)
        assertEquals(30, labels.size)
        val shown = labels.withIndex().filter { it.value.isNotEmpty() }
        assertEquals(listOf(4, 9, 14, 19, 24, 29), shown.map { it.index })
        assertEquals("13", labels.last())                     // today always has one
        assertEquals(listOf("18", "23", "28", "3", "8", "13"), shown.map { it.value })
        assertTrue(shown.size <= ChartAxis.MAX_DAY_LABELS)
    }

    @Test fun sevenDaysLabelEveryDayWithItsWeekday() {
        val dates = (6 downTo 0).map { today.minusDays(it.toLong()) }
        assertEquals(listOf("W", "T", "F", "S", "S", "M", "T"), ChartAxis.dayLabels(dates))
    }

    @Test fun hoursLabelEverySixth() {
        val h = ChartAxis.hourLabels()
        assertEquals(24, h.size)
        assertEquals(listOf(0 to "00", 6 to "06", 12 to "12", 18 to "18"), h.withIndex().filter { it.value.isNotEmpty() }.map { it.index to it.value })
    }

    @Test fun labelStepKeepsAtMostSix() {
        assertEquals(1, ChartAxis.step(7))
        assertEquals(2, ChartAxis.step(8))
        assertEquals(5, ChartAxis.step(30))
        for (n in 8..60) assertTrue("n=$n", (0 until n).count { (n - 1 - it) % ChartAxis.step(n) == 0 } <= ChartAxis.MAX_DAY_LABELS)
    }

    // ---- the launcher isn't app use ----

    @Test fun theDefaultLauncherIsPicked() {
        assertEquals("org.lineageos.trebuchet", HomeApp.pick("org.lineageos.trebuchet", emptyList()))
        // no default chosen yet (Android's chooser): the only real launcher installed; Settings' fallback home is none
        assertEquals("org.lineageos.trebuchet", HomeApp.pick(HomeApp.RESOLVER, listOf("com.android.settings", "org.lineageos.trebuchet")))
        assertNull(HomeApp.pick(HomeApp.RESOLVER, listOf("org.lineageos.trebuchet", "com.teslacoilsw.launcher")))
        assertNull(HomeApp.pick(null, listOf("com.android.settings")))
        assertNull(HomeApp.pick("com.android.settings", emptyList()))
    }

    @Test fun launcherTimeIsLeftOutOfHistory() {
        val home = "org.lineageos.trebuchet"
        assertFalse(HomeApp.counts(home, home))
        assertTrue(HomeApp.counts("com.whatsapp", home))
        assertTrue(HomeApp.counts(home, null))                // launcher unknown: nothing is left out
        val at = today.atTime(9, 0)
        val d = DayDetail(rows = listOf(MinuteRow(at, home, "", 50, 50), MinuteRow(at, "com.whatsapp", "", 10, 10)))
        assertEquals(listOf("com.whatsapp"), HomeApp.detail(d, home).rows.map { it.app })
        val t = DayTotals(seconds = mapOf((home to "") to 3600, ("com.whatsapp" to "") to 60))
        assertEquals(60, HomeApp.totals(t, home).total)
        assertEquals(3660, HomeApp.totals(t, null).total)
    }

    // ---- activity log time range ----

    private fun row(day: LocalDate, h: Int) = LogRow(day.atTime(h, 0), LogKind.APP, "x")

    @Test fun rangePresetsCoverTheirDays() {
        assertEquals(today to today, LogRange.TODAY.days(today))
        assertEquals(today.minusDays(1) to today.minusDays(1), LogRange.YESTERDAY.days(today))
        assertEquals(today.minusDays(6) to today, LogRange.WEEK.days(today))
        val rows = listOf(row(today, 10), row(today.minusDays(1), 23), row(today.minusDays(1), 0), row(today.minusDays(5), 12))
        fun shown(r: LogRange) = r.days(today).let { (a, b) -> ActivityLog.view(rows, LogFilter.ALL, a, b) }
        assertEquals(1, shown(LogRange.TODAY).size)
        assertEquals(2, shown(LogRange.YESTERDAY).size)
        assertEquals(4, shown(LogRange.WEEK).size)
        assertEquals(4, ActivityLog.view(rows, LogFilter.ALL).size)   // no range: all
    }

    @Test fun pickedDaysAreOrderedAndKeptInsideTheLog() {
        assertEquals(today.minusDays(3) to today.minusDays(1), ActivityLog.clampDays(today.minusDays(1), today.minusDays(3), today))
        assertEquals(today.minusDays(6) to today, ActivityLog.clampDays(today.minusDays(20), today.plusDays(2), today))
    }

    // ---- Home's Island banner agrees with the notice ----

    @Test fun bannerIsUpExactlyWhileTheNoticeIs() {
        var fails = 0
        var posted = false
        repeat(3) {
            val (n, act) = IslandSight.noticeStep(fails, bad = true, screenOn = true)
            fails = n
            if (act == IslandSight.Notice.POST) posted = true
            assertEquals(posted, IslandSight.alertUp(fails, bad = true))
        }
        assertTrue(posted)
        // Island back: the notice goes, and so does the banner (at once, before the next check)
        assertFalse(IslandSight.alertUp(fails, bad = false))
        assertFalse(IslandSight.alertUp(1, bad = true))       // one late check-in is no reason
    }
}
