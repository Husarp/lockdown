package com.husarp.lockdown

import com.husarp.lockdown.engine.ActivityLog
import com.husarp.lockdown.engine.AppSpan
import com.husarp.lockdown.engine.BlockedVisit
import com.husarp.lockdown.engine.EmergencyUse
import com.husarp.lockdown.engine.LogFilter
import com.husarp.lockdown.engine.LogItem
import com.husarp.lockdown.engine.LogKind
import com.husarp.lockdown.engine.LogRow
import com.husarp.lockdown.engine.SwitchEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneOffset

/** The activity log page: how apps, sites, blocks and emergency uses become rows, are merged, filtered and grouped. */
class ActivityLogTest {
    private val utc = ZoneOffset.UTC
    private val today = LocalDate.of(2026, 9, 28)
    private fun at(h: Int, m: Int, s: Int = 0, day: Int = 28) = LocalDateTime.of(2026, 9, day, h, m, s)
    private fun ms(t: LocalDateTime) = t.toInstant(utc).toEpochMilli()
    private fun span(pkg: String, from: LocalDateTime, sec: Int) = AppSpan(pkg, ms(from), ms(from) + sec * 1000L)
    private val name = { p: String -> p.substringAfterLast('.').replaceFirstChar { it.uppercase() } }

    // ---------- apps ----------

    @Test fun an_app_that_comes_back_within_a_minute_is_one_visit_with_its_times_added() {
        val rows = ActivityLog.appRows(listOf(
            span("com.whatsapp", at(9, 0), 300), span("com.whatsapp", at(9, 5, 20), 420),     // 20 s gap: the same visit
            span("com.whatsapp", at(10, 0), 60),                                              // an hour later: another
        ), utc, name)
        assertEquals(listOf(at(10, 0), at(9, 0)), rows.map { it.at })
        assertEquals(listOf(60, 720), rows.map { it.seconds })
        assertEquals("Opened · 12 min", rows[1].what)
        assertEquals("Whatsapp", rows[1].name); assertEquals("com.whatsapp", rows[1].pkg)
    }

    @Test fun flickers_and_skipped_apps_are_left_out() {
        val rows = ActivityLog.appRows(listOf(
            span("com.android.chrome", at(9, 0), 3), span("com.husarp.lockdown", at(9, 1), 600), span("com.android.systemui", at(9, 2), 600),
            span("com.whatsapp", at(9, 3), 90),
        ), utc, name) { it == "com.husarp.lockdown" || it == "com.android.systemui" }
        assertEquals(listOf("com.whatsapp"), rows.map { it.pkg })
    }

    // ---------- sites ----------

    @Test fun a_site_visit_lasts_until_the_next_event_and_names_its_browser() {
        val ev = listOf(
            SwitchEvent(at(9, 0), "com.android.chrome", "youtube.com"),
            SwitchEvent(at(9, 5), "com.android.chrome", "reddit.com"),
            SwitchEvent(at(9, 6), "", "", switch = false),                   // the visit ended (screen off)
            SwitchEvent(at(11, 0), "com.whatsapp", ""),                      // an app: not a site
            SwitchEvent(at(11, 1), "org.mozilla.firefox", "wikipedia.org"),
        )
        val rows = ActivityLog.siteRows(ev, at(11, 10), name)
        assertEquals(listOf("wikipedia.org", "reddit.com", "youtube.com"), rows.map { it.name })
        assertEquals(listOf(540, 60, 300), rows.map { it.seconds })
        assertEquals("Visited in Chrome · 5 min", rows[2].what)
        assertEquals("Visited in Firefox · 9 min", rows[0].what)
        assertTrue(rows.all { it.kind == LogKind.SITE && it.pkg.isEmpty() })
    }

    @Test fun a_visit_with_no_end_is_cut_and_never_runs_past_now() {
        val ev = listOf(SwitchEvent(at(1, 0), "com.android.chrome", "a.com"), SwitchEvent(at(20, 0), "com.android.chrome", "b.com"))
        val rows = ActivityLog.siteRows(ev, at(20, 0, 30), name)
        assertEquals(3 * 3600, rows.first { it.name == "a.com" }.seconds)           // a missing end: capped
        assertEquals(30, rows.first { it.name == "b.com" }.seconds)                  // the last one runs up to now
        assertEquals(emptyList<LogRow>(), ActivityLog.siteRows(listOf(SwitchEvent(at(9, 0), "com.android.chrome", "a.com")), at(9, 0, 3), name))
    }

    // ---------- blocks and emergency ----------

    @Test fun blocks_show_the_app_or_the_site_and_say_blocked() {
        val b = listOf(BlockedVisit(at(8, 0), "i1", "com.instagram.android"), BlockedVisit(at(8, 30), "i2", "reddit.com"), BlockedVisit(at(8, 40), "", "gambling"))
        val rows = ActivityLog.blockRows(b, { v -> v.target.takeIf { v.itemId == "i1" } }, { "Instagram" })
        assertEquals(listOf("gambling", "reddit.com", "Instagram"), rows.map { it.name })
        assertEquals(listOf("", "", "com.instagram.android"), rows.map { it.pkg })
        assertTrue(rows.all { it.what == "Blocked" && it.kind == LogKind.BLOCK })
    }

    @Test fun emergency_uses_show_how_long_they_freed_things_and_old_days_are_left_out() {
        val uses = listOf(
            EmergencyUse(at(14, 0).toString(), "t1", at(14, 20).toString()),
            EmergencyUse(at(7, 0, day = 20).toString(), "t1", at(7, 20, day = 20).toString()),     // older than the window
            EmergencyUse(at(15, 0).toString(), "t1", at(15, 0).toString()),                        // from before kinds: no length
            EmergencyUse("not a time", "t1", "x"),
        )
        val rows = ActivityLog.emergencyRows(uses, today.minusDays(6))
        assertEquals(listOf(at(15, 0), at(14, 0)), rows.map { it.at })
        assertEquals(listOf("Emergency used", "Emergency used · 20 min"), rows.map { it.what })
    }

    // ---------- merge / filter / group ----------

    private fun row(t: LocalDateTime, kind: LogKind, n: String = "x") = LogRow(t, kind, n)

    @Test fun everything_is_merged_newest_first_inside_the_last_seven_days() {
        val a = listOf(row(at(9, 0), LogKind.APP, "a"), row(at(9, 0, day = 22), LogKind.APP, "oldest in range"), row(at(9, 0, day = 21), LogKind.APP, "too old"))
        val s = listOf(row(at(10, 0), LogKind.SITE, "s"), row(at(10, 0, day = 29), LogKind.SITE, "tomorrow"))
        val b = listOf(row(at(8, 0), LogKind.BLOCK, "b"))
        val merged = ActivityLog.merge(today, a, s, b)
        assertEquals(listOf("s", "a", "b", "oldest in range"), merged.map { it.name })
    }

    @Test fun the_chips_pick_their_kinds_and_blocks_include_emergency_uses() {
        val rows = listOf(row(at(12, 0), LogKind.APP), row(at(11, 0), LogKind.SITE), row(at(10, 0), LogKind.BLOCK), row(at(9, 0), LogKind.EMERGENCY))
        assertEquals(4, ActivityLog.view(rows, LogFilter.ALL).size)
        assertEquals(listOf(LogKind.APP), ActivityLog.view(rows, LogFilter.APPS).map { it.kind })
        assertEquals(listOf(LogKind.SITE), ActivityLog.view(rows, LogFilter.SITES).map { it.kind })
        assertEquals(listOf(LogKind.BLOCK, LogKind.EMERGENCY), ActivityLog.view(rows, LogFilter.BLOCKS).map { it.kind })
        assertEquals(listOf("All", "Apps", "Sites", "Blocks"), LogFilter.entries.map { it.label })
    }

    @Test fun the_list_is_capped_and_keeps_the_newest() {
        val rows = (0 until 800).map { row(at(0, 0).plusSeconds(it.toLong()), LogKind.APP, "r$it") }.sortedByDescending { it.at }
        val shown = ActivityLog.view(rows, LogFilter.ALL)
        assertEquals(ActivityLog.MAX_ROWS, shown.size)
        assertEquals("r799", shown.first().name)
    }

    @Test fun a_heading_comes_before_each_day() {
        val rows = listOf(row(at(9, 0), LogKind.APP, "a"), row(at(8, 0), LogKind.SITE, "b"), row(at(23, 0, day = 27), LogKind.BLOCK, "c"))
        val flat = ActivityLog.flat(rows)
        assertEquals(5, flat.size)
        assertEquals(LogItem.Day(LocalDate.of(2026, 9, 28)), flat[0])
        assertEquals(LogItem.Entry(rows[1]), flat[2])
        assertEquals(LogItem.Day(LocalDate.of(2026, 9, 27)), flat[3])
        assertEquals(emptyList<LogItem>(), ActivityLog.flat(emptyList()))
    }

    @Test fun day_headings_and_lengths_read_plainly() {
        assertEquals("Today", ActivityLog.dayLabel(today, today))
        assertEquals("Yesterday", ActivityLog.dayLabel(today.minusDays(1), today))
        assertEquals("Fri 25 Sep", ActivityLog.dayLabel(LocalDate.of(2026, 9, 25), today))
        assertEquals("40 s", ActivityLog.span(40)); assertEquals("12 min", ActivityLog.span(720)); assertEquals("1 h 05 min", ActivityLog.span(3900))
        assertFalse(row(at(9, 0), LogKind.APP).what.contains("·"))
    }

    @Test fun the_cap_per_source_keeps_the_newest_rows() {
        val spans = (0 until 600).map { span("com.app$it", at(0, 0).plusMinutes(it.toLong() * 2), 30) }
        val rows = ActivityLog.appRows(spans, utc, name)
        assertEquals(ActivityLog.MAX_PER_SOURCE, rows.size)
        assertTrue(rows.zipWithNext().all { (x, y) -> !x.at.isBefore(y.at) })
    }
}
