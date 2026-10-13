package com.husarp.lockdown.engine

import kotlinx.serialization.Serializable
import java.math.BigInteger
import kotlin.math.abs
import kotlin.math.roundToInt
import kotlin.random.Random

/**
 * Challenges you can mix, the same as on the PC: a challenge is an ordered list of steps, each one kind + level + how
 * many times. Math, Memory grid, Sorting and Typed phrase. Anti-Bypass asks the list before anything loosens, the
 * bedtime screen before Dismiss. Every puzzle is made and judged HERE (never by the screen), a wrong answer gives a new
 * puzzle of that step, and a [ChallengeRun] holds the answers. No Android in this file: Compose (the dialog) and the
 * plain-View bedtime cover use it alike. Pure and testable.
 */

/** One step of a list. [level]: math 0..3 (Easy..Very hard), memory and sort 0..2, for a phrase its length in letters
 *  (30..2000). [times]: 1..20. [id] 1..24 of [A-Za-z0-9_-], unique in its list: a row keeps it while it is edited, so
 *  the stricter / looser check can follow it. Every field has a default so a half-written row still reads (and is then
 *  cleaned or dropped by [Challenges.clean]) instead of failing the whole config. */
@Serializable
data class ChallengeStep(val id: String = "", val kind: String = "", val level: Int = 0, val times: Int = 1)

/** The phrase extras, shared by every phrase step: the 3x3 grid, capitals + digits, and your own phrase ("" = a random one). */
data class PhraseOpts(val grid: Boolean = false, val complex: Boolean = false, val own: String = "")

/** What an answer did. [RIGHT]: the next puzzle (same or next step). [WRONG]: a new puzzle of this step, finished work
 *  stays. [MISMATCH] / [TOO_FAST]: a typed phrase only - the same phrase stays (fix and send again; too fast restarts
 *  its clock). [PASSED]: the last one is done. */
enum class ChallengeVerdict { RIGHT, WRONG, MISMATCH, TOO_FAST, PASSED }

/** A step as the strip on top of the screen shows it ("Math, Hard" x5). */
data class StepView(val kind: String, val level: Int, val times: Int, val label: String)

/** What the screen draws now. [step] (0-based) and [done] (in that step) place you in [steps]. Of the puzzle fields only
 *  the current kind's are filled: math [expr] (+ [findX]: ask "x = ?"), memory [size] x [size] and the [lit] squares
 *  (row-major, 0-based; with [ordered] in the order they count), sort the [items] as text (their index is their id),
 *  phrase [phrase] or, in the grid, [gridWords]. Draw the math as a picture, never as selectable text. */
data class ChallengeView(
    val steps: List<StepView>,
    val step: Int,
    val done: Int,
    val kind: String,
    val level: Int,
    val times: Int,
    val label: String,
    val passed: Boolean,
    val expr: String = "",
    val findX: Boolean = false,
    val size: Int = 0,
    val lit: List<Int> = emptyList(),
    val ordered: Boolean = false,
    val items: List<String> = emptyList(),
    val phrase: String = "",
    val gridWords: List<String> = emptyList(),
)

internal sealed interface Puzzle { val key: String }
internal class MathPuzzle(val expr: String, val answer: Long, val findX: Boolean = false) : Puzzle { override val key get() = expr }
internal class MemoryPuzzle(val size: Int, val lit: List<Int>, val ordered: Boolean) : Puzzle { override val key get() = "$size$lit" }
/** [order]: the item indices smallest to largest, the one right answer (all values differ). */
internal class SortPuzzle(val items: List<String>, val order: List<Int>) : Puzzle { override val key get() = items.joinToString("|") }
internal class PhrasePuzzle(val phrase: String, val words: List<String>?) : Puzzle { override val key get() = phrase }

object Challenges {
    const val MATH = "math"
    const val MEMORY = "memory"
    const val SORT = "sort"
    const val PHRASE = "phrase"
    val KINDS = listOf(MATH, MEMORY, SORT, PHRASE)

    const val MAX_STEPS = 6
    const val MAX_TIMES = 20
    const val DEFAULT_LENGTH = 60                    // a phrase step's length when none is given
    const val MIGRATED_ID = "s0"                     // the one phrase step an old Anti-Bypass config reads as
    /** Bedtime's list when "Challenge before Dismiss" is first turned on: Math Easy x2. */
    val BEDTIME_DEFAULT = listOf(ChallengeStep("b0", MATH, 0, 2))

    // too fast = not a human (it counts as a wrong answer); the typed phrase's 35 ms a character is the PC's too
    const val MS_PER_CHAR = 35L
    const val MATH_MIN_MS = 1200L
    const val SORT_MIN_MS_PER_ITEM = 150L
    const val MEMORY_MIN_MS_PER_SQUARE = 150L

    private val MINUS = "−"
    private val TIMES = "×"
    private val DIVIDE = "÷"
    private val ID = Regex("[A-Za-z0-9_-]{1,24}")
    private val LEVELS = mapOf(MATH to listOf("Easy", "Medium", "Hard", "Very hard"), MEMORY to listOf("Easy", "Medium", "Hard"),
        SORT to listOf("Easy", "Medium", "Hard"))
    /** The phrase length quick picks (as Guardrails offers): name to letters. */
    val PHRASE_PRESETS = listOf("Short" to 30, "Medium" to 60, "Long" to 120, "Very long" to 250)

    // ---------- names ----------

    fun kindLabel(kind: String) = when (kind) {
        MATH -> "Math"; MEMORY -> "Memory grid"; SORT -> "Sorting"; PHRASE -> "Typed phrase"; else -> kind
    }

    /** The levels a kind takes (a phrase: its length in letters), or null for an unknown kind. */
    fun levelRange(kind: String): IntRange? = when (kind) {
        MATH -> 0..3; MEMORY, SORT -> 0..2; PHRASE -> AntiBypass.MIN_LENGTH..AntiBypass.MAX_LENGTH; else -> null
    }

    /** The levels an editor offers as choices: math 0..3, memory / sort 0..2, a phrase the [PHRASE_PRESETS] (any length
     *  in between is typed). */
    fun levelChoices(kind: String): List<Int> =
        if (kind == PHRASE) PHRASE_PRESETS.map { it.second } else levelRange(kind)?.toList().orEmpty()

    /** "Easy" / "Very hard"; a phrase: the preset name when the length is one ("Medium"), else "73 letters". */
    fun levelLabel(kind: String, level: Int): String =
        if (kind == PHRASE) PHRASE_PRESETS.firstOrNull { it.second == level }?.first ?: "$level letters"
        else LEVELS[kind]?.getOrNull(level) ?: "$level"

    /** "Math, Hard". */
    fun stepLabel(s: ChallengeStep) = "${kindLabel(s.kind)}, ${levelLabel(s.kind, s.level)}"

    // ---------- the list ----------

    /** What an Anti-Bypass config with no list reads as: one phrase step of its length, once (nothing changes for anyone
     *  until they edit). */
    fun migrated(length: Int) = ChallengeStep(MIGRATED_ID, PHRASE, length.coerceIn(AntiBypass.MIN_LENGTH, AntiBypass.MAX_LENGTH), 1)

    /** A fresh row id (6 random lower-case letters / digits) that isn't in [taken]. */
    fun newId(taken: Collection<String>, rng: Random = Random.Default): String {
        val chars = ('a'..'z') + ('0'..'9')
        while (true) {
            val id = CharArray(6) { chars[rng.nextInt(chars.size)] }.concatToString()
            if (id !in taken) return id
        }
    }

    /** A new row of [kind] with a fresh id: its easiest level (a phrase: [phraseLength]), once. */
    fun newStep(kind: String, taken: Collection<String>, phraseLength: Int = DEFAULT_LENGTH, rng: Random = Random.Default) =
        ChallengeStep(newId(taken, rng), kind, if (kind == PHRASE) phraseLength.coerceIn(AntiBypass.MIN_LENGTH, AntiBypass.MAX_LENGTH) else 0, 1)

    /** [s] changed to another kind: it keeps its id and times, and takes the new kind's easiest level (a phrase: [phraseLength]). */
    fun withKind(s: ChallengeStep, kind: String, phraseLength: Int = DEFAULT_LENGTH) =
        if (kind == s.kind) s else s.copy(kind = kind, level = if (kind == PHRASE) phraseLength.coerceIn(AntiBypass.MIN_LENGTH, AntiBypass.MAX_LENGTH) else 0)

    /** Why [steps] can't be saved (a plain message), or null. Nothing is clamped silently: an unknown kind, a level or
     *  times out of range, a bad or repeated id, more than [MAX_STEPS] steps, or none (unless [allowEmpty]: the challenge is off). */
    fun validate(steps: List<ChallengeStep>, allowEmpty: Boolean = false): String? {
        if (steps.isEmpty()) return if (allowEmpty) null else "Add at least one step."
        if (steps.size > MAX_STEPS) return "A challenge has up to $MAX_STEPS steps."
        val seen = HashSet<String>()
        steps.forEachIndexed { i, s ->
            val n = i + 1
            val range = levelRange(s.kind) ?: return "Step $n: unknown kind \"${s.kind}\"."
            if (s.level !in range) return if (s.kind == PHRASE) "Step $n: the phrase length must be ${range.first} to ${range.last} letters."
                else "Step $n: pick one of the levels."
            if (s.times !in 1..MAX_TIMES) return "Step $n: how many times must be 1 to $MAX_TIMES."
            if (!ID.matches(s.id)) return "Step $n has no valid id."
            if (!seen.add(s.id)) return "Step $n repeats the id of an earlier step."
        }
        return null
    }

    /** A stored list read leniently: rows with an unknown kind are dropped, levels and times clamped into range, a bad
     *  or repeated id replaced (r0, r1 ... by place), at most [MAX_STEPS] kept. Can be empty. */
    fun clean(steps: List<ChallengeStep>?): List<ChallengeStep> {
        val used = HashSet<String>()
        val out = ArrayList<ChallengeStep>()
        for (s in steps.orEmpty()) {
            if (out.size >= MAX_STEPS) break
            val range = levelRange(s.kind) ?: continue
            var id = s.id
            if (!ID.matches(id) || id in used) { var n = out.size; while ("r$n" in used) n++; id = "r$n" }
            used.add(id)
            out.add(ChallengeStep(id, s.kind, s.level.coerceIn(range), s.times.coerceIn(1, MAX_TIMES)))
        }
        return out
    }

    /** [clean], and [fallback] when nothing readable is left (an empty list while the challenge is on). */
    fun read(steps: List<ChallengeStep>?, fallback: List<ChallengeStep>): List<ChallengeStep> = clean(steps).ifEmpty { fallback }

    /** Your own phrase's letters (without spaces), or null when there is none to use (a blank one is never asked). */
    fun ownLetters(own: String): Int? = if (own.isBlank()) null else own.replace(" ", "").length

    /**
     * Is [new] easier than [old]? Row by row, by id: an old row missing, with another kind, fewer times or a lower level
     * means yes (that loosens). New rows, higher levels, more times and a new order don't. A phrase row's strength is
     * your own phrase's letters while one is in use ([oldOwn] / [newOwn], as [ownLetters]), else its level.
     */
    fun stepsLooser(old: List<ChallengeStep>, new: List<ChallengeStep>, oldOwn: Int? = null, newOwn: Int? = null): Boolean {
        val byId = new.associateBy { it.id }
        fun strength(s: ChallengeStep, own: Int?) = if (s.kind == PHRASE) own ?: s.level else s.level
        return old.any { o ->
            val n = byId[o.id] ?: return@any true
            n.kind != o.kind || n.times < o.times || strength(n, newOwn) < strength(o, oldOwn)
        }
    }

    // ---------- how long ----------

    private val MATH_SEC = intArrayOf(6, 10, 20, 35)
    private val MEMORY_SEC = intArrayOf(8, 10, 15)
    private val SORT_SEC = intArrayOf(6, 10, 20)

    /** Seconds one go of [s] takes: math 6/10/20/35, memory 8/10/15, sort 6/10/20, a phrase 3 + 0.4 a letter (x 1.3 in the grid). */
    fun stepSeconds(s: ChallengeStep, phrase: PhraseOpts = PhraseOpts()): Double = when (s.kind) {
        MATH -> MATH_SEC[s.level.coerceIn(0, 3)].toDouble()
        MEMORY -> MEMORY_SEC[s.level.coerceIn(0, 2)].toDouble()
        SORT -> SORT_SEC[s.level.coerceIn(0, 2)].toDouble()
        PHRASE -> (3 + 0.4 * (ownLetters(phrase.own) ?: s.level)) * (if (phrase.grid) 1.3 else 1.0)
        else -> 0.0
    }

    /** The whole list in seconds: every step's go times its times. */
    fun takesSeconds(steps: List<ChallengeStep>, phrase: PhraseOpts = PhraseOpts()): Int =
        steps.sumOf { stepSeconds(it, phrase) * it.times }.roundToInt()

    /** "Takes about ...": "under 1 min", "about 3 min" (rounded to minutes). */
    fun takesText(seconds: Int): String = if (seconds < 60) "under 1 min" else "about ${(seconds + 30) / 60} min"

    /** The soft note above 10 minutes ("This will take about 14 min."), else "". */
    fun takesNote(seconds: Int): String = if (seconds > 600) "This will take ${takesText(seconds)}." else ""

    // ---------- puzzles ----------

    internal fun make(s: ChallengeStep, phrase: PhraseOpts, rng: Random, notThis: String? = null): Puzzle {
        var p = makeOnce(s, phrase, rng)
        var tries = 0
        while (p.key == notThis && tries++ < 20) p = makeOnce(s, phrase, rng)       // a new puzzle is never the same one again
        return p
    }

    private fun makeOnce(s: ChallengeStep, phrase: PhraseOpts, rng: Random): Puzzle = when (s.kind) {
        MATH -> newMath(s.level, rng)
        MEMORY -> newMemory(s.level, rng)
        SORT -> newSort(s.level, rng)
        else -> {
            val text = phrase.own.takeIf { it.isNotBlank() } ?: AntiBypass.newPhrase(s.level.coerceIn(AntiBypass.MIN_LENGTH, AntiBypass.MAX_LENGTH), phrase.complex, rng)
            PhrasePuzzle(if (phrase.grid) "" else text, if (phrase.grid) text.split(Regex("\\s+")).filter { it.isNotEmpty() } else null)
        }
    }

    /** Easy `a + b` / `a - b` (10..99, minus only with a >= b); Medium adds `a x b` (2..19 x 2..9) and `(b q) / b` (b, q in
     *  2..12); Hard `(a +- b) x c +- d` (a, b 10..99, a > b with minus, c 2..9, d 10..99, never negative); Very hard
     *  `a x b` (11..99) or find x in `a x +- b = c` (a 2..12, x 2..20, b 1..99, c > 0). */
    internal fun newMath(level: Int, rng: Random): MathPuzzle {
        fun sum(): MathPuzzle {
            val a = rng.nextInt(10, 100); val b = rng.nextInt(10, 100)
            return if (rng.nextBoolean()) MathPuzzle("$a + $b", (a + b).toLong())
            else MathPuzzle("${maxOf(a, b)} $MINUS ${minOf(a, b)}", abs(a - b).toLong())
        }
        when (level.coerceIn(0, 3)) {
            0 -> return sum()
            1 -> return when (rng.nextInt(4)) {
                0, 1 -> sum()
                2 -> { val a = rng.nextInt(2, 20); val b = rng.nextInt(2, 10); MathPuzzle("$a $TIMES $b", (a * b).toLong()) }
                else -> { val b = rng.nextInt(2, 13); val q = rng.nextInt(2, 13); MathPuzzle("${b * q} $DIVIDE $b", q.toLong()) }
            }
            2 -> while (true) {
                val plus = rng.nextBoolean()
                val b = if (plus) rng.nextInt(10, 100) else rng.nextInt(10, 99)
                val a = if (plus) rng.nextInt(10, 100) else rng.nextInt(b + 1, 100)
                val c = rng.nextInt(2, 10); val d = rng.nextInt(10, 100); val plus2 = rng.nextBoolean()
                val r = (if (plus) a + b else a - b) * c + (if (plus2) d else -d)
                if (r >= 0) return MathPuzzle("($a ${if (plus) "+" else MINUS} $b) $TIMES $c ${if (plus2) "+" else MINUS} $d", r.toLong())
            }
            else -> if (rng.nextBoolean()) {
                val a = rng.nextInt(11, 100); val b = rng.nextInt(11, 100)
                return MathPuzzle("$a $TIMES $b", (a * b).toLong())
            } else while (true) {
                val a = rng.nextInt(2, 13); val x = rng.nextInt(2, 21); val b = rng.nextInt(1, 100); val plus = rng.nextBoolean()
                val c = a * x + (if (plus) b else -b)
                if (c > 0) return MathPuzzle("${a}x ${if (plus) "+" else MINUS} $b = $c", x.toLong(), findX = true)
            }
        }
    }

    /** Easy 4x4 with 4 lit; Medium 5x5 with 6; Hard 5x5 with 8 IN ORDER ([MemoryPuzzle.lit] is that order). */
    internal fun newMemory(level: Int, rng: Random): MemoryPuzzle {
        val (size, count) = when (level.coerceIn(0, 2)) { 0 -> 4 to 4; 1 -> 5 to 6; else -> 5 to 8 }
        val cells = (0 until size * size).shuffled(rng).take(count)
        val ordered = level >= 2
        return MemoryPuzzle(size, if (ordered) cells else cells.sorted(), ordered)
    }

    private fun gcd(a: Int, b: Int) = BigInteger.valueOf(a.toLong()).gcd(BigInteger.valueOf(b.toLong())).toInt()

    /** A number in tenths as text: 5 -> "0.5", -45 -> "-4.5", 120 -> "12". */
    private fun tenths(t: Int): String {
        val a = abs(t)
        return (if (t < 0) "-" else "") + (if (a % 10 == 0) "${a / 10}" else "${a / 10}.${a % 10}")
    }

    /** Easy 6 whole numbers 1..99; Medium 8 from -20..99, 2..5 of them with one decimal place; Hard 10 mixed within -3..3:
     *  fractions (a/b in lowest terms, b in 2 4 5 8), one-place decimals, whole percents and negatives, at least one of each. Values are
     *  compared exactly (whole units of 1/200) and never equal. */
    internal fun newSort(level: Int, rng: Random): SortPuzzle {
        val units = ArrayList<Int>(); val shown = ArrayList<String>()
        fun add(u: Int, text: String) { if (u !in units) { units.add(u); shown.add(text) } }
        when (level.coerceIn(0, 2)) {
            0 -> while (units.size < 6) rng.nextInt(1, 100).let { add(it * 200, "$it") }
            1 -> {
                val decimals = rng.nextInt(2, 6)
                while (units.size < decimals) rng.nextInt(-200, 991).let { if (it % 10 != 0) add(it * 20, tenths(it)) }
                while (units.size < 8) rng.nextInt(-20, 100).let { add(it * 200, "$it") }
            }
            else -> {
                val forms = listOf(0, 1, 2) + List(7) { rng.nextInt(3) }              // 0 fraction, 1 decimal, 2 percent
                for ((i, form) in forms.withIndex()) {
                    val before = units.size
                    while (units.size == before) {
                        val sign = if (i == 0 || rng.nextBoolean()) -1 else 1       // (the first is always negative)
                        val s = if (sign < 0) "-" else ""
                        when (form) {
                            0 -> { val b = listOf(2, 4, 5, 8)[rng.nextInt(4)]; val a = rng.nextInt(1, 3 * b)
                                if (gcd(a, b) == 1) add(sign * a * (200 / b), "$s$a/$b") }
                            1 -> { val k = rng.nextInt(1, 30); if (k % 10 != 0) add(sign * k * 20, tenths(sign * k)) }
                            else -> { val p = rng.nextInt(1, 301); add(sign * p * 2, "$s$p%") }
                        }
                    }
                }
            }
        }
        var perm = units.indices.shuffled(rng)                                          // the order they are shown in
        while (perm.map { units[it] }.let { v -> v == v.sorted() }) perm = units.indices.shuffled(rng)   // (never already in order)
        val values = perm.map { units[it] }
        return SortPuzzle(perm.map { shown[it] }, values.indices.sortedBy { values[it] })
    }

    // ---------- judging ----------

    /** A typed math answer: one whole number (digits only after trimming spaces) that is the answer. */
    internal fun mathOk(p: MathPuzzle, typed: String): Boolean {
        val t = typed.trim()
        return t.length in 1..12 && t.all { it in '0'..'9' } && t.toLong() == p.answer
    }

    /** Memory: exactly the lit squares, each once (in that order when [MemoryPuzzle.ordered]). */
    internal fun memoryOk(p: MemoryPuzzle, taps: List<Int>): Boolean =
        taps.size == p.lit.size && if (p.ordered) taps == p.lit else taps.toSet() == p.lit.toSet()

    /** Sorting: every item once, smallest to largest. */
    internal fun sortOk(p: SortPuzzle, taps: List<Int>): Boolean = taps == p.order
}

/**
 * One go at a challenge list: the screen asks [view] for what to draw and sends what the user did to [answer] (typed
 * text: math, phrase - in the grid the words joined by spaces) or [answerTaps] (the tapped squares / item indices:
 * memory, sorting), each with the screen's clock in ms. The answer and the solution stay in here. A wrong answer makes
 * a new puzzle of the same step and keeps what is finished; Cancel (or closing) is just dropping this object - the next
 * run starts at step 1 with new puzzles. [nowMs]: when the first puzzle is shown; [shown] restarts the "too fast" clock
 * if it is drawn later. [steps] is read leniently ([Challenges.clean]); nothing readable asks one 60-letter phrase,
 * never less.
 */
class ChallengeRun(steps: List<ChallengeStep>, private val phrase: PhraseOpts = PhraseOpts(),
                   private val rng: Random = Random.Default, nowMs: Long = 0L) {
    val steps: List<ChallengeStep> = Challenges.clean(steps).ifEmpty { listOf(Challenges.migrated(Challenges.DEFAULT_LENGTH)) }
    var step = 0; private set
    var done = 0; private set
    var passed = false; private set
    /** The last answer came too fast (a typed phrase: it is [ChallengeVerdict.TOO_FAST]; the other kinds count it wrong). */
    var lastTooFast = false; private set
    private var shownAt = nowMs
    private var puzzle: Puzzle = Challenges.make(this.steps[0], phrase, rng)

    fun view(): ChallengeView {
        val s = steps[step]
        val v = ChallengeView(steps.map { StepView(it.kind, it.level, it.times, Challenges.stepLabel(it)) }, step, done,
            s.kind, s.level, s.times, Challenges.stepLabel(s), passed)
        return when (val p = puzzle) {
            is MathPuzzle -> v.copy(expr = p.expr, findX = p.findX)
            is MemoryPuzzle -> v.copy(size = p.size, lit = p.lit, ordered = p.ordered)
            is SortPuzzle -> v.copy(items = p.items)
            is PhrasePuzzle -> v.copy(phrase = p.phrase, gridWords = p.words.orEmpty())
        }
    }

    /** The puzzle is on screen from [nowMs]: the "too fast" clock starts over. */
    fun shown(nowMs: Long) { shownAt = nowMs }

    /** Typed text: the math answer, the phrase, or (grid) its words joined by spaces. Anything else for this puzzle is wrong. */
    fun answer(value: String, nowMs: Long): ChallengeVerdict {
        if (passed) return ChallengeVerdict.PASSED
        return when (val p = puzzle) {
            is MathPuzzle -> judge(Challenges.mathOk(p, value), Challenges.MATH_MIN_MS, nowMs)
            is PhrasePuzzle -> {
                val words = value.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }
                val right = if (p.words != null) words == p.words else value.trim() == p.phrase.trim()
                val chars = if (p.words != null) p.words.sumOf { it.length } else p.phrase.length
                judge(right, chars * Challenges.MS_PER_CHAR, nowMs, typedPhrase = true)
            }
            else -> judge(false, 0, nowMs)
        }
    }

    /** Taps: the squares in the order tapped (row-major, 0-based) for memory, the item indices in tap order for sorting. */
    fun answerTaps(taps: List<Int>, nowMs: Long): ChallengeVerdict {
        if (passed) return ChallengeVerdict.PASSED
        return when (val p = puzzle) {
            is MemoryPuzzle -> judge(Challenges.memoryOk(p, taps), p.lit.size * Challenges.MEMORY_MIN_MS_PER_SQUARE, nowMs)
            is SortPuzzle -> judge(Challenges.sortOk(p, taps), p.items.size * Challenges.SORT_MIN_MS_PER_ITEM, nowMs)
            else -> judge(false, 0, nowMs)
        }
    }

    private fun judge(right: Boolean, minMs: Long, nowMs: Long, typedPhrase: Boolean = false): ChallengeVerdict {
        lastTooFast = false
        val fast = nowMs - shownAt < minMs
        if (typedPhrase) {                                          // the same phrase stays: fix it, or wait out the clock
            if (!right) return ChallengeVerdict.MISMATCH
            if (fast) { shownAt = nowMs; lastTooFast = true; return ChallengeVerdict.TOO_FAST }
        } else if (!right || fast) {                                // too fast counts as wrong: a new puzzle of this step
            lastTooFast = fast
            puzzle = Challenges.make(steps[step], phrase, rng, puzzle.key); shownAt = nowMs
            return ChallengeVerdict.WRONG
        }
        done++
        if (done >= steps[step].times) {
            if (step + 1 >= steps.size) { passed = true; return ChallengeVerdict.PASSED }
            step++; done = 0
        }
        puzzle = Challenges.make(steps[step], phrase, rng, puzzle.key); shownAt = nowMs
        return ChallengeVerdict.RIGHT
    }
}
