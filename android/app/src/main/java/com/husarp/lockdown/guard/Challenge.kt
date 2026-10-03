package com.husarp.lockdown.guard

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.AntiBypass
import kotlinx.coroutines.delay
import kotlin.random.Random

/**
 * Returns `guard(loosening, onPass)`: for a tightening change it runs at once; for a loosening one it runs the
 * Anti-Bypass challenge first (type a phrase, wait out a cool-off, or only inside allowed hours). Tightening is
 * never gated - coming back to your rules is never the thing to stand in the way of.
 */
@Composable
fun rememberGuard(): (loosening: Boolean, onPass: () -> Unit) -> Unit {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    val ab = cfg.antibypass
    var pending by remember { mutableStateOf<(() -> Unit)?>(null) }

    pending?.let { onPass ->
        ChallengeDialog(onPass = {
            // Passing opens a short unlock window (like the PC), so a run of loosening edits doesn't re-challenge each one.
            // In trusted time: setting the clock back can't stretch it.
            val now = TrustedTime.local(ctx)
            Store.update { it.copy(antibypass = it.antibypass.copy(unlockedFrom = now.toString(), unlockedUntil = now.plusMinutes(AntiBypass.UNLOCK_MIN).toString())) }
            pending = null; onPass()
        }, onCancel = { pending = null })
    }
    return { loosening, onPass ->
        // "free" is true when no challenge is set OR the unlock window is open — either way, run it now.
        if (!loosening || AntiBypass.status(ab, TrustedTime.local(ctx)) == "free") onPass() else pending = onPass
    }
}

@Composable
private fun ChallengeDialog(onPass: () -> Unit, onCancel: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val cs = MaterialTheme.colorScheme
    val ab = cfg.antibypass
    val now = TrustedTime.local(LocalContext.current)

    val phrase = remember { AntiBypass.phraseFor(ab) }
    val words = remember(phrase) { phrase.split(" ").filter { it.isNotBlank() } }
    var typed by remember { mutableStateOf("") }
    var gridDone by remember { mutableStateOf(false) }
    var gridStatus by remember { mutableStateOf("") }
    var gridError by remember { mutableStateOf(false) }
    var waitLeft by remember { mutableIntStateOf(ab.waitMin * 60) }

    val needPhrase = ab.phrase
    val useGrid = needPhrase && ab.grid
    val phraseOk = !needPhrase || (if (useGrid) gridDone else typed.trim() == phrase.trim())
    val onTrack = phrase.trim().startsWith(typed.trim())
    val waitOk = ab.waitMin == 0 || waitLeft <= 0
    val hoursOk = !ab.hours || AntiBypass.inHours(ab, now)
    val canPass = phraseOk && waitOk && hoursOk

    // The cool-off starts once the phrase is typed right (like the PC), not while it's being typed; it holds if
    // the phrase is changed back to wrong. When it runs out, the change goes through by itself.
    if (ab.waitMin > 0) {
        LaunchedEffect(phraseOk && hoursOk) { if (phraseOk && hoursOk) while (waitLeft > 0) { delay(1000); waitLeft-- } }
        LaunchedEffect(canPass) { if (canPass) onPass() }
    }

    AlertDialog(
        onDismissRequest = onCancel,
        title = { Text("A quick pause first") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                if (!hoursOk) {
                    Text("Loosening is only allowed inside your chosen hours. Try again then.",
                        style = MaterialTheme.typography.bodyMedium)
                } else {
                    Text("Tightening saves instantly. Loosening asks for this first — take your time.",
                        style = MaterialTheme.typography.bodyMedium)
                    if (needPhrase) {
                        Spacer(Modifier.height(12.dp))
                        if (useGrid) {
                            WordGrid(words, onDone = { gridDone = true }) { t, e -> gridStatus = t; gridError = e }
                            if (gridStatus.isNotBlank()) {
                                Spacer(Modifier.height(8.dp))
                                Text(gridStatus, style = MaterialTheme.typography.bodyMedium,
                                    color = if (gridError) cs.error else cs.onSurfaceVariant)
                            }
                        } else {
                            Surface(color = cs.surfaceContainerHigh, shape = MaterialTheme.shapes.medium) {
                                Text(phrase, Modifier.padding(12.dp), fontFamily = FontFamily.Monospace,
                                    style = MaterialTheme.typography.bodyLarge, color = cs.primary)
                            }
                            Spacer(Modifier.height(8.dp))
                            OutlinedTextField(value = typed, onValueChange = { typed = it },
                                label = { Text("Type the phrase") }, singleLine = false,
                                isError = typed.isNotBlank() && !onTrack)
                            if (typed.isNotBlank() && !phraseOk) {
                                Spacer(Modifier.height(6.dp))
                                Text(if (onTrack) "Keep going…" else "That doesn't match the phrase — check it.",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = if (onTrack) cs.onSurfaceVariant else cs.error)
                            }
                        }
                    }
                    if (ab.waitMin > 0 && waitLeft > 0) {
                        Spacer(Modifier.height(8.dp))
                        Text(if (phraseOk) "Cool-off: ${waitLeft}s — the change goes through when it ends."
                            else "Then a cool-off of ${ab.waitMin} min, starting once the phrase is right.",
                            style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onPass, enabled = canPass) { Text("Confirm change") } },
        dismissButton = { TextButton(onClick = onCancel) { Text("Keep my rule") } },
    )
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
    Spacer(Modifier.height(8.dp))
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
                            if (new.length - typed.length > 1) return@onValueChange   // ignore a pasted chunk
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
                        textStyle = MaterialTheme.typography.bodyLarge.copy(textAlign = TextAlign.Center),
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
    }
}
