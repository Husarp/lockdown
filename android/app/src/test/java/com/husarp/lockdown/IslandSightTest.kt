package com.husarp.lockdown

import com.husarp.lockdown.engine.HelperWatch
import com.husarp.lockdown.engine.IslandFront
import com.husarp.lockdown.engine.IslandSight
import com.husarp.lockdown.engine.IslandSight.Ev
import com.husarp.lockdown.engine.IslandSight.Sight
import com.husarp.lockdown.link.Foreground
import com.husarp.lockdown.link.LinkProtocol
import com.husarp.lockdown.link.StateBody
import com.husarp.lockdown.link.SyncBody
import kotlinx.serialization.encodeToString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Main covers Island itself when its accessibility service sees Island apps; the Island copy then needs no
 *  foreground service (and so isn't in Android's "Active apps" list). */
class IslandSightTest {
    private val self = "com.husarp.lockdown"
    private val s = 1_000L
    private val t0 = 10_000_000L

    private fun res(ms: Long, pkg: String, cls: String = "A") = Ev(ms, Foreground.RESUMED, pkg, cls)
    private fun pau(ms: Long, pkg: String, cls: String = "A") = Ev(ms, Foreground.PAUSED, pkg, cls)

    // ---------- the Island copy's visits, from its usage events ----------

    @Test fun visits_follow_the_app_in_front() {
        val v = IslandSight.visits(listOf(
            res(t0, "yt"), pau(t0 + 10 * s, "yt"), res(t0 + 10 * s + 100, "ig"),
        ), now = t0 + 30 * s, self = self)
        assertEquals(listOf(IslandFront("yt", t0, t0 + 10 * s), IslandFront("ig", t0 + 10 * s + 100, t0 + 30 * s)), v)
    }

    @Test fun moving_between_screens_of_one_app_is_one_visit() {
        val v = IslandSight.visits(listOf(
            res(t0, "yt", "Main"), pau(t0 + 5 * s, "yt", "Main"), res(t0 + 5 * s, "yt", "Player"),
            Ev(t0 + 5 * s + 300, Foreground.STOPPED, "yt", "Main"),
        ), now = t0 + 20 * s, self = self)
        assertEquals(listOf(IslandFront("yt", t0, t0 + 20 * s)), v)
    }

    @Test fun lockdown_itself_and_short_visits_are_left_out() {
        val v = IslandSight.visits(listOf(
            res(t0, self), pau(t0 + 20 * s, self),
            res(t0 + 20 * s, "trampoline"), pau(t0 + 20 * s + 400, "trampoline"),
            res(t0 + 21 * s, "yt"), pau(t0 + 30 * s, "yt"),
        ), now = t0 + 60 * s, self = self)
        assertEquals(listOf(IslandFront("yt", t0 + 21 * s, t0 + 30 * s)), v)
    }

    @Test fun at_most_the_newest_sixty_visits() {
        val ev = (0 until 100).flatMap { i -> listOf(res(t0 + i * 10 * s, "app$i"), pau(t0 + i * 10 * s + 5 * s, "app$i")) }
        val v = IslandSight.visits(ev, now = t0 + 2000 * s, self = self)
        assertEquals(IslandSight.MAX_FRONTS, v.size)
        assertEquals("app99", v.last().pkg)
    }

    @Test fun main_accepts_only_sane_visits() {
        val c = IslandSight.clean(listOf(IslandFront("", 0, 5 * s), IslandFront("ok", 10 * s, 5 * s),
            IslandFront("x".repeat(300), 0, 5 * s), IslandFront("long", 0, 2 * 86_400_000L), IslandFront("yt", t0, t0 + 5 * s)))
        assertEquals(listOf(IslandFront("yt", t0, t0 + 5 * s)), c)
    }

    // ---------- main: did its own service hear the Island app? ----------

    private val visit = IslandFront("yt", t0, t0 + 20 * s)

    @Test fun heard_right_after_it_came_up_is_seen() {
        val log = listOf("launcher" to t0 - 2 * s, "yt" to t0 + 150, "yt" to t0 + 1200)
        assertEquals(true, IslandSight.seen(visit, log, since = 0, self = self))
    }

    @Test fun only_main_s_own_apps_around_it_is_a_miss() {
        // main's service heard the launcher (Island apps are opened from it) but never the Island app
        val log = listOf("launcher" to t0 - 1 * s, "launcher" to t0 + 25 * s)
        assertEquals(false, IslandSight.seen(visit, log, since = 0, self = self))
    }

    @Test fun nothing_heard_around_it_tells_nothing() {
        // screen off, or main's service down then: neither seen nor missed
        val log = listOf("launcher" to t0 - 5 * 60 * s)
        assertNull(IslandSight.seen(visit, log, since = 0, self = self))
    }

    @Test fun before_main_was_listening_tells_nothing() {
        val log = listOf("yt" to t0 + 150)
        assertNull(IslandSight.seen(visit, log, since = t0 - 5 * s, self = self))
    }

    @Test fun lockdown_itself_and_a_short_visit_tell_nothing() {
        val log = listOf(self to t0 + 100, "yt" to t0 + 100)
        assertNull(IslandSight.seen(IslandFront(self, t0, t0 + 20 * s), log, 0, self))
        assertNull(IslandSight.seen(IslandFront("yt", t0, t0 + 2 * s), log, 0, self))
    }

    @Test fun the_same_app_used_in_main_just_before_is_not_seeing_island() {
        // personal YouTube left 2 s before Island's YouTube came up: those events are main's own, not Island's
        val log = listOf("yt" to t0 - 2 * s, "launcher" to t0 - 1 * s)
        assertEquals(false, IslandSight.seen(visit, log, since = 0, self = self))
    }

    @Test fun only_the_first_fifteen_seconds_of_a_visit_are_looked_at() {
        val log = listOf("launcher" to t0 - s, "yt" to t0 + 18 * s)
        assertEquals(false, IslandSight.seen(visit, log, since = 0, self = self))
    }

    // ---------- main's verdict ----------

    private fun Sight.then(vararg r: Boolean) = IslandSight.update(this, r.toList())

    @Test fun three_seen_in_a_row_and_main_takes_over() {
        var x = Sight()
        x = x.then(true); assertFalse(x.covers)
        x = x.then(true); assertFalse(x.covers)
        x = x.then(true); assertTrue(x.covers)
    }

    @Test fun a_miss_before_it_takes_over_holds_it_back_until_it_leaves_the_window() {
        var x = Sight().then(false, true, true, true)
        assertFalse(x.covers)
        repeat(6) { x = x.then(true); assertFalse(x.covers) }    // the miss is still among the last 10
        x = x.then(true)
        assertTrue(x.covers)
    }

    @Test fun one_stray_miss_is_kept_two_hand_back() {
        var x = Sight().then(true, true, true)
        x = x.then(false); assertTrue(x.covers)
        x = x.then(true, true); assertTrue(x.covers)
        x = x.then(false); assertFalse(x.covers)
    }

    @Test fun losing_sight_hands_back_at_the_second_miss() {
        var x = Sight().then(true, true, true, true, true)
        x = x.then(false); assertTrue(x.covers)
        x = x.then(false); assertFalse(x.covers)
    }

    @Test fun nothing_judged_changes_nothing() {
        val x = Sight("111", true)
        assertEquals(x, IslandSight.update(x, emptyList()))
    }

    @Test fun main_covers_only_linked_and_with_its_service_running() {
        val yes = Sight("111", true)
        assertTrue(IslandSight.mainCovers(linked = true, serviceUp = true, s = yes))
        assertFalse(IslandSight.mainCovers(linked = true, serviceUp = false, s = yes))
        assertFalse(IslandSight.mainCovers(linked = false, serviceUp = true, s = yes))
        assertFalse(IslandSight.mainCovers(linked = true, serviceUp = true, s = Sight("11", false)))
    }

    // ---------- the Island copy: service or not, and who counts ----------

    @Test fun the_island_copy_drops_its_service_only_when_main_answered_that_it_covers() {
        assertFalse(IslandSight.helperNeedsService(syncOk = true, mainCovers = true))
        assertTrue(IslandSight.helperNeedsService(syncOk = true, mainCovers = false))
        assertTrue(IslandSight.helperNeedsService(syncOk = false, mainCovers = true))     // main unreachable: the old way
        assertTrue(IslandSight.helperNeedsService(syncOk = false, mainCovers = false))
    }

    @Test fun a_minute_is_never_counted_by_both() {
        // covering: main counts what its service sees (Island apps too), the Island copy counts nothing
        assertFalse(IslandSight.helperCounts(mainCovers = true))
        // not covering: the Island copy counts Island apps (main can't see them)
        assertTrue(IslandSight.helperCounts(mainCovers = false))
    }

    @Test fun a_phone_whose_main_sees_island_takes_over_and_one_that_does_not_keeps_the_service() {
        val visits = (0 until 3).map { IslandFront("yt", t0 + it * 60 * s, t0 + it * 60 * s + 20 * s) }
        val sees = visits.flatMap { listOf("launcher" to it.from - s, "yt" to it.from + 200) }
        val blind = visits.map { "launcher" to it.from - s }
        fun verdict(log: List<Pair<String, Long>>) =
            IslandSight.update(Sight(), visits.mapNotNull { IslandSight.seen(it, log, 0, self) })
        val a = verdict(sees)
        assertTrue(a.covers)
        assertFalse(IslandSight.helperNeedsService(true, IslandSight.mainCovers(true, true, a)))
        val b = verdict(blind)
        assertFalse(b.covers)
        assertTrue(IslandSight.helperNeedsService(true, IslandSight.mainCovers(true, true, b)))
    }

    // ---------- main's log of what its service heard ----------

    @Test fun the_log_keeps_one_entry_per_app_per_second() {
        val log = IslandSight.Log()
        log.add("yt", t0); log.add("yt", t0 + 300); log.add("ig", t0 + 400); log.add("yt", t0 + 1000)
        assertEquals(listOf("yt" to t0, "ig" to t0 + 400, "yt" to t0 + 1000), log.snapshot().first)
        assertEquals(t0, log.snapshot().second)
    }

    @Test fun the_log_drops_old_entries_and_says_from_when_it_is_complete() {
        val log = IslandSight.Log(keepMs = 60 * s, max = 100)
        log.add("a", t0); log.add("b", t0 + 30 * s); log.add("c", t0 + 61 * s)
        assertEquals(listOf("b" to t0 + 30 * s, "c" to t0 + 61 * s), log.snapshot().first)
        assertEquals(t0, log.snapshot().second)
        val capped = IslandSight.Log(keepMs = 60 * 60 * s, max = 2)
        capped.add("a", t0); capped.add("b", t0 + s); capped.add("c", t0 + 2 * s)
        assertEquals(t0, capped.snapshot().second)                // what's before the oldest kept one isn't known
        capped.add("d", t0 + 3 * s)
        assertEquals(t0 + s, capped.snapshot().second)
        log.clear()
        assertEquals(Long.MAX_VALUE, log.snapshot().second)
    }

    @Test fun an_app_dropped_from_the_log_is_logged_again_at_once() {
        val log = IslandSight.Log(keepMs = 60 * 60 * s, max = 1)
        log.add("a", t0); log.add("b", t0 + 100); log.add("a", t0 + 200)
        assertEquals(listOf("a" to t0 + 200), log.snapshot().first)
    }

    // ---------- the link: new fields, older copies ----------

    @Test fun visits_and_the_verdict_travel_and_an_older_main_means_the_service_stays() {
        val j = LinkProtocol.json
        val body = SyncBody(fronts = listOf(IslandFront("yt", t0, t0 + 5 * s)))
        assertEquals(body, j.decodeFromString<SyncBody>(j.encodeToString(body)))
        val older = j.decodeFromString<StateBody>("""{"ack":3,"cfgHash":"h","app":5}""")     // no "covers" field
        assertFalse(older.covers)
        assertTrue(IslandSight.helperNeedsService(syncOk = true, mainCovers = older.covers))
        assertTrue(j.decodeFromString<StateBody>(j.encodeToString(StateBody(covers = true))).covers)
    }

    // ---------- main's watch on a quiet Island copy ----------

    @Test fun covering_a_copy_that_checks_in_every_fifteen_minutes_is_never_called_stopped() {
        var st = HelperWatch.State(); var now = t0; var heard = t0
        repeat(240) { i ->
            if (i % 15 == 0) heard = now
            val out = HelperWatch.look(st, now, heard, true, true, IslandSight.quietMs(mainCovers = true))
            st = out.state
            assertEquals(HelperWatch.Act.NONE, out.act)
            now += 60 * s
        }
    }

    @Test fun covering_a_copy_quiet_for_45_minutes_of_screen_time_is_raised() {
        var st = HelperWatch.State(); var now = t0
        var raisedAt = -1
        for (i in 0..50) {
            val out = HelperWatch.look(st, now, 5, true, true, IslandSight.quietMs(mainCovers = true))
            st = out.state
            if (out.act == HelperWatch.Act.RAISE) { raisedAt = i; break }
            now += 60 * s
        }
        assertEquals(45, raisedAt)
    }

    @Test fun when_main_stops_covering_a_quiet_copy_is_raised_at_once() {
        // 10 min since its last check-in was fine while main covered; not covering, it is 2 min
        var st = HelperWatch.State(); var now = t0
        repeat(11) { st = HelperWatch.look(st, now, 5, true, true, IslandSight.quietMs(true)).state; now += 60 * s }
        assertEquals(HelperWatch.Act.RAISE, HelperWatch.look(st, now, 5, true, true, IslandSight.quietMs(false)).act)
    }

    @Test fun checked_in_lately_is_an_hour_while_main_covers() {
        assertEquals(10 * 60_000L, IslandSight.seenWithinMs(false))
        assertEquals(60 * 60_000L, IslandSight.seenWithinMs(true))
    }

    // ---------- "checked in lately" and the "Island apps not covered" notice ----------

    private val min = 60_000L

    @Test fun a_copy_told_main_covers_keeps_the_hour_after_main_stops_covering() {
        // told "covered" at its last check-in, it sleeps until its next 15-min wake: 14 min later is not "not checking in"
        assertTrue(IslandSight.checkedInLately(14 * min, toldCovers = true, coversNow = false))
        assertTrue(IslandSight.checkedInLately(59 * min, toldCovers = true, coversNow = false))
        assertFalse(IslandSight.checkedInLately(60 * min, toldCovers = true, coversNow = false))
    }

    @Test fun a_copy_told_main_doesnt_cover_has_ten_minutes() {
        assertTrue(IslandSight.checkedInLately(9 * min, toldCovers = false, coversNow = false))
        assertFalse(IslandSight.checkedInLately(10 * min, toldCovers = false, coversNow = false))
        // main took over since (it covers now): the hour
        assertTrue(IslandSight.checkedInLately(30 * min, toldCovers = false, coversNow = true))
    }

    @Test fun a_clock_set_back_is_not_a_fresh_check_in() {
        assertFalse(IslandSight.checkedInLately(-1, toldCovers = true, coversNow = true))
    }

    @Test fun main_stopping_to_cover_raises_no_notice_before_the_copy_can_have_heard() {
        // check-in at 0 (told covered), main stops covering at 5, the worker checks at 14 and 29; the copy wakes at 15,
        // hears main stopped covering and starts its service (a check-in every 30 s from then on)
        var fails = 0
        for ((at, lastSeen) in listOf(14 * min to 0L, 29 * min to 29 * min - 30_000L)) {
            val bad = !IslandSight.checkedInLately(at - lastSeen, toldCovers = lastSeen == 0L, coversNow = false)
            val (n, act) = IslandSight.noticeStep(fails, bad, screenOn = true)
            assertFalse(bad); assertEquals(IslandSight.Notice.CANCEL, act)
            fails = n
        }
    }

    @Test fun the_notice_comes_only_after_two_failed_checks_in_a_row() {
        val (one, a1) = IslandSight.noticeStep(0, bad = true, screenOn = true)
        assertEquals(1, one); assertEquals(IslandSight.Notice.NONE, a1)
        val (two, a2) = IslandSight.noticeStep(one, bad = true, screenOn = true)
        assertEquals(2, two); assertEquals(IslandSight.Notice.POST, a2)
        // still bad: it stays up
        assertEquals(IslandSight.Notice.POST, IslandSight.noticeStep(two, bad = true, screenOn = true).second)
    }

    @Test fun a_passing_check_takes_the_notice_down_and_starts_the_count_again() {
        assertEquals(0 to IslandSight.Notice.CANCEL, IslandSight.noticeStep(3, bad = false, screenOn = true))
        // fail, pass, fail: never two in a row, never up
        var fails = 0
        for (bad in listOf(true, false, true, false)) {
            val (n, act) = IslandSight.noticeStep(fails, bad, screenOn = true)
            assertTrue(act != IslandSight.Notice.POST)
            fails = n
        }
    }

    @Test fun a_check_with_the_screen_off_counts_neither_way() {
        assertEquals(1 to IslandSight.Notice.NONE, IslandSight.noticeStep(1, bad = true, screenOn = false))
        assertEquals(0 to IslandSight.Notice.NONE, IslandSight.noticeStep(0, bad = true, screenOn = false))
        // but a pass takes it down with the screen off too
        assertEquals(0 to IslandSight.Notice.CANCEL, IslandSight.noticeStep(2, bad = false, screenOn = false))
    }

    // ---------- an Island browser's site, while the Island copy counts it ----------

    @Test fun an_island_browsers_site_comes_only_from_its_bar_read_just_now() {
        val chrome = "com.android.chrome"
        assertEquals("example.com", IslandSight.islandSite(chrome, "https://www.example.com/page", privateTab = false))
        // main couldn't read that window: no site, never the address it kept (that may be the personal copy's)
        assertEquals("", IslandSight.islandSite(chrome, null, privateTab = false))
        // a search phrase isn't a site; a private tab's site isn't kept (the browser is)
        assertEquals("", IslandSight.islandSite(chrome, "cheap flights", privateTab = false))
        assertEquals("", IslandSight.islandSite(chrome, "example.com", privateTab = true))
    }
}
