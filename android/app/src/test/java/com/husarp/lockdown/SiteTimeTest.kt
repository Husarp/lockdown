package com.husarp.lockdown

import com.husarp.lockdown.engine.AppTime
import com.husarp.lockdown.engine.Charge
import com.husarp.lockdown.engine.FrontSplit
import com.husarp.lockdown.engine.MinuteAcc
import com.husarp.lockdown.engine.MinuteRow
import com.husarp.lockdown.engine.SiteTop
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.LocalDateTime

/** Time per site: how the seconds are split between sites / apps, and how Insights and the CSV show them. */
class SiteTimeTest {
    private val chrome = "com.android.chrome"
    private fun at(h: Int, m: Int, s: Int = 0, day: Int = 28) = LocalDateTime.of(2026, 9, day, h, m, s)

    private fun total(cs: List<Charge>, app: String, site: String) = cs.filter { it.app == app && it.site == site }.sumOf { it.seconds }

    // ---------- FrontSplit ----------

    @Test fun the_same_site_keeps_getting_its_time() {
        val s = FrontSplit()
        s.step(at(12, 0, 0), chrome, "youtube.com", 0.0)
        val c = s.step(at(12, 0, 3), chrome, "youtube.com", 3.0)
        assertEquals(listOf(Charge(at(12, 0, 3), chrome, "youtube.com", 3.0)), c)
    }

    @Test fun a_host_change_gives_the_seconds_before_it_to_the_old_site() {
        val s = FrontSplit()
        s.step(at(12, 0, 0), chrome, "youtube.com", 0.0)
        // the address changed 2 s after the last step: that step finds reddit.com, but the 2 s were youtube's
        val c = s.step(at(12, 0, 2), chrome, "reddit.com", 2.0)
        assertEquals(2.0, total(c, chrome, "youtube.com"), 1e-9)
        assertEquals(0.0, total(c, chrome, "reddit.com"), 1e-9)
        // from here on it is reddit's
        assertEquals(3.0, total(s.step(at(12, 0, 5), chrome, "reddit.com", 3.0), chrome, "reddit.com"), 1e-9)
    }

    @Test fun the_browser_leaving_the_front_gives_its_last_seconds_to_its_last_site() {
        val s = FrontSplit()
        s.step(at(12, 0, 0), chrome, "youtube.com", 0.0)
        val c = s.step(at(12, 0, 1), "com.whatsapp", "", 1.0)
        assertEquals(1.0, total(c, chrome, "youtube.com"), 1e-9)
        assertEquals(0.0, total(c, "com.whatsapp", ""), 1e-9)
    }

    @Test fun the_screen_going_off_gives_the_last_seconds_to_what_was_in_front_then_nothing() {
        val s = FrontSplit()
        s.step(at(12, 0, 0), chrome, "youtube.com", 0.0)
        val c = s.idle(at(12, 0, 2), 2.0)
        assertEquals(listOf(Charge(at(12, 0, 2), chrome, "youtube.com", 2.0)), c)
        assertTrue(s.idle(at(12, 0, 32), 10.0).isEmpty())            // off: the slow steps charge nothing
        assertTrue(s.idle(at(12, 1, 2), 10.0).isEmpty())
    }

    @Test fun the_first_step_after_a_gap_charges_nothing_for_it() {
        val s = FrontSplit()
        s.step(at(12, 0, 0), chrome, "youtube.com", 0.0)
        s.idle(at(12, 0, 3), 3.0)
        assertTrue(s.step(at(12, 5, 0), chrome, "youtube.com", 3.0).isEmpty())   // (a block, Lockdown, the screen off was in between)
        assertEquals(3.0, total(s.step(at(12, 5, 3), chrome, "youtube.com", 3.0), chrome, "youtube.com"), 1e-9)
    }

    @Test fun nothing_is_charged_for_no_time() {
        val s = FrontSplit()
        s.step(at(12, 0, 0), chrome, "a.com", 0.0)
        assertTrue(s.step(at(12, 0, 0), chrome, "b.com", 0.0).isEmpty())
    }

    @Test fun time_over_midnight_is_cut_at_the_minute_so_each_day_gets_its_part() {
        val s = FrontSplit()
        s.step(at(23, 59, 58, day = 28), chrome, "youtube.com", 0.0)
        val c = s.step(at(0, 0, 1, day = 29), chrome, "youtube.com", 3.0)
        assertEquals(2, c.size)
        assertEquals(at(23, 59, 58, day = 28), c[0].at); assertEquals(2.0, c[0].seconds, 1e-9)
        assertEquals(at(0, 0, 1, day = 29), c[1].at); assertEquals(1.0, c[1].seconds, 1e-9)
        // through the minute accumulator the two parts land in minutes of their own days
        val acc = MinuteAcc()
        val rows = c.flatMap { acc.add(it.at, it.app, it.site, it.seconds) } + acc.flush()
        assertEquals(listOf(LocalDate.of(2026, 9, 28), LocalDate.of(2026, 9, 29)), rows.map { it.minute.toLocalDate() })
        assertEquals(listOf(2, 1), rows.map { it.seconds })
    }

    @Test fun a_site_change_at_a_minute_edge_splits_that_minute_between_the_sites() {
        val s = FrontSplit(); val acc = MinuteAcc()
        val out = ArrayList<MinuteRow>()
        fun go(cs: List<Charge>) { for (c in cs) out += acc.add(c.at, c.app, c.site, c.seconds) }
        go(s.step(at(12, 0, 57), chrome, "a.com", 0.0))
        go(s.step(at(12, 1, 0), chrome, "a.com", 3.0))
        go(s.step(at(12, 1, 2), chrome, "b.com", 2.0))      // b appears: the 2 s were a's
        go(s.step(at(12, 1, 5), chrome, "b.com", 3.0))
        out += acc.flush()
        val bySite = out.groupBy { it.site }.mapValues { e -> e.value.sumOf { it.seconds } }
        assertEquals(mapOf("a.com" to 5, "b.com" to 3), bySite)
    }

    // ---------- Insights top list ----------

    private val apps = listOf(
        AppTime(chrome, "Chrome", 60 * 60_000L),
        AppTime("com.whatsapp", "WhatsApp", 20 * 60_000L),
    )
    private val sites = mapOf(chrome to mapOf("youtube.com" to 40 * 60_000L, "reddit.com" to 15 * 60_000L))

    @Test fun with_sites_on_the_browser_row_is_replaced_by_its_sites_and_what_no_site_was_read_for() {
        val rows = SiteTop.rows(apps, sites, on = true) { it }
        assertEquals(listOf("youtube.com", "WhatsApp", "reddit.com", "Chrome"), rows.map { it.label })
        assertEquals(listOf(40 * 60_000L, 20 * 60_000L, 15 * 60_000L, 5 * 60_000L), rows.map { it.ms })
        val yt = rows.first()
        assertEquals("youtube.com", yt.site); assertEquals("Chrome", yt.via); assertEquals(chrome, yt.pkg)
        assertEquals(null, rows.first { it.label == "Chrome" }.site)       // the browser's own row is still the app
        assertEquals(null, rows.first { it.label == "WhatsApp" }.via)
    }

    @Test fun the_browser_row_is_hidden_when_almost_nothing_is_left() {
        val all = mapOf(chrome to mapOf("youtube.com" to 59 * 60_000L + 40_000L))     // 20 s left of the hour
        val rows = SiteTop.rows(apps, all, on = true) { it }
        assertEquals(listOf("youtube.com", "WhatsApp"), rows.map { it.label })
        val more = SiteTop.rows(apps, mapOf(chrome to mapOf("youtube.com" to 70 * 60_000L)), on = true) { it }   // more than the browser had
        assertEquals(listOf("youtube.com", "WhatsApp"), more.map { it.label })
    }

    @Test fun with_sites_off_the_list_is_unchanged() {
        val rows = SiteTop.rows(apps, sites, on = false) { it }
        assertEquals(listOf("Chrome", "WhatsApp"), rows.map { it.label })
        assertEquals(listOf(60 * 60_000L, 20 * 60_000L), rows.map { it.ms })
        assertTrue(rows.all { it.site == null && it.via == null })
    }

    @Test fun with_no_site_data_the_list_is_unchanged_even_when_on() {
        assertEquals(listOf("Chrome", "WhatsApp"), SiteTop.rows(apps, emptyMap(), on = true) { it }.map { it.label })
    }

    @Test fun a_browser_without_an_app_row_still_shows_its_sites_named_by_the_lookup() {
        val rows = SiteTop.rows(listOf(AppTime("com.whatsapp", "WhatsApp", 5 * 60_000L)), sites, on = true) { if (it == chrome) "Chrome" else it }
        assertEquals(listOf("youtube.com", "reddit.com", "WhatsApp"), rows.map { it.label })
        assertEquals("Chrome", rows.first().via)
    }

    @Test fun a_site_seen_for_under_a_second_is_left_out() {
        val rows = SiteTop.rows(apps, mapOf(chrome to mapOf("a.com" to 900L, "b.com" to 5 * 60_000L)), on = true) { it }
        assertEquals(listOf("Chrome", "WhatsApp", "b.com").sorted(), rows.map { it.label }.sorted())
    }

    @Test fun site_time_is_added_up_from_the_history_rows_of_browsers_only() {
        fun row(app: String, site: String, sec: Int, m: Int = 0) = MinuteRow(at(12, m), app, site, sec, sec)
        val ms = SiteTop.msByBrowser(listOf(
            row(chrome, "youtube.com", 40), row(chrome, "youtube.com", 30, m = 1), row(chrome, "", 20, m = 2),
            row("com.whatsapp", "", 50), row("com.example.notabrowser", "x.com", 60),
        ))
        assertEquals(mapOf(chrome to mapOf("youtube.com" to 70_000L)), ms)
    }

    @Test fun the_csv_folds_sites_into_the_browser_when_sites_are_off() {
        val day = LocalDate.of(2026, 9, 28)
        val days = mapOf(day to mapOf((chrome to "youtube.com") to 600, (chrome to "reddit.com") to 300, (chrome to "") to 60, ("com.whatsapp" to "") to 120))
        val folded = SiteTop.withoutSites(days)[day]!!
        assertEquals(mapOf((chrome to "") to 960, ("com.whatsapp" to "") to 120), folded)
    }
}
