package com.husarp.lockdown

import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.MinuteRow
import com.husarp.lockdown.engine.Stats
import com.husarp.lockdown.engine.SwitchEvent
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDate
import java.time.LocalDateTime

class StatsEngineTest {
    private val day = LocalDate.of(2026, 9, 28)
    private fun min(h: Int, m: Int) = LocalDateTime.of(2026, 9, 28, h, m)

    private val rows = listOf(
        MinuteRow(min(12, 0), "com.a", "", 60, 60),
        MinuteRow(min(12, 1), "com.a", "", 60, 60),
        MinuteRow(min(12, 2), "com.b", "x.com", 60, 60),
        MinuteRow(min(12, 10), "com.a", "", 60, 60),   // after an 8-min gap
    )

    @Test fun totals_and_breakdowns() {
        assertEquals(240 to 240, Stats.totals(rows))
        assertEquals(180, Stats.perApp(rows)["com.a"])
        assertEquals(60, Stats.perApp(rows)["com.b"])
        assertEquals(60, Stats.perSite(rows)["x.com"])
        assertEquals(240, Stats.perDay(rows)[day])
    }

    @Test fun categories_default_and_saved() {
        val items = listOf(Item("1", "A", "com.a", ItemType.APP))     // on the blocklist
        assertEquals("distracting", Stats.categoryOf("app", "com.a", emptyMap(), items))
        assertEquals("neutral", Stats.categoryOf("app", "com.other", emptyMap(), items))
        assertEquals("productive", Stats.categoryOf("app", "com.a", mapOf("app:com.a" to "productive"), items))
    }

    @Test fun sessions_split_on_a_long_gap() {
        assertEquals(2, Stats.sessions(rows).size)                    // 12:00-12:03 and 12:10-12:11
    }

    @Test fun longest_focus_is_the_longest_same_app_run() {
        val f = Stats.longestFocus(rows)!!
        assertEquals(120, f.first)
        assertEquals("com.a", f.second)
        assertEquals(min(12, 0), f.third)
    }

    @Test fun heatmap_levels() {
        val h = Stats.heatmap(rows, listOf(day))
        assertEquals(1, h[0][12])                                     // 4 active min in hour 12 -> level 1
        assertEquals(0, h[0][9])
    }

    @Test fun visits_and_switch_summary() {
        val events = listOf(
            SwitchEvent(LocalDateTime.of(2026, 9, 28, 12, 0, 0), "com.a", ""),
            SwitchEvent(LocalDateTime.of(2026, 9, 28, 12, 0, 20), "com.b", "x.com"),
            SwitchEvent(LocalDateTime.of(2026, 9, 28, 12, 5, 0), "com.a", ""),
        )
        val now = LocalDateTime.of(2026, 9, 28, 12, 10, 0)
        val s = Stats.switchSummary(events, now)
        assertEquals(3, s.count)
        assertEquals(1, s.short)                                      // the 20-second visit
        assertEquals("app" to "com.a", s.targets.first().first)       // most-visited target
        assertEquals(2, s.targets.first().second)                    // two visits to it
    }

    @Test fun visit_style_labels() {
        assertEquals("checking", Stats.visitStyle(12, 30.0))
        assertEquals("focused", Stats.visitStyle(1, 250.0))
        assertEquals("mixed", Stats.visitStyle(2, 160.0))
    }

    @Test fun streaks() {
        val days = listOf(day, day.minusDays(1), day.minusDays(2))
        val active = mapOf(day to 100, day.minusDays(1) to 100, day.minusDays(2) to 5000)
        assertEquals(2, Stats.goalStreak(active, days, goalSec = 200))
        assertEquals(2, Stats.noUnlockStreak(days, setOf(day.minusDays(2))))
    }

    @Test fun formatting() {
        assertEquals("4 h 12 m", Stats.hm(4 * 3600 + 12 * 60))
        assertEquals("52 m", Stats.hm(52 * 60))
        assertEquals("0 m", Stats.hm(0))
        assertEquals("1 m 46 s", Stats.ms(106))
        assertEquals("42 s", Stats.ms(42))
    }
}
