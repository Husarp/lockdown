package com.husarp.lockdown

import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.AntiBypassCfg
import com.husarp.lockdown.engine.BreakCfg
import com.husarp.lockdown.engine.ChallengeRun
import com.husarp.lockdown.engine.ChallengeStep
import com.husarp.lockdown.engine.ChallengeVerdict
import com.husarp.lockdown.engine.Challenges
import com.husarp.lockdown.engine.PhraseOpts
import com.husarp.lockdown.engine.ReminderTier
import com.husarp.lockdown.engine.RemindersEngine
import com.husarp.lockdown.engine.SleepCfg
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.math.BigDecimal
import java.time.LocalDateTime
import kotlin.random.Random

/** The challenge kinds and the step list (Challenges.kt), the run, the looser rules for Anti-Bypass and the bedtime
 *  screen. The same rules as the PC's (the shared spec of the Loki round "challenges"). */
class ChallengeKindsTest {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val seeds = 1..300

    private fun step(id: String, kind: String, level: Int, times: Int = 1) = ChallengeStep(id, kind, level, times)
    private val math = Challenges.MATH
    private val memory = Challenges.MEMORY
    private val sort = Challenges.SORT
    private val phrase = Challenges.PHRASE

    // ---------- reading what the screen shows, as a user would ----------

    private fun eval(e: String): Pair<Long, Boolean> {
        Regex("""^(\d+)x ([+−]) (\d+) = (\d+)$""").matchEntire(e)?.let { m ->
            val (a, op, b, c) = m.destructured
            return (if (op == "+") (c.toLong() - b.toLong()) / a.toLong() else (c.toLong() + b.toLong()) / a.toLong()) to true
        }
        Regex("""^\((\d+) ([+−]) (\d+)\) × (\d+) ([+−]) (\d+)$""").matchEntire(e)?.let { m ->
            val (a, op1, b, c, op2, d) = m.destructured
            val inner = if (op1 == "+") a.toLong() + b.toLong() else a.toLong() - b.toLong()
            return inner * c.toLong() + (if (op2 == "+") d.toLong() else -d.toLong()) to false
        }
        val m = Regex("""^(\d+) ([+−×÷]) (\d+)$""").matchEntire(e) ?: error("unreadable: $e")
        val (a, op, b) = m.destructured
        return when (op) {
            "+" -> a.toLong() + b.toLong()
            "−" -> a.toLong() - b.toLong()
            "×" -> a.toLong() * b.toLong()
            else -> { assertEquals("exact division", 0L, a.toLong() % b.toLong()); a.toLong() / b.toLong() }
        } to false
    }

    private fun value(t: String): BigDecimal = when {
        t.endsWith("%") -> BigDecimal(t.dropLast(1)).divide(BigDecimal(100))
        "/" in t -> t.split("/").let { (a, b) -> BigDecimal(a).divide(BigDecimal(b)) }
        else -> BigDecimal(t)
    }

    private fun sortedOrder(items: List<String>) = items.indices.sortedBy { value(items[it]) }

    /** Answers the current puzzle right, [at] ms in. */
    private fun solve(run: ChallengeRun, at: Long): ChallengeVerdict {
        val v = run.view()
        return when (v.kind) {
            math -> run.answer(eval(v.expr).first.toString(), at)
            memory -> run.answerTaps(v.lit, at)
            sort -> run.answerTaps(sortedOrder(v.items), at)
            else -> run.answer(if (v.gridWords.isNotEmpty()) v.gridWords.joinToString(" ") else v.phrase, at)
        }
    }

    private fun run(vararg steps: ChallengeStep, opts: PhraseOpts = PhraseOpts(), seed: Int = 1) =
        ChallengeRun(steps.toList(), opts, Random(seed), 0L)

    // ---------- math ----------

    @Test fun math_easy_is_two_digit_plus_or_minus_never_negative() {
        val seen = HashSet<String>()
        for (s in seeds) {
            val p = Challenges.newMath(0, Random(s))
            val m = Regex("""^(\d+) ([+−]) (\d+)$""").matchEntire(p.expr)!!
            val (a, op, b) = m.destructured
            seen += op
            assertTrue(a.toInt() in 10..99 && b.toInt() in 10..99)
            if (op == "−") assertTrue(a.toInt() >= b.toInt())
            assertTrue(p.answer >= 0); assertEquals(eval(p.expr).first, p.answer); assertFalse(p.findX)
        }
        assertEquals(setOf("+", "−"), seen)
    }

    @Test fun math_medium_adds_times_and_exact_division() {
        val ops = HashSet<String>()
        for (s in seeds) {
            val p = Challenges.newMath(1, Random(s))
            val (a, op, b) = Regex("""^(\d+) ([+−×÷]) (\d+)$""").matchEntire(p.expr)!!.destructured
            ops += op
            when (op) {
                "×" -> assertTrue(a.toInt() in 2..19 && b.toInt() in 2..9)
                "÷" -> { assertTrue(b.toInt() in 2..12); assertTrue((a.toInt() / b.toInt()) in 2..12); assertEquals(0, a.toInt() % b.toInt()) }
                "−" -> assertTrue(a.toInt() >= b.toInt() && a.toInt() in 10..99)
                else -> assertTrue(a.toInt() in 10..99 && b.toInt() in 10..99)
            }
            assertEquals(eval(p.expr).first, p.answer); assertTrue(p.answer >= 0)
        }
        assertEquals(setOf("+", "−", "×", "÷"), ops)
    }

    @Test fun math_hard_is_three_steps_with_brackets_and_never_negative() {
        for (s in seeds) {
            val p = Challenges.newMath(2, Random(s))
            val (a, op1, b, c, _, d) = Regex("""^\((\d+) ([+−]) (\d+)\) × (\d+) ([+−]) (\d+)$""").matchEntire(p.expr)!!.destructured
            assertTrue(a.toInt() in 10..99 && b.toInt() in 10..99 && c.toInt() in 2..9 && d.toInt() in 10..99)
            if (op1 == "−") assertTrue(a.toInt() > b.toInt())
            assertTrue(p.answer >= 0); assertEquals(eval(p.expr).first, p.answer)
        }
    }

    @Test fun math_very_hard_is_two_digit_times_or_find_x() {
        var times = 0; var findX = 0
        for (s in seeds) {
            val p = Challenges.newMath(3, Random(s))
            if (p.findX) {
                findX++
                val (a, _, b, c) = Regex("""^(\d+)x ([+−]) (\d+) = (\d+)$""").matchEntire(p.expr)!!.destructured
                assertTrue(a.toInt() in 2..12 && b.toInt() in 1..99 && c.toInt() > 0)
                assertTrue(p.answer in 2..20)
                assertEquals(eval(p.expr).first, p.answer)
            } else {
                times++
                val (a, op, b) = Regex("""^(\d+) (×) (\d+)$""").matchEntire(p.expr)!!.destructured
                assertTrue(a.toInt() in 11..99 && b.toInt() in 11..99); assertEquals("×", op)
                assertEquals(a.toLong() * b.toLong(), p.answer)
            }
        }
        assertTrue(times > 50 && findX > 50)
    }

    @Test fun a_math_answer_is_one_whole_number_after_trimming_spaces() {
        val p = Challenges.newMath(0, Random(5))
        val right = p.answer.toString()
        assertTrue(Challenges.mathOk(p, right)); assertTrue(Challenges.mathOk(p, "  $right "))
        assertFalse(Challenges.mathOk(p, "$right.0")); assertFalse(Challenges.mathOk(p, "-$right")); assertFalse(Challenges.mathOk(p, "+$right"))
        assertFalse(Challenges.mathOk(p, "")); assertFalse(Challenges.mathOk(p, "1 $right")); assertFalse(Challenges.mathOk(p, "abc"))
        assertFalse(Challenges.mathOk(p, (p.answer + 1).toString())); assertFalse(Challenges.mathOk(p, "9".repeat(40)))
    }

    // ---------- memory grid ----------

    @Test fun memory_levels_size_count_and_order() {
        for (s in seeds) {
            for ((level, spec) in listOf(0 to Triple(4, 4, false), 1 to Triple(5, 6, false), 2 to Triple(5, 8, true))) {
                val p = Challenges.newMemory(level, Random(s))
                assertEquals(spec.first, p.size); assertEquals(spec.second, p.lit.size); assertEquals(spec.third, p.ordered)
                assertEquals(p.lit.size, p.lit.toSet().size)                                       // distinct squares
                assertTrue(p.lit.all { it in 0 until p.size * p.size })
            }
        }
        assertTrue((1..30).any { Challenges.newMemory(2, Random(it)).lit != Challenges.newMemory(2, Random(it)).lit.sorted() })   // hard: really in an order
    }

    @Test fun memory_any_order_on_easy_and_medium_exact_sequence_on_hard() {
        val easy = Challenges.newMemory(0, Random(3))
        assertTrue(Challenges.memoryOk(easy, easy.lit.reversed())); assertTrue(Challenges.memoryOk(easy, easy.lit))
        assertFalse(Challenges.memoryOk(easy, easy.lit.dropLast(1)))                               // one short
        assertFalse(Challenges.memoryOk(easy, easy.lit + 0))                                       // one more
        assertFalse(Challenges.memoryOk(easy, easy.lit.dropLast(1) + easy.lit.first()))            // a square twice, one missing
        val other = (0 until 16).first { it !in easy.lit }
        assertFalse(Challenges.memoryOk(easy, easy.lit.dropLast(1) + other))
        val hard = Challenges.newMemory(2, Random(3))
        assertTrue(Challenges.memoryOk(hard, hard.lit))
        assertFalse(Challenges.memoryOk(hard, hard.lit.reversed()))
        assertFalse(Challenges.memoryOk(hard, hard.lit.sorted().takeIf { it != hard.lit } ?: hard.lit.reversed()))
    }

    // ---------- sorting ----------

    @Test fun sorting_easy_six_whole_numbers() {
        for (s in seeds) {
            val p = Challenges.newSort(0, Random(s))
            assertEquals(6, p.items.size); assertEquals(6, p.items.toSet().size)
            assertTrue(p.items.all { it.toInt() in 1..99 })
            assertEquals(p.order, sortedOrder(p.items))
        }
    }

    @Test fun sorting_medium_eight_numbers_some_with_a_decimal() {
        for (s in seeds) {
            val p = Challenges.newSort(1, Random(s))
            assertEquals(8, p.items.size)
            val v = p.items.map(::value)
            assertEquals(8, v.toSet().size)                                                         // all different
            assertTrue(v.all { it >= BigDecimal(-20) && it < BigDecimal(100) })
            assertTrue(p.items.count { "." in it } in 2..5)
            assertTrue(p.items.none { "/" in it || "%" in it })
            assertEquals(p.order, sortedOrder(p.items))
        }
        assertTrue((1..300).any { Challenges.newSort(1, Random(it)).items.any { t -> t.startsWith("-") } })
    }

    @Test fun sorting_hard_ten_mixed_with_one_of_each_form_and_exact_values() {
        for (s in seeds) {
            val p = Challenges.newSort(2, Random(s))
            assertEquals(10, p.items.size)
            val v = p.items.map(::value)
            assertEquals(10, v.toSet().size)
            assertTrue(v.all { it.abs() <= BigDecimal(3) })
            assertTrue(p.items.any { "/" in it }); assertTrue(p.items.any { it.endsWith("%") })
            assertTrue(p.items.any { "." in it }); assertTrue(p.items.any { it.startsWith("-") })
            assertTrue(p.items.filter { "/" in it }.all { it.substringAfter("/").toInt() in listOf(2, 4, 5, 8) })
            assertTrue(p.items.filter { "/" in it }.all { f -> f.removePrefix("-").split("/").let { (a, b) -> a.toBigInteger().gcd(b.toBigInteger()) == 1.toBigInteger() } })   // lowest terms
            assertTrue(p.items.filter { "/" in it }.all { f -> f.removePrefix("-").split("/").let { (a, b) -> a.toInt() < 3 * b.toInt() } })
            assertNotEquals(p.order, p.items.indices.toList())                                       // not shown already in order
            assertEquals(p.order, sortedOrder(p.items))
            assertEquals(v.sorted(), p.order.map { v[it] })
        }
    }

    @Test fun sorting_checker_wants_every_item_once_smallest_first() {
        val p = Challenges.newSort(0, Random(9))
        assertTrue(Challenges.sortOk(p, p.order))
        assertFalse(Challenges.sortOk(p, p.order.reversed())); assertFalse(Challenges.sortOk(p, p.order.dropLast(1)))
        assertFalse(Challenges.sortOk(p, p.order.dropLast(1) + p.order.first()))
        assertFalse(Challenges.sortOk(p, emptyList()))
    }

    // ---------- too fast, wrong, new puzzles ----------

    @Test fun too_fast_counts_as_wrong_and_makes_a_new_puzzle() {
        val r = run(step("a", math, 0, 2))
        val first = r.view().expr
        assertEquals(ChallengeVerdict.WRONG, solve(r, 1199))                                       // right, but under 1200 ms
        assertTrue(r.lastTooFast); assertNotEquals(first, r.view().expr); assertEquals(0, r.view().done)
        assertEquals(ChallengeVerdict.WRONG, solve(r, 1199 + 1199))                               // the clock restarted at the last answer
        assertEquals(ChallengeVerdict.RIGHT, solve(r, 1199 + 1199 + 1200))
        assertFalse(r.lastTooFast); assertEquals(1, r.view().done)
        // sorting: 150 ms an item; memory: 150 ms a lit square
        val s = run(step("a", sort, 0)); assertEquals(ChallengeVerdict.WRONG, solve(s, 899)); assertTrue(s.lastTooFast)
        assertEquals(ChallengeVerdict.PASSED, solve(s, 899 + 900))
        val m = run(step("a", memory, 0)); assertEquals(ChallengeVerdict.WRONG, solve(m, 599)); assertEquals(ChallengeVerdict.PASSED, solve(m, 599 + 600))
        val h = run(step("a", memory, 2)); assertEquals(ChallengeVerdict.WRONG, solve(h, 1199)); assertEquals(ChallengeVerdict.PASSED, solve(h, 1199 + 1200))
    }

    @Test fun the_screen_can_restart_the_clock_when_it_really_draws_the_puzzle() {
        val r = run(step("a", math, 0))
        r.shown(50_000)
        assertEquals(ChallengeVerdict.WRONG, solve(r, 50_500))
        r.shown(60_000)
        assertEquals(ChallengeVerdict.PASSED, solve(r, 61_200))
    }

    // ---------- the run ----------

    @Test fun a_run_walks_the_steps_in_order() {
        val r = run(step("a", math, 0, 2), step("b", memory, 0, 1), step("c", sort, 1, 1), step("d", phrase, 30, 1))
        var t = 0L
        fun next(): ChallengeVerdict { t += 5_000; return solve(r, t) }
        var v = r.view()
        assertEquals(listOf("Math, Easy", "Memory grid, Easy", "Sorting, Medium", "Typed phrase, Short"), v.steps.map { it.label })
        assertEquals(listOf(2, 1, 1, 1), v.steps.map { it.times })
        assertEquals(0 to 0, v.step to v.done); assertEquals(math, v.kind); assertTrue(v.expr.isNotEmpty())
        assertEquals(ChallengeVerdict.RIGHT, next()); v = r.view(); assertEquals(0 to 1, v.step to v.done)
        assertEquals(ChallengeVerdict.RIGHT, next()); v = r.view(); assertEquals(1 to 0, v.step to v.done)
        assertEquals(memory, v.kind); assertEquals(4, v.size); assertEquals(4, v.lit.size)
        assertEquals(ChallengeVerdict.RIGHT, next()); assertEquals(sort, r.view().kind); assertEquals(8, r.view().items.size)
        assertEquals(ChallengeVerdict.RIGHT, next()); v = r.view()
        assertEquals(phrase, v.kind); assertEquals(30, v.phrase.replace(" ", "").length)
        assertFalse(r.passed)
        assertEquals(ChallengeVerdict.PASSED, next())
        assertTrue(r.passed); assertTrue(r.view().passed)
        assertEquals(ChallengeVerdict.PASSED, r.answer("anything", t + 5_000))                    // nothing more to do, nothing reopens
    }

    @Test fun a_wrong_answer_gives_a_new_puzzle_of_the_same_step_and_keeps_what_is_done() {
        val r = run(step("a", math, 1, 3), step("b", sort, 0, 1))
        var t = 0L
        t += 5_000; assertEquals(ChallengeVerdict.RIGHT, solve(r, t))
        t += 5_000; assertEquals(ChallengeVerdict.RIGHT, solve(r, t))
        val before = r.view()
        assertEquals(2, before.done)
        t += 5_000; assertEquals(ChallengeVerdict.WRONG, r.answer("not it", t))
        var after = r.view()
        assertEquals(0 to 2, after.step to after.done); assertEquals(before.steps, after.steps)
        assertNotEquals(before.expr, after.expr)                                                   // a new puzzle, not the same one again
        t += 5_000; assertEquals(ChallengeVerdict.RIGHT, solve(r, t))
        assertEquals(1 to 0, r.view().let { it.step to it.done })
        // sorting: a wrong order is wrong, a new puzzle
        val items = r.view().items
        t += 5_000; assertEquals(ChallengeVerdict.WRONG, r.answerTaps(sortedOrder(items).reversed(), t))
        assertNotEquals(items, r.view().items); assertEquals(1, r.view().step)
        t += 5_000; assertEquals(ChallengeVerdict.PASSED, solve(r, t))
        // an answer of the wrong form for the puzzle is just wrong
        val m = run(step("a", memory, 0)); assertEquals(ChallengeVerdict.WRONG, m.answer("5", 9_000))
        val x = run(step("a", math, 0)); assertEquals(ChallengeVerdict.WRONG, x.answerTaps(listOf(1, 2), 9_000))
    }

    @Test fun cancel_is_dropping_the_run_the_next_one_starts_over() {
        val a = run(step("a", math, 0, 3), seed = 1)
        solve(a, 5_000); solve(a, 10_000)
        assertEquals(2, a.view().done)
        val b = run(step("a", math, 0, 3), seed = 2)                                               // (a new object; nothing is shared)
        assertEquals(0, b.view().step); assertEquals(0, b.view().done); assertFalse(b.passed)
        assertEquals(2, a.view().done)
    }

    @Test fun a_typed_phrase_keeps_its_phrase_on_mismatch_and_too_fast() {
        val r = run(step("a", phrase, 30, 2))
        val p = r.view().phrase
        assertEquals(30, p.replace(" ", "").length); assertTrue(p.matches(Regex("([a-z]{5} ){5}[a-z]{5}")))
        assertEquals(ChallengeVerdict.MISMATCH, r.answer(p.dropLast(1) + "!", 10_000)); assertEquals(p, r.view().phrase)
        assertEquals(ChallengeVerdict.MISMATCH, r.answer("", 10_000))
        assertEquals(ChallengeVerdict.TOO_FAST, r.answer(p, 35L * p.length - 1)); assertEquals(p, r.view().phrase)
        assertTrue(r.lastTooFast)
        assertEquals(ChallengeVerdict.TOO_FAST, r.answer(p, 35L * p.length - 1 + 35L * p.length - 1))   // the clock started over
        val t = 35L * p.length - 1 + 35L * p.length - 1 + 35L * p.length
        assertEquals(ChallengeVerdict.RIGHT, r.answer("  $p ", t))                                 // trimmed, as the phone always did
        assertEquals(1, r.view().done); assertNotEquals(p, r.view().phrase)                      // the next phrase is a new one
        assertEquals(ChallengeVerdict.PASSED, solve(r, t + 100_000))
    }

    @Test fun phrase_extras_capitals_numbers_grid_and_your_own_phrase() {
        val c = run(step("a", phrase, 200, 1), opts = PhraseOpts(complex = true))
        val text = c.view().phrase.replace(" ", "")
        assertEquals(200, text.length); assertTrue(text.any { it.isUpperCase() } && text.any { it.isDigit() })
        val g = run(step("a", phrase, 30, 1), opts = PhraseOpts(grid = true))
        val words = g.view().gridWords
        assertEquals(6, words.size); assertEquals("", g.view().phrase)
        assertEquals(ChallengeVerdict.MISMATCH, g.answer(words.reversed().joinToString(" "), 99_000))
        assertEquals(ChallengeVerdict.MISMATCH, g.answer(words.dropLast(1).joinToString(" "), 99_000))
        assertEquals(ChallengeVerdict.TOO_FAST, g.answer(words.joinToString(" "), 30 * 35L - 1))      // 30 letters x 35 ms (no spaces in the grid)
        assertEquals(ChallengeVerdict.PASSED, g.answer(words.joinToString(" "), 30 * 35L - 1 + 30 * 35L))
        val own = run(step("a", phrase, 30, 1), opts = PhraseOpts(own = "my own  words here"))
        assertEquals("my own  words here", own.view().phrase)
        assertEquals(ChallengeVerdict.PASSED, own.answer("my own  words here", 10_000))
        val blank = run(step("a", phrase, 30, 1), opts = PhraseOpts(own = "   "))              // a blank one would pass by typing nothing
        assertEquals(30, blank.view().phrase.replace(" ", "").length)
        assertEquals(ChallengeVerdict.MISMATCH, blank.answer("", 10_000))
    }

    @Test fun a_run_with_nothing_readable_asks_a_phrase_never_nothing() {
        val r = ChallengeRun(listOf(step("x", "bogus", 1, 1)), PhraseOpts(), Random(1), 0L)
        assertEquals(phrase, r.view().kind); assertEquals(60, r.view().level)
        assertFalse(r.passed)
        assertEquals(phrase, ChallengeRun(emptyList(), PhraseOpts(), Random(1), 0L).view().kind)
    }

    // ---------- validation, reading, labels, time ----------

    @Test fun validation_refuses_what_is_out_of_range() {
        assertNull(Challenges.validate(listOf(step("a", math, 3, 20), step("b", phrase, 2000, 1), step("c", memory, 0, 1), step("d", sort, 2, 5))))
        assertNull(Challenges.validate(emptyList(), allowEmpty = true))
        assertNotNull(Challenges.validate(emptyList()))
        assertNotNull(Challenges.validate(List(7) { step("s$it", math, 0) }))
        assertNull(Challenges.validate(List(6) { step("s$it", math, 0) }))
        assertNotNull(Challenges.validate(listOf(step("a", "password", 0))))
        assertNotNull(Challenges.validate(listOf(step("a", math, 4)))); assertNotNull(Challenges.validate(listOf(step("a", math, -1))))
        assertNotNull(Challenges.validate(listOf(step("a", memory, 3)))); assertNotNull(Challenges.validate(listOf(step("a", sort, 3))))
        assertNotNull(Challenges.validate(listOf(step("a", phrase, 29)))); assertNotNull(Challenges.validate(listOf(step("a", phrase, 2001))))
        assertNotNull(Challenges.validate(listOf(step("a", math, 0, 0)))); assertNotNull(Challenges.validate(listOf(step("a", math, 0, 21))))
        assertNotNull(Challenges.validate(listOf(step("", math, 0)))); assertNotNull(Challenges.validate(listOf(step("a b", math, 0))))
        assertNotNull(Challenges.validate(listOf(step("x".repeat(25), math, 0))))
        assertNull(Challenges.validate(listOf(step("x".repeat(24), math, 0), step("A_b-9", math, 0))))
        assertNotNull(Challenges.validate(listOf(step("a", math, 0), step("a", memory, 0))))        // a repeated id
    }

    @Test fun reading_a_stored_list_is_lenient() {
        val got = Challenges.clean(listOf(step("a", "bogus", 1), step("b", math, 9, 99), step("c", phrase, 5, 0), step("b", sort, -3, 2), step("", memory, 1)))
        assertEquals(listOf(step("b", math, 3, 20), step("c", phrase, 30, 1), step("r2", sort, 0, 2), step("r3", memory, 1, 1)), got)
        assertEquals(6, Challenges.clean(List(9) { step("s$it", math, 0) }).size)
        assertEquals(emptyList<ChallengeStep>(), Challenges.clean(listOf(step("a", "bogus", 0))))
        assertEquals(emptyList<ChallengeStep>(), Challenges.clean(null))
        assertNull(Challenges.validate(got))                                                       // what is read is always savable
    }

    @Test fun labels_and_new_rows() {
        assertEquals("Math", Challenges.kindLabel(math)); assertEquals("Memory grid", Challenges.kindLabel(memory))
        assertEquals("Sorting", Challenges.kindLabel(sort)); assertEquals("Typed phrase", Challenges.kindLabel(phrase))
        assertEquals(listOf("Easy", "Medium", "Hard", "Very hard"), (0..3).map { Challenges.levelLabel(math, it) })
        assertEquals(listOf("Easy", "Medium", "Hard"), (0..2).map { Challenges.levelLabel(sort, it) })
        assertEquals(listOf("Short", "Medium", "Long", "Very long", "73 letters"), listOf(30, 60, 120, 250, 73).map { Challenges.levelLabel(phrase, it) })
        assertEquals("Memory grid, Hard", Challenges.stepLabel(step("a", memory, 2)))
        assertEquals(listOf(30, 60, 120, 250), Challenges.levelChoices(phrase)); assertEquals(listOf(0, 1, 2, 3), Challenges.levelChoices(math))
        val taken = setOf("a", "b")
        val n = Challenges.newStep(phrase, taken, 120, Random(1))
        assertEquals(phrase, n.kind); assertEquals(120, n.level); assertEquals(1, n.times); assertTrue(n.id !in taken); assertNull(Challenges.validate(listOf(n)))
        assertEquals(step("a", math, 0, 4), Challenges.withKind(step("a", phrase, 250, 4), math))
        assertEquals(step("a", phrase, 60, 4), Challenges.withKind(step("a", math, 3, 4), phrase))
    }

    @Test fun takes_about_adds_up_every_go() {
        assertEquals(6 * 5 + 10 * 2 + 20, Challenges.takesSeconds(listOf(step("a", math, 0, 5), step("b", sort, 1, 2), step("c", sort, 2, 1))))
        assertEquals(8 + 10 + 15 + 6 + 10 + 20 + 35, Challenges.takesSeconds(listOf(step("a", memory, 0), step("b", memory, 1), step("c", memory, 2),
            step("d", math, 0), step("e", math, 1), step("f", math, 2), step("g", math, 3))))
        assertEquals(27, Challenges.takesSeconds(listOf(step("a", phrase, 60))))                    // 3 + 0.4 x 60
        assertEquals(35, Challenges.takesSeconds(listOf(step("a", phrase, 60)), PhraseOpts(grid = true)))   // x 1.3
        assertEquals(6, Challenges.takesSeconds(listOf(step("a", phrase, 1000)), PhraseOpts(own = "ab cd ef gh")))   // its own 8 letters: 3 + 3.2
        assertEquals("under 1 min", Challenges.takesText(59)); assertEquals("about 1 min", Challenges.takesText(60))
        assertEquals("about 3 min", Challenges.takesText(170)); assertEquals("about 1 min", Challenges.takesText(89)); assertEquals("about 2 min", Challenges.takesText(90))
        assertEquals("", Challenges.takesNote(600)); assertEquals("This will take about 14 min.", Challenges.takesNote(840))
    }

    // ---------- stricter / looser, row by row ----------

    @Test fun the_looser_table_row_by_row() {
        val old = listOf(step("a", math, 2, 5), step("b", memory, 1, 2), step("c", phrase, 60, 1))
        fun looser(new: List<ChallengeStep>, oldOwn: Int? = null, newOwn: Int? = null) = Challenges.stepsLooser(old, new, oldOwn, newOwn)
        assertFalse(looser(old))                                                                   // nothing changed
        assertFalse(looser(old + step("d", sort, 0, 1)))                                           // add a step: free
        assertFalse(looser(listOf(step("a", math, 3, 5)) + old.drop(1)))                           // raise a level: free
        assertFalse(looser(listOf(step("a", math, 2, 6)) + old.drop(1)))                           // more times: free
        assertFalse(looser(old.reversed()))                                                        // reorder: free
        assertFalse(looser(listOf(old[2], old[0], old[1], step("z", sort, 0))))
        assertTrue(looser(old.drop(1)))                                                            // remove a step
        assertTrue(looser(listOf(step("a", math, 1, 5)) + old.drop(1)))                            // lower a level
        assertTrue(looser(listOf(step("a", math, 2, 4)) + old.drop(1)))                            // fewer times
        assertTrue(looser(listOf(step("a", sort, 2, 5)) + old.drop(1)))                            // another kind, even a higher level
        assertTrue(looser(listOf(step("a", math, 2, 5), step("b", memory, 1, 2), step("c", phrase, 30, 1))))   // a shorter phrase
        assertFalse(looser(listOf(step("a", math, 2, 5), step("b", memory, 1, 2), step("c", phrase, 120, 1))))
        assertTrue(looser(listOf(step("x", math, 2, 5), step("b", memory, 1, 2), step("c", phrase, 60, 1))))   // a new id = the old row is gone
        // the phrase's strength is your own phrase's letters while one is in use
        assertTrue(looser(old, oldOwn = null, newOwn = 40))                                        // random 60 -> yours with 40 letters
        assertFalse(looser(old, oldOwn = null, newOwn = 80))
        assertFalse(looser(old, oldOwn = 50, newOwn = null))                                       // yours (50) -> a random 60
        assertTrue(looser(listOf(old[0], old[1], step("c", phrase, 30, 1)), oldOwn = 50, newOwn = null))   // ... unless it is shorter
        assertFalse(Challenges.stepsLooser(emptyList(), listOf(step("a", math, 0))))               // from nothing: free
        assertTrue(Challenges.stepsLooser(old, emptyList()))
    }

    @Test fun anti_bypass_settings_looser_follows_the_list_and_keeps_every_old_check() {
        val ab = AntiBypassCfg(phrase = true, length = 60, steps = listOf(step("a", math, 2, 5), step("b", memory, 1, 2), step("c", phrase, 60, 1)))
        fun loose(new: AntiBypassCfg) = AntiBypass.settingsLooser(ab, new)
        assertFalse(loose(ab))
        assertFalse(loose(ab.copy(steps = ab.steps!! + step("d", sort, 0))))
        assertFalse(loose(ab.copy(steps = ab.steps!!.reversed())))
        assertFalse(loose(ab.copy(steps = listOf(step("a", math, 3, 9)) + ab.steps!!.drop(1))))
        assertTrue(loose(ab.copy(steps = ab.steps!!.drop(1))))
        assertTrue(loose(ab.copy(steps = listOf(step("a", math, 1, 5)) + ab.steps!!.drop(1))))
        assertTrue(loose(ab.copy(steps = listOf(step("a", math, 2, 4)) + ab.steps!!.drop(1))))
        assertTrue(loose(ab.copy(steps = listOf(step("a", sort, 2, 5)) + ab.steps!!.drop(1))))
        // the old checks stay: challenge off, the extras, wait, hours
        assertTrue(loose(ab.copy(phrase = false)))
        assertTrue(AntiBypass.settingsLooser(ab.copy(grid = true), ab)); assertTrue(AntiBypass.settingsLooser(ab.copy(complex = true), ab))
        assertTrue(AntiBypass.settingsLooser(ab.copy(waitMin = 5), ab))
        assertTrue(loose(ab.copy(useOwn = true, customPhrase = "x".repeat(200))))                  // yours is weaker, however long
        // the grid and capitals stay global even when no step is a phrase
        val noPhrase = AntiBypassCfg(phrase = true, grid = true, steps = listOf(step("a", math, 0)))
        assertTrue(AntiBypass.settingsLooser(noPhrase, noPhrase.copy(grid = false)))
        // challenge off: nothing of the list matters
        assertFalse(AntiBypass.settingsLooser(AntiBypassCfg(), AntiBypassCfg(phrase = true, steps = listOf(step("a", math, 0)))))
        assertFalse(AntiBypass.settingsLooser(ab.copy(phrase = false), ab.copy(phrase = false, steps = listOf(step("a", math, 0)))))
        // your own phrase in use: its letters are the strength of the phrase step
        val own = AntiBypassCfg(phrase = true, useOwn = true, customPhrase = "x".repeat(70), steps = listOf(step("c", phrase, 60, 1)))
        assertTrue(AntiBypass.settingsLooser(own, own.copy(useOwn = false)))                       // yours (70) -> a random 60: shorter
        val own50 = own.copy(customPhrase = "x".repeat(50))
        assertFalse(AntiBypass.settingsLooser(own50, own50.copy(useOwn = false)))                  // yours (50) -> a random 60: stronger
        assertTrue(AntiBypass.settingsLooser(own50, own50.copy(useOwn = false, steps = listOf(step("c", phrase, 40, 1)))))
    }

    @Test fun an_old_config_reads_as_one_phrase_step_and_new_ones_round_trip() {
        val old = json.decodeFromString(AntiBypassCfg.serializer(), """{"phrase":true,"length":120,"grid":true}""")
        assertNull(old.steps)
        assertEquals(listOf(step("s0", phrase, 120, 1)), old.stepList)
        assertEquals(listOf(step("s0", phrase, 60, 1)), AntiBypassCfg().stepList)                  // default length 60
        assertEquals(listOf(step("s0", phrase, 30, 1)), AntiBypassCfg(length = 5).stepList)        // (never out of range)
        assertEquals(listOf(step("s0", phrase, 120, 1)), AntiBypassCfg(length = 120, steps = emptyList()).stepList)   // empty while on: the same
        assertEquals(listOf(step("s0", phrase, 120, 1)), AntiBypassCfg(length = 120, steps = listOf(step("x", "bogus", 0))).stepList)
        // an old reader's file (no steps) and a new one's differ only in that list
        val mixed = AntiBypassCfg(phrase = true, steps = listOf(step("a", math, 1, 3), step("b", phrase, 90, 2)))
        val back = json.decodeFromString(AntiBypassCfg.serializer(), json.encodeToString(AntiBypassCfg.serializer(), mixed))
        assertEquals(mixed, back); assertEquals(mixed.steps, back.stepList)
        // an older copy (the Island helper before this version) does not know `steps` and still reads the rest
        val oldReader = json.decodeFromString<OldAb>(json.encodeToString(AntiBypassCfg.serializer(), mixed))
        assertTrue(oldReader.phrase); assertEquals(60, oldReader.length)
        // a half-written row does not stop the config reading; it is cleaned instead
        val rough = json.decodeFromString(AntiBypassCfg.serializer(), """{"phrase":true,"steps":[{"kind":"math"},{"id":"q","kind":"nope"},{"id":"m","kind":"memory","level":9,"times":99}]}""")
        assertEquals(listOf(step("r0", math, 0, 1), step("m", memory, 2, 20)), rough.stepList)
        // the migrated step is the old behaviour: same phrase length, once, same extras
        assertEquals(60, AntiBypass.phraseFor(AntiBypassCfg(phrase = true)).replace(" ", "").length)
        val run = AntiBypass.newRun(old, 0L, Random(2))
        assertEquals(phrase, run.view().kind); assertEquals(1, run.view().times)
        assertEquals(120, run.view().gridWords.joinToString("").length)                           // old.grid applies to the migrated step
    }

    @kotlinx.serialization.Serializable
    private data class OldAb(val phrase: Boolean = false, val length: Int = 60)

    @Test fun the_anti_bypass_run_takes_the_extras_and_your_own_phrase() {
        val cfg = AntiBypassCfg(phrase = true, complex = true, useOwn = true, customPhrase = "my own words", steps = listOf(step("a", phrase, 30, 1), step("b", math, 0, 1)))
        val r = AntiBypass.newRun(cfg, 0L, Random(1))
        assertEquals("my own words", r.view().phrase)
        assertEquals(PhraseOpts(grid = false, complex = true, own = "my own words"), AntiBypass.phraseOpts(cfg))
        assertEquals(PhraseOpts(complex = true), AntiBypass.phraseOpts(cfg.copy(useOwn = false)))
        assertEquals(Challenges.takesSeconds(cfg.stepList, AntiBypass.phraseOpts(cfg)), AntiBypass.takesSeconds(cfg))
    }

    // ---------- bedtime ----------

    private fun at(day: Int, h: Int, m: Int) = LocalDateTime.of(2026, 9, day, h, m)
    private val noBreak = BreakCfg(on = false)
    private val listA = listOf(step("a", math, 0, 1))
    private val listC = listOf(step("c", memory, 1, 2))
    private val night = SleepCfg(on = true, bedtime = "22:00", wake = "07:00", guarded = true, challenge = true,
        challengeSteps = listOf(step("g", sort, 0, 1)),
        tiers = listOf(ReminderTier("22:00", everySec = 900, challengeSteps = listA), ReminderTier("00:00", everySec = 300),
            ReminderTier("02:00", everySec = 60, challengeSteps = listC), ReminderTier("04:00", everySec = 30, challengeSteps = emptyList())))

    @Test fun the_bedtime_screen_takes_the_list_of_the_step_active_then() {
        val bed = at(28, 22, 0)
        assertEquals(listA, RemindersEngine.challengeAt(night, bed, at(28, 22, 5)))                    // 22:00 step: its own list
        assertEquals(night.generalChallenge, RemindersEngine.challengeAt(night, bed, at(29, 0, 30)))   // 00:00 step: none, the general one
        assertEquals(listC, RemindersEngine.challengeAt(night, bed, at(29, 2, 30)))                    // 02:00 step
        assertEquals(night.generalChallenge, RemindersEngine.challengeAt(night, bed, at(29, 4, 30)))   // an empty own list = the general one
        assertNull(RemindersEngine.challengeAt(night.copy(challenge = false), bed, at(28, 22, 5)))
        // before the first step, and with no steps at all: the general list
        val late = night.copy(tiers = listOf(ReminderTier("23:00", everySec = 900, challengeSteps = listA)))
        assertEquals(late.generalChallenge, RemindersEngine.challengeAt(late, bed, at(28, 22, 30)))
        assertEquals(listA, RemindersEngine.challengeAt(late, bed, at(28, 23, 1)))
        assertEquals(night.generalChallenge, RemindersEngine.challengeAt(night.copy(tiers = emptyList()), bed, at(29, 3, 0)))
        // a bedtime after midnight: a 23:00 step counts from bedtime, like the interval and the wait
        val after = night.copy(bedtime = "01:00", tiers = listOf(ReminderTier("23:00", everySec = 900, challengeSteps = listA), ReminderTier("03:00", everySec = 60, challengeSteps = listC)))
        assertEquals(listA, RemindersEngine.challengeAt(after, at(29, 1, 0), at(29, 1, 10)))
        assertEquals(listC, RemindersEngine.challengeAt(after, at(29, 1, 0), at(29, 3, 10)))
        // the list is read leniently and never empty: nothing readable = Math Easy x2
        assertEquals(Challenges.BEDTIME_DEFAULT, RemindersEngine.challengeAt(night.copy(challengeSteps = emptyList(), tiers = emptyList()), bed, at(28, 22, 5)))
        assertEquals(Challenges.BEDTIME_DEFAULT, SleepCfg(challenge = true).generalChallenge)
    }

    @Test fun the_bedtime_overlay_carries_the_list_and_the_extras() {
        fun prompt(s: SleepCfg, at: LocalDateTime) = RemindersEngine().tick(at, 999.0, s, noBreak, emptyList()).show.single { it.key == "sleep" }
        assertEquals(listA, prompt(night, at(28, 22, 5)).challenge)
        assertEquals(listC, prompt(night, at(29, 2, 30)).challenge)
        assertEquals(emptyList<ChallengeStep>(), prompt(night.copy(challenge = false), at(28, 22, 5)).challenge)   // off: Dismiss just dismisses
        assertEquals(PhraseOpts(grid = true, complex = true), prompt(night.copy(challengeGrid = true, challengeComplex = true), at(28, 22, 5)).challengePhrase)
        // the cover builds its run from the prompt: the list, the extras, its own clock
        val p = prompt(night.copy(tiers = emptyList(), challengeSteps = listOf(step("p", phrase, 30, 1)), challengeGrid = true), at(28, 22, 5))
        val r = ChallengeRun(p.challenge, p.challengePhrase, Random(1), 0L)
        assertEquals(6, r.view().gridWords.size)
    }

    @Test fun the_bedtime_defaults_and_old_files() {
        val s = SleepCfg()
        assertFalse(s.challenge); assertFalse(s.challengeGrid); assertFalse(s.challengeComplex)
        assertEquals(listOf(step("b0", math, 0, 2)), s.challengeSteps)
        val old = json.decodeFromString(SleepCfg.serializer(), """{"on":true,"guarded":true,"tiers":[{"from":"23:00","every":15}]}""")
        assertFalse(old.challenge); assertNull(old.tiers.single().challengeSteps); assertEquals(Challenges.BEDTIME_DEFAULT, old.generalChallenge)
        val back = json.decodeFromString(SleepCfg.serializer(), json.encodeToString(SleepCfg.serializer(), night))
        assertEquals(night, back)
        assertFalse(RemindersEngine.bedtimeLooser(old, old))
    }

    @Test fun the_bedtime_challenge_loosens_only_while_bedtime_is_important() {
        fun loose(new: SleepCfg, old: SleepCfg = night) = RemindersEngine.challengeLooser(old, new)
        assertFalse(loose(night))
        // adding, raising, reordering: free
        assertFalse(loose(night.copy(challengeSteps = night.challengeSteps + step("n", math, 0))))
        assertFalse(loose(night.copy(challengeSteps = listOf(step("g", sort, 2, 4)))))
        assertFalse(loose(night.copy(tiers = night.tiers.mapIndexed { i, t -> if (i == 0) t.copy(challengeSteps = listA + step("n", sort, 0)) else t })))
        // removing, lowering, another kind: the challenge
        assertTrue(loose(night.copy(challengeSteps = listOf(step("n", sort, 0)))))               // (a new id: the old row is gone)
        assertTrue(loose(night.copy(challengeSteps = listOf(step("g", math, 0, 1)))))                  // another kind, the same id
        assertTrue(loose(night.copy(tiers = night.tiers.mapIndexed { i, t -> if (i == 2) t.copy(challengeSteps = listOf(step("c", memory, 0, 2))) else t })))
        assertTrue(loose(night.copy(tiers = night.tiers.mapIndexed { i, t -> if (i == 2) t.copy(challengeSteps = listOf(step("c", memory, 1, 1))) else t })))
        assertTrue(loose(night.copy(tiers = night.tiers.mapIndexed { i, t -> if (i == 0) t.copy(challengeSteps = emptyList()) else t })))   // own list dropped: the general one (other ids)
        assertTrue(loose(night.copy(challenge = false)))
        // not Important: every edit is free (as its other settings)
        val free = night.copy(guarded = false)
        assertFalse(RemindersEngine.challengeLooser(free, free.copy(challenge = false)))
        assertFalse(RemindersEngine.challengeLooser(free, free.copy(challengeSteps = emptyList(), tiers = emptyList())))
        // turning it on is stricter; staying off is nothing
        assertFalse(RemindersEngine.challengeLooser(night.copy(challenge = false), night))
        assertFalse(RemindersEngine.challengeLooser(night.copy(challenge = false), night.copy(challenge = false, guarded = true, challengeSteps = emptyList())))
        // it rides on the same function the editor already calls
        assertTrue(RemindersEngine.bedtimeLooser(night, night.copy(challenge = false)))
        assertFalse(RemindersEngine.bedtimeLooser(night, night.copy(challengeSteps = night.challengeSteps + step("n", math, 0))))
    }

    @Test fun bedtime_loosens_at_any_minute_of_the_old_night() {
        // an own list moved to a later time: 22:00-23:00 now has the (lighter) general list
        val moved = night.copy(tiers = night.tiers.mapIndexed { i, t -> if (i == 0) t.copy(from = "23:00") else t })
        assertTrue(RemindersEngine.challengeLooser(night, moved))
        // the 22:00 step loses its own list: the general one has other ids, however hard it is
        val heavier = night.copy(challengeSteps = listOf(step("g", sort, 2, 3)), tiers = night.tiers.mapIndexed { i, t -> if (i == 0) t.copy(challengeSteps = null) else t })
        assertTrue(RemindersEngine.challengeLooser(night, heavier))
        // the same ids stronger in each place: free, even with times moved
        val stronger = night.copy(challengeSteps = listOf(step("g", sort, 1, 2)),
            tiers = listOf(ReminderTier("21:30", everySec = 900, challengeSteps = listOf(step("a", math, 1, 1))), night.tiers[1], night.tiers[2], night.tiers[3]))
        assertFalse(RemindersEngine.challengeLooser(night, stronger))
        // a new night that covers less shows nothing there: not a loosening
        assertFalse(RemindersEngine.challengeLooser(night, night.copy(wake = "05:00")))
        // a tier removed: its minutes fall to the general list (other ids) - the challenge
        assertTrue(RemindersEngine.challengeLooser(night, night.copy(tiers = night.tiers.filterIndexed { i, _ -> i != 2 })))
        // a general list shared by the ids of an own list: removing the own list is free if the general one is as hard
        val shared = night.copy(challengeSteps = listC, tiers = listOf(ReminderTier("22:00", everySec = 900, challengeSteps = listC)))
        assertFalse(RemindersEngine.challengeLooser(shared, shared.copy(tiers = listOf(ReminderTier("22:00", everySec = 900)))))
    }

    @Test fun the_bedtime_phrase_extras_loosen_only_with_a_phrase_step_in_an_old_list() {
        val withPhrase = night.copy(challengeGrid = true, challengeComplex = true,
            tiers = night.tiers.mapIndexed { i, t -> if (i == 2) t.copy(challengeSteps = listC + step("p", phrase, 30, 1)) else t })
        assertTrue(RemindersEngine.challengeLooser(withPhrase, withPhrase.copy(challengeGrid = false)))       // a phrase step in a tier list
        assertTrue(RemindersEngine.challengeLooser(withPhrase, withPhrase.copy(challengeComplex = false)))
        assertFalse(RemindersEngine.challengeLooser(withPhrase, withPhrase.copy(challengeGrid = true, challengeComplex = true)))
        val general = night.copy(challengeGrid = true, challengeSteps = listOf(step("p", phrase, 60, 1)), tiers = emptyList())
        assertTrue(RemindersEngine.challengeLooser(general, general.copy(challengeGrid = false)))              // ... or in the general one
        val none = night.copy(challengeGrid = true, challengeComplex = true)                                    // no phrase step anywhere
        assertFalse(RemindersEngine.challengeLooser(none, none.copy(challengeGrid = false, challengeComplex = false)))
        assertFalse(RemindersEngine.challengeLooser(general, general.copy(challengeGrid = true, challengeComplex = true)))   // turning one on
    }
}
