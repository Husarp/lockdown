package com.husarp.lockdown.guard

import android.os.SystemClock
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.ChallengeRun
import com.husarp.lockdown.engine.ChallengeVerdict
import com.husarp.lockdown.engine.ChallengeView
import com.husarp.lockdown.engine.Challenges
import com.husarp.lockdown.ui.theme.LockdownTheme
import kotlin.random.Random

/**
 * One go at a challenge list on the screen: the progress line ("Step 1 of 3 - Math, Hard - 3 of 5"), a strip of all the
 * steps (done green, the current one marked), then the current kind's puzzle: math drawn as a picture (typed answer),
 * the memory grid (the lit squares stay until Ready, then hidden, tap them back), sorting (tap smallest to largest) or
 * the typed phrase (no paste, mistakes red, the 3x3 grid when the run asks for it). Every answer goes to [run], which
 * judges it and makes the next puzzle: a wrong one gives a new puzzle of the same step with a short note. It has no
 * ties to the Store, dialogs or navigation, so any host can show it (a dialog in the app, a ComposeView in the cover).
 * [onPassed] when the last one is right; [onCancel] from its Cancel (the host drops [run]). [title]: a heading above.
 */
@Composable
fun ChallengeRunPanel(run: ChallengeRun, onPassed: () -> Unit, onCancel: () -> Unit, modifier: Modifier = Modifier, title: String? = null) {
    val cs = MaterialTheme.colorScheme
    // epoch: bumped whenever the puzzle changes, so everything typed / tapped for the old one goes with it
    var epoch by remember { mutableIntStateOf(0) }
    var note by remember { mutableStateOf("") }
    var noteBad by remember { mutableStateOf(false) }
    val v = remember(epoch) { run.view() }
    // the "too fast" clock starts when the puzzle is drawn
    LaunchedEffect(epoch) { run.shown(SystemClock.elapsedRealtime()) }

    /** Hands a verdict to the screen: a new puzzle, a note, or the end. [wrongNote]: what a wrong answer says. */
    fun handle(verdict: ChallengeVerdict, wrongNote: String) {
        when (verdict) {
            ChallengeVerdict.PASSED -> onPassed()
            ChallengeVerdict.RIGHT -> { note = ""; epoch++ }
            ChallengeVerdict.WRONG -> {
                note = if (run.lastTooFast) "That was too quick - a new one." else wrongNote
                noteBad = true; epoch++
            }
            ChallengeVerdict.TOO_FAST -> { note = "Too quick - type it out again, by hand."; noteBad = true; epoch++ }
            ChallengeVerdict.MISMATCH -> { note = "That doesn't match - check it."; noteBad = true }
        }
    }
    val now = { SystemClock.elapsedRealtime() }

    Column(modifier, verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (title != null) Text(title, style = MaterialTheme.typography.titleMedium)
        Text(progressLine(v), style = MaterialTheme.typography.bodyMedium)
        StepStrip(v)
        // key: a new puzzle starts with fresh typed / tapped state
        androidx.compose.runtime.key(epoch) {
            when (v.kind) {
                Challenges.MATH -> MathPuzzleView(v, onAnswer = { handle(run.answer(it, now()), "Not right - here is a new one.") }, onCancel)
                Challenges.MEMORY -> MemoryPuzzleView(v, onTaps = { handle(run.answerTaps(it, now()), "Missed - a new pattern.") }, onCancel)
                Challenges.SORT -> SortPuzzleView(v, onTaps = { handle(run.answerTaps(it, now()), "Not in order - a new set.") }, onCancel)
                else -> PhrasePuzzleView(v, onAnswer = { handle(run.answer(it, now()), "") }, onCancel)
            }
        }
        if (note.isNotEmpty()) Text(note, style = MaterialTheme.typography.bodySmall, color = if (noteBad) cs.error else cs.onSurfaceVariant)
    }
}

/** "Step 1 of 3 - Math, Hard - 3 of 5" (bold: the step number and the round). */
private fun progressLine(v: ChallengeView): AnnotatedString = buildAnnotatedString {
    withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append("Step ${v.step + 1} of ${v.steps.size}") }
    append(" · ${v.label} · ")
    withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append("${(v.done + 1).coerceAtMost(v.times)} of ${v.times}") }
}

/** All the steps in a row of chips: done green, the current one marked, the rest plain. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun StepStrip(v: ChallengeView) {
    val cs = MaterialTheme.colorScheme
    val ex = LockdownTheme.extra
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        v.steps.forEachIndexed { i, s ->
            val finished = i < v.step
            val current = i == v.step
            val bg = when { finished -> ex.successContainer; current -> cs.primaryContainer; else -> cs.surface }
            val fg = when { finished -> ex.onSuccessContainer; current -> cs.onPrimaryContainer; else -> cs.onSurfaceVariant }
            Surface(color = bg, contentColor = fg, shape = RoundedCornerShape(8.dp),
                border = BorderStroke(1.dp, if (finished || current) bg else cs.outlineVariant)) {
                Text(Challenges.kindLabel(s.kind) + " ×${s.times}" + if (current) "  ${v.done}/${s.times}" else if (finished) "  ✓" else "",
                    Modifier.padding(horizontal = 10.dp, vertical = 4.dp), style = MaterialTheme.typography.labelMedium)
            }
        }
    }
}

/** Cancel on the left, the step's own button (if any) on the right. */
@Composable
private fun Footer(onCancel: () -> Unit, action: String? = null, enabled: Boolean = true, onAction: () -> Unit = {}) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.CenterVertically) {
        TextButton(onClick = onCancel) { Text("Cancel") }
        if (action != null) Button(onClick = onAction, enabled = enabled) { Text(action) }
    }
}

// ---------- math ----------

/** The sum drawn on a Canvas (a picture, not text you can select or copy) and a number field under it. */
@Composable
private fun ColumnScope.MathPuzzleView(v: ChallengeView, onAnswer: (String) -> Unit, onCancel: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    var typed by remember { mutableStateOf("") }
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
    val measurer = rememberTextMeasurer()
    val ink = cs.onSurface
    val style = TextStyle(fontSize = 34.sp, fontWeight = FontWeight.SemiBold, fontFamily = FontFamily.Serif, color = ink)
    Surface(color = cs.surfaceContainerHigh, shape = MaterialTheme.shapes.medium, modifier = Modifier.fillMaxWidth()) {
        Canvas(Modifier.fillMaxWidth().height(76.dp)) {
            var layout = measurer.measure(v.expr, style)
            if (layout.size.width > size.width * 0.94f) {                     // a long one shrinks to fit
                layout = measurer.measure(v.expr, style.copy(fontSize = style.fontSize * (size.width * 0.94f / layout.size.width)))
            }
            drawText(layout, topLeft = Offset((size.width - layout.size.width) / 2f, (size.height - layout.size.height) / 2f))
        }
    }
    fun submit() { if (typed.isNotBlank()) onAnswer(typed) }
    OutlinedTextField(
        value = typed,
        // digits only, typed one at a time: no pasting a chunk
        onValueChange = { new -> if (new.all { it in '0'..'9' } && new.length <= 12 && AntiBypass.typedNotPasted(typed, new)) typed = new },
        modifier = Modifier.fillMaxWidth().focusRequester(focus),
        label = { Text(if (v.findX) "x = ?" else "Answer") },
        singleLine = true,
        keyboardOptions = KeyboardOptions(autoCorrect = false, keyboardType = KeyboardType.Number, imeAction = ImeAction.Done),
        keyboardActions = KeyboardActions(onDone = { submit() }),
        supportingText = { Text("Type the answer and press Enter. Paste is off.") },
    )
    Footer(onCancel, "Next", typed.isNotBlank()) { submit() }
}

// ---------- memory grid ----------

/** The lit squares stay (with their order numbers on Hard) until Ready; then they hide and you tap them back. A tap
 *  that isn't one of them ends the round at once (the engine makes a new pattern). */
@Composable
private fun ColumnScope.MemoryPuzzleView(v: ChallengeView, onTaps: (List<Int>) -> Unit, onCancel: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    var hidden by remember { mutableStateOf(false) }       // false: watching, true: tapping back
    var taps by remember { mutableStateOf(listOf<Int>()) }
    val lit = v.lit
    Text(if (!hidden) "Remember the lit squares" + (if (v.ordered) " and their order" else "") + ", then press Ready."
        else "Tap the squares you saw" + (if (v.ordered) ", in order" else "") + " - ${taps.size} of ${lit.size}.",
        style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
    Column(Modifier.widthIn(max = 300.dp).align(Alignment.CenterHorizontally), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        for (r in 0 until v.size) Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            for (c in 0 until v.size) {
                val i = r * v.size + c
                val showLit = !hidden && i in lit
                val tapped = i in taps
                Box(Modifier.weight(1f).aspectRatio(1f)
                    .background(when { showLit || tapped -> cs.primary; else -> cs.surfaceContainerHigh }, RoundedCornerShape(8.dp))
                    .clickable(enabled = hidden && !tapped) {
                        val next = taps + i
                        val miss = if (v.ordered) i != lit[taps.size] else i !in lit
                        taps = next
                        if (miss || next.size == lit.size) onTaps(next)
                    },
                    contentAlignment = Alignment.Center) {
                    val label = when {
                        showLit && v.ordered -> "${lit.indexOf(i) + 1}"
                        tapped && v.ordered -> "${taps.indexOf(i) + 1}"
                        else -> null
                    }
                    if (label != null) Text(label, color = cs.onPrimary, style = MaterialTheme.typography.titleMedium)
                }
            }
        }
    }
    if (!hidden) Button(onClick = { hidden = true }, Modifier.align(Alignment.CenterHorizontally)) { Text("Ready") }
    Footer(onCancel)
}

// ---------- sorting ----------

/** Tap the items from smallest to largest into the answer row. Hard re-shuffles what is left after every tap (only the
 *  display: the engine judges the tapped indices). Continue sends them once all are in; Clear starts the row again. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ColumnScope.SortPuzzleView(v: ChallengeView, onTaps: (List<Int>) -> Unit, onCancel: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    var taps by remember { mutableStateOf(listOf<Int>()) }
    val hard = v.level >= 2
    val rest = remember(taps) {
        val left = v.items.indices.filter { it !in taps }
        if (hard) left.shuffled(Random(System.nanoTime())) else left
    }
    Text("Tap the numbers from smallest to largest.", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
    Surface(color = cs.surfaceContainerHigh, shape = MaterialTheme.shapes.medium, modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp)) {
        FlowRow(Modifier.padding(8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            if (taps.isEmpty()) Text("Your order shows here", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant,
                modifier = Modifier.padding(4.dp))
            for (i in taps) SortItem(v.items[i], picked = true) {}
        }
    }
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        for (i in rest) SortItem(v.items[i], picked = false) { taps = taps + i }
    }
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        TextButton(onClick = onCancel) { Text("Cancel") }
        Spacer(Modifier.weight(1f))
        TextButton(onClick = { taps = emptyList() }, enabled = taps.isNotEmpty()) { Text("Clear") }
        Button(onClick = { onTaps(taps) }, enabled = taps.size == v.items.size) { Text("Continue") }
    }
}

@Composable
private fun SortItem(text: String, picked: Boolean, onClick: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    Surface(color = if (picked) cs.primaryContainer else cs.surface, contentColor = if (picked) cs.onPrimaryContainer else cs.onSurface,
        shape = RoundedCornerShape(10.dp), border = BorderStroke(1.dp, if (picked) cs.primaryContainer else cs.outline),
        modifier = Modifier.clickable(enabled = !picked) { onClick() }) {
        Text(text, Modifier.padding(horizontal = 14.dp, vertical = 10.dp), style = MaterialTheme.typography.titleMedium)
    }
}

// ---------- typed phrase ----------

/** The phrase as it always was: the phrase in a box, typed (no pasting) into a field with mistakes in red, or word by
 *  word into the 3x3 grid. Next hands what is typed to the engine, which says if it matches. */
@Composable
private fun ColumnScope.PhrasePuzzleView(v: ChallengeView, onAnswer: (String) -> Unit, onCancel: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    if (v.gridWords.isNotEmpty()) {
        var gridDone by remember { mutableStateOf(false) }
        var status by remember { mutableStateOf("") }
        var error by remember { mutableStateOf(false) }
        WordGrid(v.gridWords, onDone = { gridDone = true }) { t, e -> status = t; error = e }
        if (status.isNotBlank()) Text(status, style = MaterialTheme.typography.bodyMedium, color = if (error) cs.error else cs.onSurfaceVariant)
        Footer(onCancel, "Next", gridDone) { onAnswer(v.gridWords.joinToString(" ")) }
        return
    }
    val phrase = v.phrase
    var typed by remember { mutableStateOf("") }
    val onTrack = phrase.trim().startsWith(typed.trim())
    // a long phrase (up to MAX_LENGTH) scrolls in its own box, so the typing field stays in view
    Surface(Modifier.heightIn(max = 220.dp), color = cs.surfaceContainerHigh, shape = MaterialTheme.shapes.medium) {
        Text(phrase, Modifier.verticalScroll(rememberScrollState()).padding(12.dp), fontFamily = FontFamily.Monospace,
            style = MaterialTheme.typography.bodyLarge, color = cs.primary)
    }
    // Typed, not pasted (PC block_paste): a chunk dropped in at once - the clipboard, or a known custom phrase from the
    // keyboard's clipboard history - is ignored. No suggestions either.
    OutlinedTextField(value = typed, onValueChange = { if (AntiBypass.typedNotPasted(typed, it)) typed = it },
        modifier = Modifier.fillMaxWidth(), label = { Text("Type the phrase") }, singleLine = false, maxLines = 6,
        keyboardOptions = KeyboardOptions(autoCorrect = false, keyboardType = KeyboardType.Password),
        isError = typed.isNotBlank() && !onTrack)
    if (typed.isNotBlank() && !onTrack) Text("That doesn't match the phrase — check it.", style = MaterialTheme.typography.bodySmall, color = cs.error)
    else if (typed.isNotBlank()) Text("Keep going…", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
    Footer(onCancel, "Next", typed.isNotBlank()) { onAnswer(typed) }
}

/**
 * "Table writing" (the PC's 3×3 grid): nine cells, one lit at random. Tap the lit cell and type the word shown;
 * a correct word lights a different cell with the next word. Nothing is focused for you and pasting a whole chunk
 * is ignored, so a macro can't type blindly — it would have to find and tap the right cell every time.
 */
@Composable
private fun WordGrid(words: List<String>, onDone: () -> Unit, onStatus: (String, Boolean) -> Unit) {
    val cs = MaterialTheme.colorScheme
    var index by remember { mutableIntStateOf(0) }
    var active by remember { mutableIntStateOf(Random.nextInt(9)) }
    var typed by remember { mutableStateOf("") }
    var error by remember { mutableStateOf(false) }

    if (index >= words.size) {
        Text("All words typed ✓", style = MaterialTheme.typography.titleSmall, color = cs.primary)
        return
    }

    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Text("Word ${index + 1} of ${words.size}", style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
        Surface(color = cs.surfaceContainerHigh, shape = MaterialTheme.shapes.small) {
            Text(words[index], Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodyLarge, color = cs.primary)
        }
    }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        for (r in 0..2) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                for (c in 0..2) {
                    val i = r * 3 + c
                    val lit = i == active
                    OutlinedTextField(
                        value = if (lit) typed else "",
                        onValueChange = onValueChange@{ new ->
                            if (!lit) return@onValueChange
                            if (!AntiBypass.typedNotPasted(typed, new)) return@onValueChange   // ignore a pasted chunk
                            typed = new
                            val word = words[index]
                            when {
                                new == word -> {
                                    val next = index + 1
                                    index = next
                                    typed = ""; error = false
                                    if (next >= words.size) { onStatus("All words typed.", false); onDone() }
                                    else {
                                        active = (active + 1 + Random.nextInt(8)) % 9   // a different cell
                                        onStatus("$next of ${words.size} done — tap the lit box.", false)
                                    }
                                }
                                word.startsWith(new) -> { error = false; onStatus("Tap the lit box and type the word.", false) }
                                else -> { error = true; onStatus("Typo — fix it to go on.", true) }
                            }
                        },
                        enabled = lit,
                        isError = lit && error,
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(autoCorrect = false, keyboardType = KeyboardType.Password),
                        textStyle = MaterialTheme.typography.bodyLarge.copy(textAlign = TextAlign.Center),
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
    }
}
