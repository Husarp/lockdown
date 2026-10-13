package com.husarp.lockdown

import com.husarp.lockdown.engine.ActivityLog
import com.husarp.lockdown.engine.AppSpan
import com.husarp.lockdown.engine.IslandOpen
import com.husarp.lockdown.engine.IslandOpens
import com.husarp.lockdown.engine.LogFilter
import com.husarp.lockdown.engine.LogKind
import com.husarp.lockdown.link.IslandLink
import com.husarp.lockdown.link.LinkProtocol
import com.husarp.lockdown.link.StateBody
import com.husarp.lockdown.link.SyncBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneOffset

/** Island apps opened: the helper's visits over the link (what is over, the mark, the cap) and main's merge into its log. */
class IslandOpensTest {
    private val utc = ZoneOffset.UTC
    private val t0 = LocalDateTime.of(2026, 10, 12, 10, 0).toInstant(utc).toEpochMilli()
    private val s = 1000L
    private val m = 60_000L
    private val name = { p: String -> p.replaceFirstChar { it.uppercase() } }
    private fun span(pkg: String, from: Long, sec: Long) = AppSpan(pkg, from, from + sec * s)
    private fun open(pkg: String, at: Long, end: Long = at + 60 * s, sec: Int = 60) = IslandOpen(pkg, name(pkg), at, end, sec)

    // ---------- the helper: what is over ----------

    @Test fun only_visits_that_are_over_and_long_enough_go() {
        val spans = listOf(
            span("yt", t0, 120), span("yt", t0 + 150 * s, 30),          // back within a minute: one visit of 150 s
            span("maps", t0 + 5 * m, 3),                                // a flicker
            span("mail", t0 + 10 * m, 40),                              // ended 30 s before now: may still go on
            span("lockdown", t0 + 2 * m, 60),                           // left out like in main's log
        )
        val now = t0 + 10 * m + 70 * s
        val out = IslandOpens.finished(spans, now, name) { it == "lockdown" }
        assertEquals(listOf(IslandOpen("yt", "Yt", t0, t0 + 180 * s, 150)), out)
        assertEquals(listOf("yt", "mail"), IslandOpens.finished(spans, now + m, name) { it == "lockdown" }.map { it.pkg })
    }

    @Test fun the_app_still_in_front_does_not_go_until_it_is_left() {
        val now = t0 + 30 * m
        val spans = listOf(span("yt", t0, 10 * 60), AppSpan("game", t0 + 20 * m, now))   // the open one is closed at "now"
        assertEquals(listOf("yt"), IslandOpens.finished(spans, now, name).map { it.pkg })
    }

    @Test fun a_long_label_is_cut() {
        val out = IslandOpens.finished(listOf(span("x", t0, 60)), t0 + 10 * m, { "L".repeat(200) })
        assertEquals(80, out.single().label.length)
    }

    // ---------- the mark: only what is new ----------

    @Test fun nothing_goes_while_the_mark_is_unknown() {
        assertEquals(emptyList<IslandOpen>(), IslandOpens.toSend(listOf(open("a", t0)), null))
    }

    @Test fun only_visits_that_ended_after_the_mark_go() {
        val all = listOf(open("a", t0), open("b", t0 + 2 * m), open("c", t0 + 4 * m))
        assertEquals(listOf("b", "c"), IslandOpens.toSend(all, t0 + 60 * s).map { it.pkg })
        assertEquals(emptyList<IslandOpen>(), IslandOpens.toSend(all, t0 + 4 * m + 60 * s))
        assertEquals(3, IslandOpens.toSend(all, 0).size)
    }

    @Test fun a_big_backlog_sends_the_latest_cap() {
        val all = (0 until 1200).map { open("p$it", t0 + it * m) }
        val out = IslandOpens.toSend(all, 0)
        assertEquals(IslandOpens.MAX_SEND, out.size)
        assertEquals("p700", out.first().pkg); assertEquals("p1199", out.last().pkg)
    }

    /** The helper and main over several check-ins: a visit that started earlier but ends later still arrives, a lost
     *  answer sends again without doubling, and each check-in carries only what is new. */
    @Test fun the_link_carries_each_visit_once() {
        var mainRows = emptyList<IslandOpen>()
        var helperMark: Long? = null                                                // not known before the first answer
        val sent = ArrayList<Int>()
        fun sync(spans: List<AppSpan>, now: Long, answerLost: Boolean = false) {
            val out = IslandOpens.toSend(IslandOpens.finished(spans, now, name), helperMark)
            sent.add(out.size)
            if (out.isNotEmpty()) mainRows = IslandOpens.merge(mainRows, IslandOpens.clean(out, now), now)
            if (!answerLost) helperMark = IslandOpens.mark(mainRows, now)
        }
        // "long" starts first and is left, "short" is used, then "long" comes back within a minute: one long visit
        val spans = listOf(span("long", t0, 60), span("short", t0 + 60 * s, 30), span("long", t0 + 100 * s, 300))
        sync(spans, t0 + 3 * m)                                                     // mark unknown: nothing
        sync(spans, t0 + 3 * m)                                                     // "short" is over; "long" isn't
        assertEquals(listOf("short"), mainRows.map { it.pkg })
        sync(spans, t0 + 8 * m, answerLost = true)                                  // "long" is over now
        sync(spans, t0 + 9 * m)                                                     // sent again: kept once
        sync(spans, t0 + 10 * m)                                                    // nothing new
        assertEquals(listOf(0, 1, 1, 1, 0), sent)
        assertEquals(listOf("short", "long"), mainRows.map { it.pkg })
        assertEquals(t0, mainRows.last().at)
        assertEquals(360, mainRows.last().sec)                                     // 60 s + 300 s
    }

    // ---------- main: what it keeps ----------

    @Test fun main_keeps_a_visit_once_drops_old_ones_and_keeps_the_latest() {
        val now = t0 + 30 * m
        val old = listOf(open("a", t0), open("old", now - IslandOpens.KEEP_MS - m))
        val merged = IslandOpens.merge(old, listOf(open("a", t0), open("b", t0 + 5 * m)), now)
        assertEquals(listOf("a", "b"), merged.map { it.pkg })
        val many = IslandOpens.merge(emptyList(), (0 until 1500).map { open("p$it", now - 7 * 86_400_000L + it * m) }, now)
        assertEquals(IslandOpens.MAX_KEEP, many.size)
        assertEquals("p1499", many.last().pkg)
    }

    @Test fun nonsense_from_the_link_is_dropped() {
        val now = t0 + 30 * m
        val rows = listOf(
            open("", t0), open("a", t0, sec = 0), open("a", t0, sec = 90_000), open("a", t0 + 2 * m, end = t0),   // no package, bad seconds, ends before it starts
            open("a", now - m, end = now + m), open("a", now - IslandOpens.KEEP_MS - m), open("x".repeat(201), t0),  // not over yet, too old, too long
            IslandOpen("ok", "L".repeat(100), t0, t0 + m, 60),
        )
        assertEquals(listOf(IslandOpen("ok", "L".repeat(80), t0, t0 + m, 60)), IslandOpens.clean(rows, now))
        assertEquals(IslandOpens.MAX_SEND, IslandOpens.clean((0 until 600).map { open("p$it", t0) }, now).size)
    }

    @Test fun the_mark_is_the_latest_end_up_to_now() {
        val now = t0 + 30 * m
        assertEquals(0L, IslandOpens.mark(emptyList(), now))
        assertEquals(t0 + 5 * m, IslandOpens.mark(listOf(open("a", t0, end = t0 + 5 * m), open("b", t0 + m, end = t0 + 2 * m)), now))
        assertEquals(t0 + m, IslandOpens.mark(listOf(open("a", t0, end = t0 + m), open("b", t0, end = now + m)), now))   // a clock set back
    }

    // ---------- main's Activity log ----------

    @Test fun island_visits_are_apps_named_with_island_in_the_log() {
        val at = LocalDateTime.of(2026, 10, 12, 10, 0)
        val main = ActivityLog.appRows(listOf(span("chrome", t0 + 5 * m, 120)), utc, name)
        val island = ActivityLog.islandRows(listOf(open("yt", t0, sec = 300), open("chrome", t0 + 10 * m)), utc)
        val all = ActivityLog.merge(LocalDate.of(2026, 10, 12), main, island)
        assertEquals(listOf("Chrome (Island)", "Chrome", "Yt (Island)"), all.map { it.name })
        assertTrue(all.all { it.kind == LogKind.APP })
        assertEquals(at, all.last().at)
        assertEquals("yt", all.last().pkg)
        assertEquals("Opened · 5 min", all.last().what)
        assertEquals(3, ActivityLog.view(all, LogFilter.APPS).size)
        assertEquals(0, ActivityLog.view(all, LogFilter.SITES).size)
    }

    // ---------- older copies ----------

    @Test fun older_copies_send_and_take_nothing() {
        val j = LinkProtocol.json
        val body = SyncBody(opens = listOf(open("a", t0)))
        assertEquals(body, j.decodeFromString(SyncBody.serializer(), j.encodeToString(SyncBody.serializer(), body)))
        assertNull(j.decodeFromString(SyncBody.serializer(), """{"seq":1}""").opens)                 // an older helper
        val olderMain = j.decodeFromString(StateBody.serializer(), """{"ack":3,"cfgHash":"h","app":5}""")
        assertNull(olderMain.opensMark)                                                                 // -> the helper sends none
        assertEquals(emptyList<IslandOpen>(), IslandOpens.toSend(listOf(open("a", t0)), olderMain.opensMark))
        assertEquals(42L, j.decodeFromString(StateBody.serializer(), j.encodeToString(StateBody.serializer(), StateBody(opensMark = 42))).opensMark)
        // a helper's saved state from before: no mark yet
        assertNull(j.decodeFromString(IslandLink.HelperState.serializer(), """{"secret":"s","port":38641}""").opensMark)
    }
}
