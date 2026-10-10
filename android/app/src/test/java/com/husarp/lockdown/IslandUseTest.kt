package com.husarp.lockdown

import com.husarp.lockdown.engine.AppTime
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
}
