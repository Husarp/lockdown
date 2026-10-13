package com.husarp.lockdown

import com.husarp.lockdown.engine.AppKind
import com.husarp.lockdown.engine.AppList
import com.husarp.lockdown.engine.AppSort
import com.husarp.lockdown.engine.AppStat
import com.husarp.lockdown.engine.AppVisit
import com.husarp.lockdown.engine.IslandOpen
import com.husarp.lockdown.engine.TopRow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneOffset

/** The app lists (Home's "Time per app today", Insights' Top apps, "All apps"): bars, sort orders, kinds, opens. */
class AppListTest {
    private val utc = ZoneOffset.UTC
    private val today = LocalDate.of(2026, 10, 13)
    private fun ms(h: Int, m: Int, day: Int = 13) = LocalDateTime.of(2026, 10, day, h, m).toInstant(utc).toEpochMilli()
    private val min = 60_000L

    // ---------- bars ----------

    @Test fun `apps sharing a group limit get their own bars, not the limit's`() {
        // the screenshot: MangaPin 50 m (top), Discord 10 m and YouTube 1 m share a limit with 19 min left
        val top = 50 * min
        val discord = AppList.bar(10 * min, top)
        val youtube = AppList.bar(1 * min, top)
        assertEquals(0.2f, discord, 0.001f)
        assertEquals(0.02f, youtube, 0.001f)
        assertTrue(discord > youtube)
        assertEquals(1f, AppList.bar(top, top), 0.001f)
    }

    @Test fun `a bar is a sliver at least and never past full`() {
        assertEquals(0.02f, AppList.bar(0, 50 * min), 0.001f)
        assertEquals(0.02f, AppList.bar(5 * min, 0), 0.001f)
        assertEquals(1f, AppList.bar(80 * min, 50 * min), 0.001f)
    }

    // ---------- sort ----------

    private val list = listOf(
        AppStat("a", "Alpha", 10 * min, opens = 3, lastMs = ms(9, 0)),
        AppStat("b", "Beta", 50 * min, opens = 1, lastMs = ms(14, 0)),
        AppStat("c", "Gamma", 20 * min, opens = 9, lastMs = 0L),
        AppStat("d", "delta", 20 * min, opens = 3, lastMs = ms(12, 0)),
    )

    @Test fun `most time, ties by name`() =
        assertEquals(listOf("b", "d", "c", "a"), AppList.sort(list, AppSort.TIME).map { it.pkg })

    @Test fun `most opens, ties by time`() =
        assertEquals(listOf("c", "d", "a", "b"), AppList.sort(list, AppSort.OPENS).map { it.pkg })

    @Test fun `recently opened, never opened last`() =
        assertEquals(listOf("b", "d", "a", "c"), AppList.sort(list, AppSort.RECENT).map { it.pkg })

    // ---------- kinds ----------

    @Test fun `system apps - Android's flags, the home screen and Settings`() {
        val always = setOf("org.lineageos.trebuchet", "com.android.settings")
        assertTrue(AppList.system("com.android.systemui", AppList.FLAG_SYSTEM, always))
        assertTrue(AppList.system("com.google.android.youtube", AppList.FLAG_SYSTEM or AppList.FLAG_UPDATED_SYSTEM_APP, always))
        assertTrue(AppList.system("org.lineageos.trebuchet", 0, always))          // the launcher, whatever its flags
        assertTrue(AppList.system("com.android.settings", null, always))
        assertFalse(AppList.system("com.discord", 0, always))
        assertFalse(AppList.system("com.mangapin", null, always))                // Island-only: not installed here
    }

    @Test fun `kinds from where an app was used`() {
        val rows = listOf(TopRow("com.discord", "Discord", 10 * min), TopRow("com.mangapin", "MangaPin (Island)", 50 * min),
            TopRow("org.lineageos.trebuchet", "Trebuchet", 22 * min), TopRow("com.both", "Both", 3 * min),
            TopRow("com.android.chrome", "example.com", 2 * min, site = "example.com", via = "Chrome"))
        val stats = AppList.stats(rows, mainPkgs = setOf("com.discord", "org.lineageos.trebuchet", "com.both"),
            islandPkgs = setOf("com.mangapin", "com.both"), system = setOf("org.lineageos.trebuchet"))
        fun shown(k: AppKind) = AppList.filter(stats, k).map { it.label }
        assertEquals(rows.map { it.label }, shown(AppKind.ALL))
        assertEquals(listOf("Discord", "MangaPin (Island)", "Both", "example.com"), shown(AppKind.INSTALLED))
        assertEquals(listOf("Discord", "Trebuchet", "Both", "example.com"), shown(AppKind.PERSONAL))   // a site: its browser's kind
        assertEquals(listOf("MangaPin (Island)", "Both"), shown(AppKind.ISLAND))
    }

    @Test fun `Personal and Island only with Island linked, else all apps`() {
        assertEquals(listOf(AppKind.ALL, AppKind.INSTALLED), AppList.kinds(false))
        assertEquals(AppKind.entries.toList(), AppList.kinds(true))
        assertEquals(AppKind.ALL, AppList.effective(AppKind.ISLAND, AppList.kinds(false)))
        assertEquals(AppKind.INSTALLED, AppList.effective(AppKind.INSTALLED, AppList.kinds(false)))
        assertEquals(AppKind.ISLAND, AppList.effective(AppKind.ISLAND, AppList.kinds(true)))
    }

    // ---------- opens ----------

    @Test fun `opens are the log's visits in the range, Island's and sites' too`() {
        val from = ms(0, 0); val to = ms(23, 59)
        val visits = listOf(
            AppVisit("com.discord", ms(9, 0), ms(9, 10), 10 * min),
            AppVisit("com.discord", ms(13, 0), ms(13, 5), 5 * min),
            AppVisit("com.discord", ms(14, 0), ms(14, 0) + 3000, 3000),            // a flicker: not an open
            AppVisit("com.discord", ms(22, 0, day = 12), ms(22, 5, day = 12), 5 * min),   // yesterday: out of range
        )
        val island = listOf(IslandOpen("com.mangapin", "MangaPin", ms(11, 0), ms(11, 50), 3000))
        val sites = listOf("example.com" to ms(10, 0), "example.com" to ms(15, 30))
        val o = AppList.opens(visits, island, sites, from, to)
        assertEquals(2 to ms(13, 0), o["com.discord"])
        assertEquals(1 to ms(11, 0), o["com.mangapin"])
        assertEquals(2 to ms(15, 30), o["example.com"])
        val stats = AppList.stats(listOf(TopRow("com.android.chrome", "example.com", min, site = "example.com"), TopRow("com.discord", "Discord", min)),
            setOf("com.discord"), emptySet(), emptySet(), o)
        assertEquals(listOf(2, 2), stats.map { it.opens })
        assertEquals(ms(15, 30), stats[0].lastMs)
    }

    @Test fun `last opened text`() {
        assertEquals("", AppList.lastText(0, today, utc))
        assertEquals("14:05", AppList.lastText(ms(14, 5), today, utc))
        assertEquals("Yesterday 09:30", AppList.lastText(ms(9, 30, day = 12), today, utc))
        assertEquals("Sat 10 Oct", AppList.lastText(ms(9, 30, day = 10), today, utc))
    }
}
