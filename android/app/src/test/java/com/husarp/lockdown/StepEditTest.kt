package com.husarp.lockdown

import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.AntiBypassCfg
import com.husarp.lockdown.engine.ChallengeStep
import com.husarp.lockdown.engine.Challenges
import com.husarp.lockdown.ui.StepEdit
import kotlin.random.Random
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The step list editor's edits: move, remove, add, kind / level / times changes, and the Anti-Bypass config round trip. */
class StepEditTest {
    private val a = ChallengeStep("a", Challenges.MATH, 2, 5)
    private val b = ChallengeStep("b", Challenges.MEMORY, 1, 2)
    private val c = ChallengeStep("c", Challenges.PHRASE, 60, 1)

    @Test fun moveSwapsNeighboursAndStaysAtTheEnds() {
        assertEquals(listOf(b, a, c), StepEdit.move(listOf(a, b, c), 0, 1))
        assertEquals(listOf(a, c, b), StepEdit.move(listOf(a, b, c), 2, -1))
        assertEquals(listOf(a, b, c), StepEdit.move(listOf(a, b, c), 0, -1))
        assertEquals(listOf(a, b, c), StepEdit.move(listOf(a, b, c), 2, 1))
        assertEquals(listOf(a, b, c), StepEdit.move(listOf(a, b, c), 7, 1))
    }

    @Test fun removeKeepsTheMinimum() {
        assertEquals(listOf(a, c), StepEdit.remove(listOf(a, b, c), 1))
        assertEquals(listOf(a), StepEdit.remove(listOf(a), 0))                       // one stays while the challenge is on
        assertEquals(emptyList<ChallengeStep>(), StepEdit.remove(listOf(a), 0, min = 0))
        assertEquals(listOf(a, b), StepEdit.remove(listOf(a, b), 5))
    }

    @Test fun addGivesAFreshIdAndStopsAtTheMaximum() {
        var l = listOf(a)
        repeat(Challenges.MAX_STEPS - 1) { l = StepEdit.add(l, Challenges.SORT, rng = Random(it)) }
        assertEquals(Challenges.MAX_STEPS, l.size)
        assertEquals(l.size, l.map { it.id }.toSet().size)
        assertNull(Challenges.validate(l))
        assertEquals(l, StepEdit.add(l, Challenges.MATH))                            // full
        assertEquals(listOf(a), StepEdit.add(listOf(a), "bogus"))                    // unknown kind
        val p = StepEdit.add(listOf(a), Challenges.PHRASE, phraseLength = 120).last()
        assertEquals(120, p.level); assertEquals(1, p.times)
    }

    @Test fun kindChangeKeepsIdAndTimesAndResetsTheLevel() {
        val r = StepEdit.setKind(listOf(a, b), 0, Challenges.PHRASE, phraseLength = 90)
        assertEquals(ChallengeStep("a", Challenges.PHRASE, 90, 5), r[0])
        assertEquals(b, r[1])
        assertEquals(ChallengeStep("a", Challenges.MEMORY, 0, 5), StepEdit.setKind(listOf(a), 0, Challenges.MEMORY)[0])
        assertEquals(listOf(a), StepEdit.setKind(listOf(a), 0, "bogus"))
    }

    @Test fun levelAndTimesAreClamped() {
        assertEquals(3, StepEdit.setLevel(listOf(a), 0, 9)[0].level)                  // math tops out at Very hard
        assertEquals(0, StepEdit.setLevel(listOf(a), 0, -4)[0].level)
        assertEquals(Challenges.MAX_TIMES, StepEdit.setTimes(listOf(a), 0, 99)[0].times)
        assertEquals(1, StepEdit.setTimes(listOf(a), 0, 0)[0].times)
        assertEquals(AntiBypass.MIN_LENGTH, StepEdit.setLevel(listOf(c), 0, 3)[0].level)
    }

    @Test fun editsLeaveOtherStepsAndIdsAlone() {
        val l = listOf(a, b, c)
        val r = StepEdit.setTimes(StepEdit.setLevel(l, 1, 2), 2, 4)
        assertEquals(listOf("a", "b", "c"), r.map { it.id })
        assertEquals(a, r[0]); assertEquals(2, r[1].level); assertEquals(4, r[2].times)
    }

    @Test fun editingBackToTheMigratedListIsNoChange() {
        val saved = AntiBypassCfg(phrase = true, length = 60)                          // no list: reads as one 60-letter phrase
        val edited = StepEdit.cfgWith(saved, listOf(a, c), saved)
        assertEquals(listOf(a, c), edited.steps)
        assertEquals(60, edited.length)
        val back = StepEdit.cfgWith(edited, listOf(Challenges.migrated(60)), saved)
        assertEquals(saved, back)                                                      // dirty goes away
    }

    @Test fun firstPhraseStepSetsTheLength() {
        val saved = AntiBypassCfg(phrase = true, length = 60)
        val edited = StepEdit.cfgWith(saved, listOf(a, c.copy(level = 120)), saved)
        assertEquals(120, edited.length)
        assertNotNull(edited.steps)
        assertEquals(60, StepEdit.cfgWith(saved, listOf(a), saved).length)             // no phrase step: the length is left
    }

    @Test fun listedConfigStaysListedWhenItEqualsTheMigratedOne() {
        val saved = AntiBypassCfg(phrase = true, length = 60, steps = listOf(Challenges.migrated(60)))
        assertEquals(listOf(Challenges.migrated(60)), StepEdit.cfgWith(saved, listOf(Challenges.migrated(60)), saved).steps)
    }

    @Test fun theEditorsListsPassValidationAndTheLooserCheck() {
        val saved = AntiBypassCfg(phrase = true, length = 60)
        val more = StepEdit.cfgWith(saved, StepEdit.add(saved.stepList, Challenges.MATH, rng = Random(1)), saved)
        assertNull(Challenges.validate(more.stepList)); assertFalse(AntiBypass.settingsLooser(saved, more))        // adding is free
        val harder = StepEdit.cfgWith(saved, StepEdit.setTimes(saved.stepList, 0, 3), saved)
        assertFalse(AntiBypass.settingsLooser(saved, harder))
        val swapped = StepEdit.cfgWith(saved, StepEdit.setKind(saved.stepList, 0, Challenges.MATH), saved)
        assertTrue(AntiBypass.settingsLooser(saved, swapped))                                                        // another kind asks
        val removed = StepEdit.cfgWith(more, StepEdit.remove(more.stepList, 0), saved)
        assertTrue(AntiBypass.settingsLooser(more, removed))
    }
}
