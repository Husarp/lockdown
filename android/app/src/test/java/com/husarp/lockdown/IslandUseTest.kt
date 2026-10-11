package com.husarp.lockdown

import com.husarp.lockdown.engine.AppTime
import com.husarp.lockdown.engine.IslandHourRow
import com.husarp.lockdown.engine.IslandUse
import com.husarp.lockdown.engine.IslandUseRow
import com.husarp.lockdown.link.LinkProtocol
import com.husarp.lockdown.link.SyncBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId

class IslandUseTest {
    private val zone = ZoneId.of("Europe/Warsaw")
    private val today = LocalDate.of(2026, 10, 10)
    private fun ms(day: String, h: Int, m: Int = 0) = LocalDate.parse(day).atTime(h, m).atZone(zone).toInstant().toEpochMilli()

    @Test fun a_span_over_midnight_is_split_by_day() {
        val out = HashMap<Pair<String, String>, Long>()
        IslandUse.addSpan(out, "a", ms("2026-10-09", 23, 30), ms("2026-10-10", 0, 20), zone)
        assertEquals(30 * 60_000L, out["2026-10-09" to "a"])
        assertEquals(20 * 60_000L, out["2026-10-10" to "a"])
    }

    @Test fun island_only_apps_are_labelled_and_the_same_package_adds_up() {
        val main = listOf(AppTime("chrome", "Chrome", 600_000), AppTime("mail", "Mail", 60_000))
        val island = mapOf("chrome" to AppTime("chrome", "Chrome", 300_000), "game" to AppTime("game", "Game", 950_000))
        val top = IslandUse.topApps(main, island)
        assertEquals(listOf("game", "chrome", "mail"), top.map { it.pkg })
        assertEquals("Game (Island)", top[0].label)
        assertEquals(900_000L, top[1].ms)
        assertEquals("Chrome", top[1].label)
    }

    @Test fun incoming_days_replace_and_other_days_stay() {
        val old = listOf(IslandUseRow("2026-10-01", "a", "A", 100), IslandUseRow("2026-10-09", "a", "A", 50), IslandUseRow("2024-01-01", "a", "A", 5))
        val inc = listOf(IslandUseRow("2026-10-09", "a", "A", 80), IslandUseRow("2026-10-10", "b", "B", 7))
        val m = IslandUse.merge(old, inc, today)
        assertEquals(listOf(100, 80, 7), m.map { it.sec })          // the old 2024 day is dropped
        val t = IslandUse.total(m, LocalDate.of(2026, 10, 9), today)
        assertEquals(80_000L, t["a"]!!.ms); assertEquals(7_000L, t["b"]!!.ms)
    }

    @Test fun nonsense_from_the_link_is_dropped() {
        val rows = listOf(IslandUseRow("x", "a", "A", 5), IslandUseRow("2026-10-10", "a", "A", 90_000),
            IslandUseRow("2026-10-10", "a", "A", -4), IslandUseRow("2026-10-10", "", "A", 4), IslandUseRow("2026-10-10", "ok", "A", 4))
        assertEquals(listOf("ok"), IslandUse.clean(rows).map { it.pkg })
    }

    @Test fun old_and_new_sync_bodies_still_read_each_other() {
        val newBody = LinkProtocol.json.encodeToString(SyncBody.serializer(), SyncBody(use = listOf(IslandUseRow("2026-10-10", "a", "A", 5))))
        assertEquals(1, LinkProtocol.json.decodeFromString(SyncBody.serializer(), newBody).use!!.size)
        // an older helper sends no "use"; an older main would skip the unknown field (ignoreUnknownKeys)
        assertNull(LinkProtocol.json.decodeFromString(SyncBody.serializer(), """{"seq":1}""").use)
        assertEquals(1L, LinkProtocol.json.decodeFromString(SyncBody.serializer(), """{"seq":1,"use":[],"future":3}""").seq)
    }

    private fun row(day: String, pkg: String, sec: Int) = IslandUseRow(day, pkg, pkg, sec)

    @Test fun per_day_adds_all_apps_of_each_day() {
        val rows = listOf(row("2026-10-09", "a", 60), row("2026-10-09", "b", 30), row("2026-10-10", "a", 5))
        assertEquals(listOf(90_000L, 5_000L, 0L), IslandUse.perDay(rows, listOf(today.minusDays(1), today, today.plusDays(1))))
    }

    @Test fun history_that_already_has_the_island_app_is_not_counted_twice() {
        val d = today
        val hist = mapOf(d to mapOf(("a" to "") to 100, ("chrome" to "x.com") to 40, ("chrome" to "y.com") to 20))
        val out = IslandUse.fillTotals(hist, listOf(row("2026-10-10", "a", 80), row("2026-10-10", "chrome", 50)))
        assertEquals(hist, out)                                                     // history already has more
    }

    @Test fun only_the_part_history_missed_is_added_and_new_days_come_in_whole() {
        val hist = mapOf(today to mapOf(("a" to "") to 60))
        val out = IslandUse.fillTotals(hist, listOf(row("2026-10-10", "a", 100), row("2026-10-10", "b", 30), row("2026-10-08", "a", 20)))
        assertEquals(mapOf(("a" to "") to 100, ("b" to "") to 30), out[today])
        assertEquals(mapOf(("a" to "") to 20), out[LocalDate.of(2026, 10, 8)])
        assertEquals(60, hist[today]!![("a" to "")])                                // input untouched
        assertEquals(hist, IslandUse.fillTotals(hist, emptyList()))
    }

    @Test fun hour_spans_split_at_hour_and_midnight_and_skip_before_since() {
        val out = HashMap<Pair<String, Int>, Long>()
        IslandUse.addHourSpan(out, ms("2026-10-09", 23, 50), ms("2026-10-10", 1, 10), 0, zone)
        assertEquals(10 * 60_000L, out["2026-10-09" to 23])
        assertEquals(60 * 60_000L, out["2026-10-10" to 0])
        assertEquals(10 * 60_000L, out["2026-10-10" to 1])
        val late = HashMap<Pair<String, Int>, Long>()
        IslandUse.addHourSpan(late, ms("2026-10-09", 23, 50), ms("2026-10-10", 0, 20), ms("2026-10-10", 0), zone)
        assertEquals(mapOf("2026-10-10" to 0 to 20 * 60_000L), late)
        IslandUse.addHourSpan(late, ms("2026-10-10", 5), ms("2026-10-10", 5), 0, zone)    // empty span adds nothing
        assertEquals(1, late.size)
    }

    @Test fun hour_rows_are_capped_at_an_hour_and_tiny_ones_dropped() {
        val rows = IslandUse.hourRows(mapOf(("2026-10-10" to 3) to 5_000_000L, ("2026-10-10" to 2) to 1500L, ("2026-10-10" to 1) to 900L))
        assertEquals(listOf(IslandHourRow("2026-10-10", 2, 1), IslandHourRow("2026-10-10", 3, 3600)), rows)
    }

    @Test fun nonsense_hour_rows_are_dropped_and_count_is_capped() {
        val rows = listOf(IslandHourRow("x", 1, 5), IslandHourRow("2026-10-10", 24, 5), IslandHourRow("2026-10-10", -1, 5),
            IslandHourRow("2026-10-10", 3, 0), IslandHourRow("2026-10-10", 3, 3601), IslandHourRow("2026-10-10", 3, 60))
        assertEquals(listOf(3), IslandUse.cleanHours(rows).map { it.hour })
        assertEquals(IslandUse.MAX_HOUR_ROWS, IslandUse.cleanHours(List(500) { IslandHourRow("2026-10-10", it % 24, 5) }).size)
    }

    @Test fun hour_merge_replaces_sent_days_and_hour_ms_reads_one_day() {
        val old = listOf(IslandHourRow("2026-10-09", 8, 100), IslandHourRow("2026-10-10", 8, 50), IslandHourRow("2024-01-01", 1, 5))
        val m = IslandUse.mergeHours(old, listOf(IslandHourRow("2026-10-10", 9, 30)), today)
        assertEquals(listOf(8 to 100, 9 to 30), m.map { it.hour to it.sec })
        val h = IslandUse.hourMs(m, today)!!
        assertEquals(24, h.size); assertEquals(30_000L, h[9]); assertEquals(0L, h[8])
        assertNull(IslandUse.hourMs(m, LocalDate.of(2026, 10, 1)))
    }

    @Test fun sync_body_with_hours_reads_both_ways() {
        val b = LinkProtocol.json.encodeToString(SyncBody.serializer(), SyncBody(useHours = listOf(IslandHourRow("2026-10-10", 1, 5))))
        assertEquals(1, LinkProtocol.json.decodeFromString(SyncBody.serializer(), b).useHours!!.size)
        assertNull(LinkProtocol.json.decodeFromString(SyncBody.serializer(), """{"seq":1}""").useHours)
    }
}
