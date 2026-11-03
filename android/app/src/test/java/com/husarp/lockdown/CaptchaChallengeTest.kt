package com.husarp.lockdown

import com.husarp.lockdown.engine.CaptchaPuzzle
import com.husarp.lockdown.engine.CaptchaRenderer
import com.husarp.lockdown.engine.ChallengeRun
import com.husarp.lockdown.engine.ChallengeStep
import com.husarp.lockdown.engine.ChallengeVerdict
import com.husarp.lockdown.engine.Challenges
import com.husarp.lockdown.engine.PhraseOpts
import com.husarp.lockdown.ui.StepEdit
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

class CaptchaChallengeTest {
    private fun step(level: Int = 0, times: Int = 1) = ChallengeStep("c", "captcha", level, times)

    @Test fun levels_alphabet_and_cleaning_match_the_shared_format() {
        assertTrue("captcha" in Challenges.KINDS)
        assertEquals("Captcha", Challenges.kindLabel("captcha"))
        assertEquals(listOf(0, 1, 2), Challenges.levelChoices("captcha"))
        for (level in 0..2) {
            assertNull(Challenges.validate(listOf(step(level, 20))))
            assertEquals(listOf("Easy", "Medium", "Hard")[level], Challenges.levelLabel("captcha", level))
            repeat(100) { seed ->
                val p = Challenges.make(step(level), PhraseOpts(), Random(seed)) as CaptchaPuzzle
                assertEquals(4 + level, p.text.length)
                assertTrue(p.text.all { it in Challenges.CAPTCHA_ALPHABET })
                assertTrue(p.text.none { it in "0O1Il5S" })
            }
        }
        assertNotNull(Challenges.validate(listOf(step(3))))
        assertEquals(listOf(step(2, 20)), Challenges.clean(listOf(step(99, 99))))
        assertTrue(Challenges.stepsLooser(listOf(step(2)), listOf(step(1))))
        assertTrue(Challenges.stepsLooser(listOf(step()), emptyList()))
        assertFalse(Challenges.stepsLooser(listOf(step()), listOf(step(2))))
    }

    @Test fun views_carry_only_picture_and_length_and_the_engine_checks_case_insensitively() {
        var secret = ""
        val png = byteArrayOf(1, 2, 3)
        val renderer = CaptchaRenderer { text, level -> secret = text; assertEquals(1, level); png }
        val run = ChallengeRun(listOf(step(1)), rng = Random(4))
        val view = run.view(renderer)
        assertEquals(5, view.captchaLength)
        assertArrayEquals(png, view.captchaImage)
        assertEquals("", view.phrase); assertEquals("", view.expr)
        assertTrue(view.gridWords.isEmpty()); assertTrue(view.items.isEmpty())
        assertFalse(view.toString().contains(secret))
        assertEquals(ChallengeVerdict.PASSED, run.answer(secret.lowercase(), 5000))
        assertEquals(ChallengeVerdict.PASSED, run.answer("wrong", 5001))
    }

    @Test fun wrong_and_fast_answers_refresh_captcha_and_keep_completed_work() {
        var secret = ""
        val renderer = CaptchaRenderer { text, _ -> secret = text; byteArrayOf(1) }
        val run = ChallengeRun(listOf(step(times = 2), ChallengeStep("p", "phrase", 30)),
            PhraseOpts(own = "last phrase"), Random(9))
        run.view(renderer)
        val first = secret
        assertEquals(ChallengeVerdict.RIGHT, run.answer(first, 1000))
        run.view(renderer)
        val second = secret
        assertNotEquals(first, second)
        assertEquals(1, run.done)
        assertEquals(ChallengeVerdict.WRONG, run.answer("bad", 2000))
        run.view(renderer)
        assertNotEquals(second, secret)
        val third = secret
        assertEquals(1, run.done)
        run.shown(3000)
        assertEquals(ChallengeVerdict.WRONG, run.answer(third, 3599))
        assertTrue(run.lastTooFast)
        run.view(renderer)
        assertNotEquals(third, secret)
        assertEquals(ChallengeVerdict.RIGHT, run.answer(secret, 4199)) // exactly 150ms per char
        assertEquals(1, run.step); assertEquals(0, run.done)
        assertEquals("phrase", run.view().kind)
        assertEquals(ChallengeVerdict.PASSED, run.answer("last phrase", 2000000)) // no run expiry
    }

    @Test fun editor_add_switch_level_and_times_preserve_captcha() {
        val added = StepEdit.add(emptyList(), "captcha", rng = Random(1))
        assertEquals("captcha", added.single().kind)
        val hard = StepEdit.setTimes(StepEdit.setLevel(added, 0, 9), 0, 99)
        assertEquals(2, hard.single().level); assertEquals(20, hard.single().times)
        val math = StepEdit.setKind(hard, 0, "math")
        val back = StepEdit.setKind(math, 0, "captcha")
        assertEquals(added.single().id, back.single().id)
        assertEquals(0, back.single().level); assertEquals(20, back.single().times)
        assertNull(Challenges.validate(back))
    }
}
