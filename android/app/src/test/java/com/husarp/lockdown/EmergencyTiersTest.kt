package com.husarp.lockdown

import com.husarp.lockdown.block.Enforce
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.EmergencyCfg
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.Emergency
import com.husarp.lockdown.engine.EmergencyTier
import com.husarp.lockdown.engine.EmergencyUse
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.LimitClock
import com.husarp.lockdown.engine.PeriodHold
import com.husarp.lockdown.engine.ResetCarry
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.link.applyConfig
import kotlinx.serialization.decodeFromString
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

/** Emergency kinds ("tiers"): the migration from the single setting, counting per kind and period, the running
 *  unlocks, the looseness check, import and the Island helper. */
class EmergencyTiersTest {
    private val mon = LocalDateTime.of(2026, 9, 28, 12, 0)          // Monday noon
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val week5 = EmergencyTier("w", 3, "week", 5)
    private val month60 = EmergencyTier("m", 1, "month", 60)
    private val game = Item("a", "Game", "com.game", ItemType.APP, rules = listOf(Rule(RuleType.TEMPORARY, tempUntil = mon.plusDays(30).toString())))
    private val chat = Item("b", "Chat", "com.chat", ItemType.APP, rules = listOf(Rule(RuleType.TEMPORARY, tempUntil = mon.plusDays(30).toString())))

    private fun use(t: EmergencyTier, at: LocalDateTime, items: List<String> = emptyList(), alerts: Boolean = false) =
        EmergencyUse(at.toString(), t.id, at.plusMinutes(t.minutes.toLong()).toString(), items, alerts)

    private fun cfg(tiers: List<EmergencyTier>?, uses: List<EmergencyUse> = emptyList(), on: Boolean = true) =
        Config(items = listOf(game, chat), emergency = EmergencyCfg(enabled = on, tiers = tiers), emergencyUses = uses)

    /** "Emergency (N left)" as the bedtime screen shows it. */
    private fun left(c: Config, now: LocalDateTime) = Emergency.available(c.emergency.enabled, c.emergency.tierList(), c.allUses(), now, c.clock())

    /** Starts kind [id] on [c] as the dialog does; the new config and the result. */
    private fun start(c: Config, id: String, at: LocalDateTime, items: List<String> = listOf("a"), alerts: Boolean = false) =
        c.withEmergency(id, items, alerts, at)

    // ---------- migration ----------

    @Test fun no_kinds_yet_means_the_single_setting_as_one_kind() {
        assertEquals(listOf(EmergencyTier("t1", 3, "week", 20)), EmergencyCfg().tierList())                       // a fresh install
        assertEquals(listOf(EmergencyTier("t1", 1, "day", 45)), EmergencyCfg(minutes = 45, uses = 1, per = "day").tierList())
        assertEquals(emptyList<EmergencyTier>(), EmergencyCfg(tiers = emptyList()).tierList())                    // all removed stays removed
        // a 0.5.14 config file loads as one kind, its uses and running unlock with it
        val old = """{"emergency":{"enabled":true,"minutes":20,"uses":3,"per":"week"},"items":[{"id":"a","name":"Game","target":"com.game","type":"APP"}],
            "unlocks":["2026-09-28T10:00"],"unlockUntil":"2026-09-28T10:20","unlockItems":["a"]}"""
        val c = json.decodeFromString<Config>(old)
        assertEquals(2, Emergency.usesLeft(c.allUses(), c.emergency.tierList().single(), mon).left)
        assertEquals(LocalDateTime.of(2026, 9, 28, 10, 20), Enforce.emergencyUntil(c, "a", LocalDateTime.of(2026, 9, 28, 10, 5)))
    }

    @Test fun bad_kinds_are_dropped_or_clamped() {
        val got = Emergency.clean(listOf(
            EmergencyTier("", 3, "week", 20), EmergencyTier("x", 0, "week", 20), EmergencyTier("y", 3, "year", 20),
            EmergencyTier("a", 50, "day", 500), EmergencyTier("a", 1, "week", 5), EmergencyTier("b", 2, "month", 0),
            EmergencyTier("c", 1, "week", 5), EmergencyTier("d", 1, "week", 5), EmergencyTier("e", 1, "week", 5), EmergencyTier("f", 1, "week", 5)))
        assertEquals(listOf(EmergencyTier("a", 10, "day", 60), EmergencyTier("b", 2, "month", 1), EmergencyTier("c", 1, "week", 5),
            EmergencyTier("d", 1, "week", 5), EmergencyTier("e", 1, "week", 5)), got)
    }

    @Test fun uses_from_before_kinds_count_for_the_migrated_kind_only() {
        val legacy = Config(unlocks = listOf(mon.minusHours(2).toString(), mon.minusHours(1).toString()))
        val t1 = legacy.emergency.tierList().single()
        assertEquals(1, Emergency.usesLeft(legacy.allUses(), t1, mon).left)
        // after the first save they still count for t1, and for no other kind
        val saved = legacy.copy(emergency = legacy.emergency.copy(tiers = listOf(t1, week5)))
        assertEquals(1, Emergency.usesLeft(saved.allUses(), t1, mon).left)
        assertEquals(3, Emergency.usesLeft(saved.allUses(), week5, mon).left)
        // t1 removed: they count for nothing
        val gone = legacy.copy(emergency = legacy.emergency.copy(tiers = listOf(week5)))
        assertEquals(3, left(gone, mon))
    }

    @Test fun a_running_unlock_from_before_kinds_holds_but_isnt_a_second_use() {
        val until = mon.plusMinutes(15)
        val c = Config(items = listOf(game), unlocks = listOf(mon.minusMinutes(5).toString()), unlockUntil = until.toString(),
            unlockItems = listOf("a"), alertsPausedUntil = until.toString())
        assertEquals(2, left(c, mon))                                       // the one start, not three
        assertEquals(until, Enforce.emergencyUntil(c, "a", mon))
        assertTrue(Enforce.alertsPaused(c, mon))
        assertNull(Enforce.emergencyUntil(c, "b", mon))
        // the old cap: an end further off than its length from now (clock set back) doesn't hold
        val forged = c.copy(unlockUntil = mon.plusDays(2).toString(), alertsPausedUntil = mon.plusDays(2).toString())
        assertNull(Enforce.emergencyUntil(forged, "a", mon))
        assertFalse(Enforce.alertsPaused(forged, mon))
        assertNotNull(Enforce.emergencyUntil(forged, "a", mon.plusDays(2).minusMinutes(10)))   // as before kinds
        // the streak in Insights counts that day once (the start), not the running one again
        assertEquals(1, c.allUses().count { it.tier.isNotEmpty() })
    }

    @Test fun the_config_saves_the_kinds_and_uses_but_nothing_worked_out() {
        val c = cfg(listOf(week5), listOf(use(week5, mon, listOf("a"))))
        val text = json.encodeToString(c)
        assertFalse("everyUse" in text)
        val back = json.decodeFromString<Config>(text)
        assertEquals(c, back)
        assertEquals(mon.plusMinutes(5), Enforce.emergencyUntil(back, "a", mon.plusMinutes(1)))
    }

    // ---------- counting ----------

    @Test fun two_kinds_count_on_their_own() {
        var c = cfg(listOf(week5, month60))
        repeat(3) { i -> c = start(c, "w", mon.plusHours(i.toLong())).first }
        assertEquals(0, Emergency.usesLeft(c.allUses(), week5, mon.plusHours(4)).left)
        assertEquals(1, Emergency.usesLeft(c.allUses(), month60, mon.plusHours(4)).left)
        assertEquals(1, left(c, mon.plusHours(4)))
        val (_, refused) = start(c, "w", mon.plusHours(4))
        assertNull(refused.use)
        assertEquals("No 5-min emergency unlocks left this week.", refused.error)
        assertEquals("No emergency unlocks left.", start(cfg(listOf(week5), c.emergencyUses), "w", mon.plusHours(4)).second.error)   // one kind: as before
        val (after, ok) = start(c, "m", mon.plusHours(4))
        assertEquals(mon.plusHours(5).toString(), ok.use!!.until)
        assertEquals("m", ok.use!!.tier)
        assertEquals(0, left(after, mon.plusHours(4)))
        // one use however many items, and with the alerts together
        val (both, r) = start(cfg(listOf(week5)), "w", mon, listOf("a", "b"), alerts = true)
        assertEquals(listOf("a", "b"), r.use!!.items)
        assertTrue(r.use!!.alerts)
        assertEquals(2, left(both, mon))
    }

    @Test fun each_kind_resets_with_its_own_period() {
        val c = cfg(listOf(week5, month60), listOf(use(week5, mon), use(month60, mon)))
        assertEquals(LocalDateTime.of(2026, 10, 5, 0, 0), Emergency.usesLeft(c.allUses(), week5, mon).reset)
        assertEquals(LocalDateTime.of(2026, 10, 1, 0, 0), Emergency.usesLeft(c.allUses(), month60, mon).reset)
        assertEquals(1, Emergency.usesLeft(c.allUses(), month60, LocalDateTime.of(2026, 10, 1, 0, 0)).left)   // a new month
        assertEquals(2, Emergency.usesLeft(c.allUses(), week5, LocalDateTime.of(2026, 10, 4, 23, 59)).left)
        assertEquals(3, Emergency.usesLeft(c.allUses(), week5, LocalDateTime.of(2026, 10, 5, 0, 0)).left)
    }

    @Test fun periods_follow_the_reset_time_and_its_hold() {
        // a 03:00 reset: Monday 02:00 is still last week
        val three = LimitClock(LocalTime.of(3, 0))
        val sun = listOf(use(week5, LocalDateTime.of(2026, 9, 27, 22, 0)))
        assertEquals(2, Emergency.usesLeft(sun, week5, LocalDateTime.of(2026, 9, 28, 2, 0), three).left)
        assertEquals(3, Emergency.usesLeft(sun, week5, LocalDateTime.of(2026, 9, 28, 3, 0), three).left)
        // a 20:00 reset: the week starts Sunday 20:00
        val eight = LimitClock(LocalTime.of(20, 0))
        val sunEve = LocalDateTime.of(2026, 10, 4, 20, 0)
        assertEquals(sunEve, Emergency.usesLeft(emptyList(), week5, mon, eight).reset)
        assertEquals(3, Emergency.usesLeft(listOf(use(week5, mon)), week5, sunEve, eight).left)
        // with a 20:00 reset the month ends the evening before the 1st - unless a change of the reset time holds it
        val sept = listOf(use(month60, LocalDateTime.of(2026, 9, 10, 12, 0)))
        val late = LocalDateTime.of(2026, 9, 30, 21, 0)
        assertEquals(1, Emergency.usesLeft(sept, month60, late, eight).left)
        val held = LimitClock(LocalTime.of(20, 0), holds = mapOf("month" to LimitClock.Hold("m2026-09", LocalDateTime.of(2026, 10, 1, 0, 0))))
        assertEquals(0, Emergency.usesLeft(sept, month60, late, held).left)
        assertEquals(1, Emergency.usesLeft(sept, month60, LocalDateTime.of(2026, 10, 1, 0, 0), held).left)
    }

    @Test fun a_changed_reset_time_holds_only_the_running_week_and_month() {
        // reset 00:00 -> 06:00 on Fri 2 Oct: that week and October are held, not the weeks and months before them
        val c = LimitClock.of(LocalTime.of(6, 0), LimitClock.change(LimitClock.DEFAULT, LocalTime.of(6, 0), LocalDateTime.of(2026, 10, 2, 12, 0)))
        val sat = LocalDateTime.of(2026, 10, 3, 12, 0)
        val before = listOf(use(month60, LocalDateTime.of(2026, 9, 10, 12, 0)), use(week5, LocalDateTime.of(2026, 9, 24, 12, 0)))
        assertEquals(1, Emergency.usesLeft(before, month60, sat, c).left)
        assertEquals(3, Emergency.usesLeft(before, week5, sat, c).left)
        // uses inside them still count there, even ones the new time would put in the week or month before
        val inside = listOf(use(month60, LocalDateTime.of(2026, 10, 1, 3, 0)), use(week5, LocalDateTime.of(2026, 9, 28, 3, 0)))
        assertEquals(0, Emergency.usesLeft(inside, month60, sat, c).left)
        assertEquals(2, Emergency.usesLeft(inside, week5, sat, c).left)
        // a hold saved by an older version (no start) still takes in every earlier time: the strict side
        val old = LimitClock.of(LocalTime.of(6, 0), ResetCarry(holds = mapOf("month" to PeriodHold("m2026-10", "2026-11-01T00:00"))))
        assertEquals(0, Emergency.usesLeft(before, month60, sat, old).left)
    }

    @Test fun a_use_dated_in_the_future_counts() {
        val ahead = listOf(use(month60, mon.plusDays(40)))
        assertEquals(0, Emergency.usesLeft(ahead, month60, mon).left)
    }

    @Test fun available_is_the_sum_and_none_while_off() {
        val uses = listOf(use(week5, mon))
        assertEquals(3, Emergency.available(true, listOf(week5, month60), uses, mon))
        assertEquals(0, Emergency.available(false, listOf(week5, month60), uses, mon))
        assertEquals(0, Emergency.available(true, emptyList(), uses, mon))
    }

    @Test fun the_shortest_kind_with_a_use_left_is_offered_first() {
        assertEquals("w", Emergency.shortest(listOf(month60, week5), emptyList(), mon)?.id)
        val spent = List(3) { use(week5, mon.minusHours(it + 1L)) }
        assertEquals("m", Emergency.shortest(listOf(week5, month60), spent, mon)?.id)
        assertNull(Emergency.shortest(listOf(week5), spent, mon))
    }

    @Test fun refusals() {
        val c = cfg(listOf(week5, month60))
        assertEquals("That kind is gone - pick another.", start(c, "zz", mon).second.error)
        assertEquals("Pick something to unlock.", start(c, "w", mon, emptyList()).second.error)
        assertEquals("Emergency unlocks are off.", start(c.copy(emergency = c.emergency.copy(enabled = false)), "w", mon).second.error)
        // a removed kind can't be used
        val removed = c.copy(emergency = c.emergency.copy(tiers = listOf(month60)))
        assertNull(start(removed, "w", mon).second.use)
        // never a permanently blocked item (its own rule)
        val forGood = c.copy(items = listOf(game.copy(rules = listOf(Rule(RuleType.PERMANENT)))))
        assertEquals("A permanent block can't be unlocked.", start(forGood, "w", mon).second.error)
        // refused: the config is unchanged
        assertEquals(c, start(c, "zz", mon).first)
    }

    @Test fun per_week_to_per_month_on_the_1st_would_hand_uses_back() {
        val thu = LocalDateTime.of(2026, 10, 1, 12, 0)                    // the week began Monday 28 September
        val spent = List(3) { use(week5, mon.plusHours(it.toLong())) }
        assertEquals(0, Emergency.usesLeft(spent, week5, thu).left)
        assertEquals(3, Emergency.usesLeft(spent, week5.copy(per = "month"), thu).left)   // hence the challenge
    }

    @Test fun editing_a_kind_mid_period() {
        val two = listOf(use(week5, mon), use(week5, mon.plusDays(2)))
        assertEquals(0, Emergency.usesLeft(two, week5.copy(uses = 1), mon.plusDays(2)).left)        // 3 -> 1 after 2 used
        assertEquals(2, Emergency.usesLeft(two, week5.copy(per = "day"), mon.plusDays(2).plusHours(1)).left)   // only today's
        // a new length leaves a running unlock's end as it was
        val c = cfg(listOf(week5), listOf(use(week5, mon, listOf("a"))))
        val longer = c.copy(emergency = c.emergency.copy(tiers = listOf(week5.copy(minutes = 60))))
        assertEquals(mon.plusMinutes(5), Enforce.emergencyUntil(longer, "a", mon.plusMinutes(1)))
        assertNull(Enforce.emergencyUntil(longer, "a", mon.plusMinutes(5)))
    }

    // ---------- running unlocks ----------

    @Test fun overlapping_unlocks_each_keep_their_own_end() {
        val uses = listOf(use(month60, mon, listOf("a")), use(week5, mon.plusMinutes(10), listOf("b"), alerts = true))
        assertEquals(mon.plusMinutes(60), Emergency.itemUntil(uses, "a", mon.plusMinutes(12)))     // not cut to the 5-min one
        assertEquals(mon.plusMinutes(15), Emergency.itemUntil(uses, "b", mon.plusMinutes(12)))
        assertEquals(mon.plusMinutes(15), Emergency.alertsPausedUntil(uses, mon.plusMinutes(12)))
        assertNull(Emergency.alertsPausedUntil(uses, mon.plusMinutes(15)))
        // the same item in both: the later end
        val both = listOf(use(month60, mon, listOf("a")), use(week5, mon.plusMinutes(58), listOf("a")))
        assertEquals(mon.plusMinutes(63), Emergency.itemUntil(both, "a", mon.plusMinutes(59)))
    }

    @Test fun a_forged_or_future_unlock_doesnt_hold() {
        val ahead = listOf(use(week5, mon.plusHours(3), listOf("a")))
        assertNull(Emergency.itemUntil(ahead, "a", mon))                                              // dated in the future
        val tooLong = listOf(EmergencyUse(mon.toString(), "w", mon.plusMinutes(62).toString(), listOf("a"), alerts = true))
        assertNull(Emergency.itemUntil(tooLong, "a", mon.plusMinutes(1)))                             // longer than any kind
        assertNull(Emergency.alertsPausedUntil(tooLong, mon.plusMinutes(1)))
        val junk = listOf(EmergencyUse("junk", "w", "junk", listOf("a")))
        assertNull(Emergency.itemUntil(junk, "a", mon))
    }

    @Test fun removing_a_kind_or_turning_it_off_keeps_its_running_unlock() {
        val c = cfg(listOf(week5, month60), listOf(use(month60, mon, listOf("a"))))
        val removed = c.copy(emergency = c.emergency.copy(tiers = listOf(week5)))
        assertEquals(mon.plusMinutes(60), Enforce.emergencyUntil(removed, "a", mon.plusMinutes(30)))
        val off = c.copy(emergency = c.emergency.copy(enabled = false))
        assertEquals(mon.plusMinutes(60), Enforce.emergencyUntil(off, "a", mon.plusMinutes(30)))
        assertNull(Enforce.app(removed, "com.game", mon.plusMinutes(30), null))
        assertNotNull(Enforce.app(removed, "com.game", mon.plusMinutes(60), null))
    }

    // ---------- looseness (parity with the PC's emergency_looser) ----------

    @Test fun what_needs_the_challenge() {
        val two = listOf(week5, month60)
        fun loose(oldOn: Boolean, old: List<EmergencyTier>, newOn: Boolean, new: List<EmergencyTier>) = AntiBypass.emergencyLooser(oldOn, old, newOn, new)
        assertFalse(loose(true, two, false, two))                                        // on -> off
        assertTrue(loose(false, two, true, two))                                         // off -> on
        assertFalse(loose(false, two, false, two.map { it.copy(uses = 10, minutes = 60) })) // staying off
        assertTrue(loose(true, listOf(week5), true, two))                                // a new kind
        assertFalse(loose(true, two, true, listOf(week5)))                               // removing one
        assertTrue(loose(true, two, true, listOf(week5.copy(uses = 4), month60)))        // more uses
        assertFalse(loose(true, two, true, listOf(week5.copy(uses = 2), month60)))       // fewer
        assertFalse(loose(true, two, true, listOf(week5, month60.copy(minutes = 61))))     // 61 is clamped to 60: the same
        assertTrue(loose(true, two, true, listOf(week5.copy(minutes = 10), month60)))    // longer
        assertFalse(loose(true, two, true, listOf(week5.copy(minutes = 1), month60)))    // shorter
        assertTrue(loose(true, two, true, listOf(week5.copy(per = "day"), month60)))     // week -> day
        assertTrue(loose(true, two, true, listOf(week5, month60.copy(per = "week"))))    // month -> week
        val day = listOf(EmergencyTier("d", 1, "day", 5))
        assertFalse(loose(true, day, true, listOf(EmergencyTier("d", 1, "week", 5))))    // day -> week
        assertFalse(loose(true, day, true, listOf(EmergencyTier("d", 1, "month", 5))))   // day -> month
        // week -> month: the month you're in may not hold this week, so a spent week's uses would come back early
        assertTrue(loose(true, two, true, listOf(week5.copy(per = "month"), month60)))
        // swapping two kinds' values is a loosening (the spent short one would come back as long)
        assertTrue(loose(true, two, true, listOf(week5.copy(uses = 1, per = "month", minutes = 60), month60.copy(uses = 3, per = "week", minutes = 5))))
        assertFalse(loose(true, two, true, two))                                         // unchanged
        assertFalse(loose(true, two, true, listOf(month60, week5)))                      // reordered
        assertTrue(loose(true, emptyList(), true, listOf(week5)))                        // a kind after all were removed
    }

    @Test fun new_ids_never_repeat_a_taken_one() {
        val id = Emergency.newId(listOf("t1"))
        assertEquals(8, id.length)
        assertFalse(id == "t1")
        assertFalse(Emergency.newId(listOf(id)) == id)
    }

    // ---------- import and Island ----------

    @Test fun an_import_brings_kinds_but_never_the_use_history() {
        val current = Config(emergencyUses = listOf(use(week5, mon, listOf("a"))), unlocks = listOf(mon.minusDays(1).toString()),
            unlockUntil = mon.plusMinutes(3).toString(), unlockItems = listOf("a"), alertsPausedUntil = mon.plusMinutes(3).toString())
        val file = Config(emergency = EmergencyCfg(tiers = listOf(week5, month60)))      // no uses: would reset the counts
        val got = Store.decodeImport(json.encodeToString(file), current)!!
        assertEquals(listOf(week5, month60), got.emergency.tierList())
        assertEquals(current.emergencyUses, got.emergencyUses)
        assertEquals(current.unlocks, got.unlocks)
        assertEquals(current.unlockUntil, got.unlockUntil)
        assertEquals(current.unlockItems, got.unlockItems)
        assertEquals(current.alertsPausedUntil, got.alertsPausedUntil)
        // nor can a file bring in a running unlock
        val forged = Config(emergencyUses = listOf(use(month60, mon, listOf("b"))), unlockUntil = mon.plusMinutes(10).toString(), unlockItems = listOf("b"))
        val none = Store.decodeImport(json.encodeToString(forged), Config())!!
        assertNull(Enforce.emergencyUntil(none, "b", mon.plusMinutes(1)))
    }

    @Test fun the_island_helper_gets_the_kinds_and_frees_an_unlocked_app() {
        val main = cfg(listOf(week5, month60), listOf(use(month60, mon, listOf("a"))))
        val helper = applyConfig(main, Config(items = listOf(Item("9", "Old", "old.app", ItemType.APP))))
        assertEquals(main.emergency, helper.emergency)
        assertEquals(main.emergencyUses, helper.emergencyUses)
        assertNull(Enforce.app(helper, "com.game", mon.plusMinutes(30), null))
        assertNotNull(Enforce.app(helper, "com.chat", mon.plusMinutes(30), null))
        assertNotNull(Enforce.app(helper, "com.game", mon.plusMinutes(60), null))
    }

}
