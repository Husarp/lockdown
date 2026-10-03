package com.husarp.lockdown

import com.husarp.lockdown.block.Enforce
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.AntiBypassCfg
import com.husarp.lockdown.engine.BlockPause
import com.husarp.lockdown.engine.BreakCfg
import com.husarp.lockdown.engine.CustomCfg
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Pause
import com.husarp.lockdown.engine.RemindersEngine
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.SleepCfg
import com.husarp.lockdown.engine.TrustedClock
import com.husarp.lockdown.engine.Window
import com.husarp.lockdown.engine.ZoneGuard
import com.husarp.lockdown.engine.ZoneState
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import java.time.LocalTime

/** Pause my blocks, the emergency unlock for sites, the challenge settings and trusted time. */
class PauseAndTimeTest {
    private val t0 = LocalDateTime.of(2026, 10, 3, 22, 0)
    private val app = Item("a", "Game", "com.game", ItemType.APP, rules = listOf(Rule(RuleType.TEMPORARY, tempUntil = t0.plusDays(1).toString())))
    private val site = Item("s", "Feed", "feed.com", ItemType.SITE, blockType = "dns", rules = listOf(Rule(RuleType.TEMPORARY, tempUntil = t0.plusDays(1).toString())))
    private val forGood = Item("p", "Casino", "casino.com", ItemType.SITE, rules = listOf(Rule(RuleType.PERMANENT)))
    private val cfg = Config(items = listOf(app, site, forGood))

    // ---------- Pause my blocks ----------

    @Test fun a_pause_runs_only_from_its_start_to_its_end() {
        val p = Pause.start(t0, 60, silent = false, reset = LocalTime.MIDNIGHT)
        assertEquals(t0.plusHours(1).toString(), p.until)
        assertNotNull(Pause.state(p, t0.plusMinutes(30)))
        assertNull(Pause.state(p, t0.minusMinutes(1)))                  // the clock put back before it began
        assertNull(Pause.state(p, t0.plusHours(1)))                     // over on the dot
        assertNull(Pause.state(null, t0))
    }

    @Test fun a_pause_can_never_run_longer_than_a_day() {
        val forged = BlockPause(t0.toString(), t0.plusDays(2).toString())
        assertNull(Pause.state(forged, t0.plusHours(1)))
        assertNull(Pause.state(BlockPause("junk", "junk"), t0))
    }

    @Test fun rest_of_the_day_ends_at_the_next_reset_time() {
        val four = LocalTime.of(4, 0)
        assertEquals(LocalDateTime.of(2026, 10, 4, 4, 0), Pause.endFor(null, t0, four))                       // 22:00 -> 04:00
        assertEquals(LocalDateTime.of(2026, 10, 4, 4, 0), Pause.endFor(null, LocalDateTime.of(2026, 10, 4, 3, 0), four))
        assertEquals(LocalDateTime.of(2026, 10, 4, 0, 0), Pause.endFor(null, t0, LocalTime.MIDNIGHT))
        assertEquals(listOf("30 min", "1 h", "2 h", "4 h", Pause.REST_OF_DAY), Pause.DURATIONS.keys.toList())
        assertEquals("4 h", Pause.label(240))
    }

    @Test fun only_a_silent_pause_silences() {
        val loud = Pause.start(t0, 30, false, LocalTime.MIDNIGHT)
        val quiet = Pause.start(t0, 30, true, LocalTime.MIDNIGHT)
        assertNull(Pause.silentUntil(loud, t0.plusMinutes(1)))
        assertEquals(t0.plusMinutes(30), Pause.silentUntil(quiet, t0.plusMinutes(1)))
        assertFalse(Enforce.silenced(cfg.copy(pause = loud), t0.plusMinutes(1)))
        assertTrue(Enforce.silenced(cfg.copy(pause = quiet), t0.plusMinutes(1)))
    }

    @Test fun a_pause_lifts_apps_and_sites_but_counts_their_time_and_ends_by_itself() {
        assertNotNull(Enforce.app(cfg, "com.game", t0, null))
        assertNotNull(Enforce.site(cfg, "feed.com", t0, null))
        val paused = cfg.copy(pause = Pause.start(t0, 30, false, LocalTime.MIDNIGHT))
        val during = t0.plusMinutes(10)
        assertNull(Enforce.app(paused, "com.game", during, null))
        assertNull(Enforce.site(paused, "feed.com", during, null))      // the site filter asks this too
        assertNull(Enforce.site(paused, "casino.com", during, null))    // permanent blocks too (PC)
        assertFalse(Enforce.itemBlocked(paused, app, during))           // not blocked -> its time is counted
        assertNotNull(Enforce.app(paused, "com.game", t0.plusMinutes(30), null))   // back on the dot
    }

    @Test fun an_import_neither_brings_a_pause_nor_ends_one() {
        val json = Json { encodeDefaults = true }
        val running = Pause.start(t0, 60, false, LocalTime.MIDNIGHT)
        val withPause = json.encodeToString(Config(pause = running))
        assertNull(Store.decodeImport(withPause, Config())!!.pause)
        assertEquals(running, Store.decodeImport(json.encodeToString(Config()), Config(pause = running))!!.pause)
    }

    @Test fun a_silent_pause_holds_bedtime_and_your_reminders_until_it_ends() {
        val e = RemindersEngine()
        val water = CustomCfg(id = "w", text = "Drink water", kind = "interval", every = 1)
        val first = e.tick(LocalDateTime.of(2026, 9, 28, 12, 0), 0.0, SleepCfg(), BreakCfg(on = false), listOf(water), dt = 60.0)
        assertTrue(first.show.any { it.key == "custom:w" })
        val silent = e.tick(LocalDateTime.of(2026, 9, 28, 12, 1), 0.0, SleepCfg(), BreakCfg(on = false), listOf(water), dt = 60.0, silent = true)
        assertTrue("custom:w" in silent.close)                                // the one on screen goes
        val still = e.tick(LocalDateTime.of(2026, 9, 28, 12, 3), 0.0, SleepCfg(), BreakCfg(on = false), listOf(water), dt = 120.0, silent = true)
        assertTrue(still.show.isEmpty())

        val s = SleepCfg(on = true, bedtime = "23:00", wake = "07:00")
        val bed = RemindersEngine()
        assertTrue(bed.tick(LocalDateTime.of(2026, 9, 28, 23, 10), 999.0, s, BreakCfg(on = false), emptyList(), silent = true).show.isEmpty())
        assertTrue(bed.tick(LocalDateTime.of(2026, 9, 28, 23, 11), 999.0, s, BreakCfg(on = false), emptyList()).show.any { it.key == "sleep" })
    }

    // ---------- the emergency unlock frees sites too ----------

    @Test fun an_emergency_unlocks_a_site_but_never_a_permanent_one() {
        val until = t0.plusMinutes(20).toString()
        val unlocked = cfg.copy(unlockUntil = until, unlockItems = listOf("s", "p"))
        assertNull(Enforce.site(unlocked, "feed.com", t0.plusMinutes(5), null))
        assertNull(Enforce.site(unlocked, "www.feed.com", t0.plusMinutes(5), null))
        assertFalse(Enforce.itemBlocked(unlocked, site, t0.plusMinutes(5)))
        assertNotNull(Enforce.site(unlocked, "casino.com", t0.plusMinutes(5), null))
        assertNotNull(Enforce.site(unlocked, "feed.com", t0.plusMinutes(20), null))   // over: blocked again
        // one written to last longer than the unlock can (a clock set forward, then back) doesn't hold
        val forged = cfg.copy(unlockUntil = t0.plusDays(3).toString(), unlockItems = listOf("s"))
        assertNotNull(Enforce.site(forged, "feed.com", t0, null))
    }

    @Test fun an_unlocked_broad_site_doesnt_free_a_permanent_subdomain_either_order() {
        val sub = Item("o", "Old feed", "old.feed.com", ItemType.SITE, rules = listOf(Rule(RuleType.PERMANENT)))
        for (items in listOf(listOf(site, sub), listOf(sub, site))) {
            val c = Config(items = items, unlockUntil = t0.plusMinutes(20).toString(), unlockItems = listOf("s"))
            assertNotNull(Enforce.site(c, "old.feed.com", t0.plusMinutes(5), null))
            assertNull(Enforce.site(c, "feed.com", t0.plusMinutes(5), null))
        }
    }

    // ---------- challenge settings ----------

    @Test fun challenge_settings_loosen_only_when_weaker() {
        val on = AntiBypassCfg(phrase = true, length = 60)
        assertTrue(AntiBypass.settingsLooser(on, on.copy(length = 30)))
        assertFalse(AntiBypass.settingsLooser(on, on.copy(length = 120)))
        assertTrue(AntiBypass.settingsLooser(on.copy(complex = true), on))
        assertFalse(AntiBypass.settingsLooser(on, on.copy(complex = true)))
        assertTrue(AntiBypass.settingsLooser(on, on.copy(customPhrase = "short one")))          // 8 letters < 60
        // your own phrase is known in advance: setting or changing one is looser whatever its length
        assertTrue(AntiBypass.settingsLooser(on.copy(complex = true), on.copy(complex = true, customPhrase = "a".repeat(60))))
        assertTrue(AntiBypass.settingsLooser(on.copy(customPhrase = "x".repeat(70)), on.copy(customPhrase = "y".repeat(70))))
        assertFalse(AntiBypass.settingsLooser(AntiBypassCfg(), on.copy(customPhrase = "x".repeat(70))))   // challenge was off
        assertFalse(AntiBypass.settingsLooser(on.copy(customPhrase = "x".repeat(40)), on))                 // back to random 60
        val hours = AntiBypassCfg(hours = true)
        assertTrue(AntiBypass.settingsLooser(hours, hours.copy(windows = hours.windows + Window(listOf(0), "09:00", "10:00"))))
        assertTrue(AntiBypass.settingsLooser(AntiBypassCfg(waitMin = 10), AntiBypassCfg(waitMin = 5)))
        assertFalse(AntiBypass.settingsLooser(AntiBypassCfg(waitMin = 5), AntiBypassCfg(waitMin = 30)))
    }

    @Test fun the_phrase_field_takes_typing_not_a_pasted_chunk() {
        assertTrue(AntiBypass.typedNotPasted("abc", "abcd"))
        assertTrue(AntiBypass.typedNotPasted("abcd", "ab"))                 // deleting is fine
        assertFalse(AntiBypass.typedNotPasted("", "kqzph mxacd"))
        assertFalse(AntiBypass.typedNotPasted("kq", "kqzph"))
    }

    @Test fun emergency_settings_fewer_or_shorter_are_free() {
        assertFalse(AntiBypass.emergencyLooser(true, 20, 3, "week", true, 10, 2, "week"))
        assertFalse(AntiBypass.emergencyLooser(true, 20, 3, "day", true, 20, 3, "week"))
        assertFalse(AntiBypass.emergencyLooser(true, 20, 3, "week", false, 20, 3, "week"))       // turning it off
        assertTrue(AntiBypass.emergencyLooser(true, 20, 3, "week", true, 20, 4, "week"))
    }

    // ---------- trusted time ----------

    private class Fake(var elapsed: Long, var system: Long)

    @Test fun moving_the_clock_forward_or_back_changes_nothing() {
        val f = Fake(elapsed = 1_000, system = 1_000_000)
        val c = TrustedClock(lastTrusted = 0, saved = null, elapsed = { f.elapsed }, system = { f.system })
        assertEquals(1_000_000, c.now())
        f.system += 3 * 3600_000; f.elapsed += 60_000          // clock set 3 h forward, a minute passes
        assertEquals(1_060_000, c.now())
        f.system -= 10 * 3600_000                              // ... and back
        assertEquals(1_060_000, c.now())
    }

    @Test fun a_restart_in_the_same_boot_keeps_the_base_and_a_new_boot_never_goes_back() {
        val f = Fake(elapsed = 5_000, system = 2_000_000)
        val first = TrustedClock(0, null, { f.elapsed }, { f.system })
        f.system += 3600_000; f.elapsed += 1_000                // clock forward, then the app restarts
        val again = TrustedClock(first.now(), first.base, { f.elapsed }, { f.system })
        assertEquals(2_001_000, again.now())                    // not the clock set forward
        val reboot = Fake(elapsed = 10, system = 500_000)       // new boot, clock set back
        val fresh = TrustedClock(2_001_000, null, { reboot.elapsed }, { reboot.system })
        assertEquals(2_001_000, fresh.now())
        // a base from another boot (elapsed now lower than it was) isn't used
        val stale = TrustedClock(0, TrustedClock.Base(9_000_000, 50_000, true), { reboot.elapsed }, { reboot.system })
        assertEquals(500_000, stale.now())
    }

    @Test fun internet_time_sets_the_base_and_is_asked_again_every_30_minutes() {
        val f = Fake(elapsed = 0, system = 9_999_999)
        val c = TrustedClock(0, null, { f.elapsed }, { f.system })
        assertTrue(c.syncDue())
        c.asking()
        assertFalse(c.syncDue())
        f.elapsed = TrustedClock.RETRY_MS
        assertTrue(c.syncDue())                                 // no answer: ask again after 2 min
        c.synced(1_000_000, f.elapsed)
        f.elapsed += 1_000
        assertEquals(1_001_000, c.now())
        assertTrue(c.base.synced)
        assertFalse(c.syncDue())
        f.elapsed += TrustedClock.RESYNC_MS
        assertTrue(c.syncDue())
    }

    @Test fun a_new_time_zone_counts_only_after_24_hours() {
        val day = ZoneGuard.DELAY_MS
        var s = ZoneGuard.step(null, "Europe/Warsaw", 0)
        assertEquals(ZoneState("Europe/Warsaw"), s)
        s = ZoneGuard.step(s, "Asia/Tokyo", 1_000)
        assertEquals("Europe/Warsaw", s.name)
        assertEquals("Asia/Tokyo", s.pending)
        assertEquals("Europe/Warsaw", ZoneGuard.step(s, "Asia/Tokyo", day).name)          // not yet
        assertEquals(ZoneState("Asia/Tokyo"), ZoneGuard.step(s, "Asia/Tokyo", 1_000 + day))
        assertEquals(ZoneState("Europe/Warsaw"), ZoneGuard.step(s, "Europe/Warsaw", 5_000)) // back: nothing pending
        val other = ZoneGuard.step(s, "America/New_York", 2_000)                            // a third: starts again
        assertEquals("America/New_York", other.pending)
        assertEquals(2_000, other.since)
    }
}
