package com.husarp.lockdown

import com.husarp.lockdown.engine.BedtimeChallenge
import com.husarp.lockdown.engine.ChallengeStep
import com.husarp.lockdown.engine.Challenges
import com.husarp.lockdown.engine.ReminderTier
import com.husarp.lockdown.engine.RemindersEngine
import com.husarp.lockdown.engine.SleepCfg
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

/** What the bedtime editor does with "Challenge before Dismiss" (BedtimeChallenge.kt): the switch, the general list, a
 *  step's own list, what can't be saved - and that those edits are judged by the engine's looser rule. */
class BedtimeChallengeTest {
    private fun step(id: String, kind: String, level: Int, times: Int = 1) = ChallengeStep(id, kind, level, times)
    private val math = Challenges.MATH
    private val phrase = Challenges.PHRASE
    private fun at(day: Int, h: Int, m: Int) = LocalDateTime.of(2026, 9, day, h, m)

    private val general = listOf(step("g", math, 1, 3))
    private val own = listOf(step("o", Challenges.MEMORY, 0, 2))
    private val night = SleepCfg(on = true, bedtime = "22:00", wake = "07:00", guarded = true, challenge = true, challengeSteps = general,
        tiers = listOf(ReminderTier("22:00", everySec = 900), ReminderTier("02:00", everySec = 60, challengeSteps = own)))

    @Test fun turning_it_on_starts_with_math_easy_twice_and_keeps_a_list_you_had() {
        val first = BedtimeChallenge.turnOn(SleepCfg())
        assertTrue(first.challenge)
        assertEquals(listOf(step("b0", math, 0, 2)), first.challengeSteps)
        // a list kept from before (off, then on again) is the one that comes back
        assertEquals(general, BedtimeChallenge.turnOn(SleepCfg(challengeSteps = general)).challengeSteps)
        // nothing readable left in it: the default, never an empty list
        assertEquals(Challenges.BEDTIME_DEFAULT, BedtimeChallenge.turnOn(SleepCfg(challengeSteps = emptyList())).challengeSteps)
    }

    @Test fun a_step_uses_the_general_list_until_it_gets_its_own() {
        val plain = night.tiers[0]; val owner = night.tiers[1]
        assertFalse(BedtimeChallenge.hasOwn(plain)); assertTrue(BedtimeChallenge.hasOwn(owner))
        assertFalse(BedtimeChallenge.hasOwn(plain.copy(challengeSteps = emptyList())))
        // "Own list" starts as a copy of the general one (same rows, same ids)
        val switched = BedtimeChallenge.useOwn(plain, night.generalChallenge)
        assertEquals(general, switched.challengeSteps); assertTrue(BedtimeChallenge.hasOwn(switched))
        // back to "Uses the general list": the own list is gone
        assertNull(BedtimeChallenge.useGeneral(owner).challengeSteps)
        assertFalse(BedtimeChallenge.hasOwn(BedtimeChallenge.useGeneral(owner)))
    }

    @Test fun the_screen_asks_the_list_of_the_step_in_force_after_the_editor_switches() {
        val bed = at(28, 22, 0)
        // 02:00 has its own list, 22:00 uses the general one
        assertEquals(general, RemindersEngine.challengeAt(night, bed, at(28, 22, 30)))
        assertEquals(own, RemindersEngine.challengeAt(night, bed, at(29, 2, 30)))
        // 22:00 gets its own list; 02:00 goes back to the general one
        val edited = night.copy(tiers = listOf(BedtimeChallenge.useOwn(night.tiers[0], general).let { it.copy(challengeSteps = it.challengeSteps!! + step("n", Challenges.SORT, 0, 1)) },
            BedtimeChallenge.useGeneral(night.tiers[1])))
        assertEquals(general + step("n", Challenges.SORT, 0, 1), RemindersEngine.challengeAt(edited, bed, at(28, 22, 30)))
        assertEquals(general, RemindersEngine.challengeAt(edited, bed, at(29, 2, 30)))
    }

    @Test fun editor_switches_ask_the_challenge_only_when_something_gets_easier_and_bedtime_is_important() {
        // a step gets its own list that is the general list plus a row: nothing easier
        val harder = night.copy(tiers = listOf(night.tiers[0].copy(challengeSteps = general + step("n", Challenges.SORT, 0, 1)), night.tiers[1]))
        assertFalse(RemindersEngine.bedtimeLooser(night, harder))
        // 02:00 drops its own (easier, Memory Easy x2) list for the general one (Math Medium x3): by id the old rows are gone
        assertTrue(RemindersEngine.bedtimeLooser(night, night.copy(tiers = listOf(night.tiers[0], BedtimeChallenge.useGeneral(night.tiers[1])))))
        // 22:00 gets an own list that lowers the general rows
        val lowered = night.copy(tiers = listOf(night.tiers[0].copy(challengeSteps = listOf(step("g", math, 0, 3))), night.tiers[1]))
        assertTrue(RemindersEngine.bedtimeLooser(night, lowered))
        // the same edits are free when bedtime is not Important
        val free = night.copy(guarded = false)
        assertFalse(RemindersEngine.bedtimeLooser(free, lowered.copy(guarded = false)))
        assertFalse(RemindersEngine.bedtimeLooser(free, free.copy(tiers = listOf(free.tiers[0], BedtimeChallenge.useGeneral(free.tiers[1])))))
    }

    @Test fun the_phrase_extras_belong_to_any_list_with_a_phrase_step() {
        assertFalse(BedtimeChallenge.hasPhraseStep(night))
        assertTrue(BedtimeChallenge.hasPhraseStep(night.copy(challengeSteps = general + step("p", phrase, 60))))
        // only a step's own list has it: still counts
        val inTier = night.copy(tiers = listOf(night.tiers[0], night.tiers[1].copy(challengeSteps = own + step("p", phrase, 30))))
        assertTrue(BedtimeChallenge.hasPhraseStep(inTier))
        // an unreadable row is no phrase step
        assertFalse(BedtimeChallenge.hasPhraseStep(night.copy(challengeSteps = listOf(step("x", "captcha", 0)))))
        assertEquals(listOf(general, own), BedtimeChallenge.lists(night))
        assertEquals(listOf(night.generalChallenge), BedtimeChallenge.lists(night.copy(tiers = emptyList())))
    }

    @Test fun lists_that_cannot_be_saved_say_why_and_nothing_is_asked_while_it_is_off() {
        assertNull(BedtimeChallenge.error(night))
        assertNull(BedtimeChallenge.error(night.copy(challenge = false, challengeSteps = emptyList())))      // off: its lists don't matter
        assertNotNull(BedtimeChallenge.error(night.copy(challengeSteps = emptyList())))                      // on with nothing in it
        assertEquals("Challenge before Dismiss: A challenge has up to 6 steps.",
            BedtimeChallenge.error(night.copy(challengeSteps = (0..6).map { step("s$it", math, 0) })))
        assertTrue(BedtimeChallenge.error(night.copy(challengeSteps = listOf(step("a", math, 9))))!!.startsWith("Challenge before Dismiss: "))
        // a step's own list: named by its time; empty means "uses the general list", which is fine
        val bad = night.copy(tiers = listOf(night.tiers[0].copy(challengeSteps = listOf(step("a", math, 0, 99))), night.tiers[1]))
        assertEquals("Challenge for the step from 22:00: Step 1: how many times must be 1 to 20.", BedtimeChallenge.error(bad))
        assertNull(BedtimeChallenge.error(night.copy(tiers = listOf(night.tiers[0].copy(challengeSteps = emptyList()), night.tiers[1]))))
    }

    @Test fun takes_about_follows_the_bedtime_phrase_extras() {
        assertEquals("under 1 min", BedtimeChallenge.takes(SleepCfg(), Challenges.BEDTIME_DEFAULT))        // Math Easy x2 = 12 s
        val slow = listOf(step("m", math, 3, 4), step("p", phrase, 250, 1))                                // 140 s + 103 s
        assertEquals("about 4 min", BedtimeChallenge.takes(SleepCfg(), slow))
        assertEquals("about 5 min", BedtimeChallenge.takes(SleepCfg(challengeGrid = true), slow))          // the grid is slower (x 1.3 on the phrase)
    }
}
