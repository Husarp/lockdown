package com.husarp.lockdown.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.AntiBypassCfg
import com.husarp.lockdown.engine.ChallengeStep
import com.husarp.lockdown.engine.Challenges
import com.husarp.lockdown.engine.PhraseOpts
import kotlin.random.Random

/** The step list's edits as plain functions (the editor below calls them; pure, so they are tested). */
object StepEdit {
    /** [steps] with step [i] moved [delta] places (-1 up, +1 down); stays put at the ends. */
    fun move(steps: List<ChallengeStep>, i: Int, delta: Int): List<ChallengeStep> {
        val j = i + delta
        if (i !in steps.indices || j !in steps.indices) return steps
        return steps.toMutableList().also { val t = it[i]; it[i] = it[j]; it[j] = t }
    }

    /** [steps] without step [i]; never below [min] steps (the challenge is on). */
    fun remove(steps: List<ChallengeStep>, i: Int, min: Int = 1): List<ChallengeStep> =
        if (i !in steps.indices || steps.size <= min) steps else steps.filterIndexed { k, _ -> k != i }

    /** [steps] plus a new step of [kind] (fresh id, easiest level, once); nothing past [Challenges.MAX_STEPS]. */
    fun add(steps: List<ChallengeStep>, kind: String, phraseLength: Int = Challenges.DEFAULT_LENGTH, rng: Random = Random.Default): List<ChallengeStep> =
        if (steps.size >= Challenges.MAX_STEPS || Challenges.levelRange(kind) == null) steps
        else steps + Challenges.newStep(kind, steps.map { it.id }, phraseLength, rng)

    /** Step [i] changed to [kind]: it keeps its id and times and takes the new kind's easiest level. */
    fun setKind(steps: List<ChallengeStep>, i: Int, kind: String, phraseLength: Int = Challenges.DEFAULT_LENGTH): List<ChallengeStep> =
        if (i !in steps.indices || Challenges.levelRange(kind) == null) steps
        else steps.mapIndexed { k, s -> if (k == i) Challenges.withKind(s, kind, phraseLength) else s }

    /** Step [i] at [level], clamped into its kind's range. */
    fun setLevel(steps: List<ChallengeStep>, i: Int, level: Int): List<ChallengeStep> =
        if (i !in steps.indices) steps
        else steps.mapIndexed { k, s -> if (k == i) s.copy(level = level.coerceIn(Challenges.levelRange(s.kind) ?: return steps)) else s }

    /** Step [i] asked [times] times, clamped to 1..[Challenges.MAX_TIMES]. */
    fun setTimes(steps: List<ChallengeStep>, i: Int, times: Int): List<ChallengeStep> =
        if (i !in steps.indices) steps
        else steps.mapIndexed { k, s -> if (k == i) s.copy(times = times.coerceIn(1, Challenges.MAX_TIMES)) else s }

    /**
     * [cfg] with the list [steps] (Anti-Bypass). The first phrase step's length is kept in `length` too, for readers
     * that know no list. A list that is just what an unlisted config already reads as stays unlisted (null), so
     * editing back to the start is no change at all. [saved]: the config as saved.
     */
    fun cfgWith(cfg: AntiBypassCfg, steps: List<ChallengeStep>, saved: AntiBypassCfg): AntiBypassCfg {
        val length = steps.firstOrNull { it.kind == Challenges.PHRASE }?.level ?: cfg.length
        val next = cfg.copy(steps = steps, length = length)
        return if (saved.steps == null && length == saved.length && steps == listOf(Challenges.migrated(length))) next.copy(steps = null) else next
    }
}

/**
 * The challenge's step list as rows (Guardrails' Anti-Bypass now; the bedtime lists in the Reminders screen too): each
 * step a kind, a level, how many times (1-20), up / down and remove; "+ Add a step" under it (up to
 * [Challenges.MAX_STEPS]; at least [minSteps]), then the "Takes about ..." line and its soft note above 10 minutes.
 * [kindsAllowed]: the kinds a row can be (and a new step offered). [phrase]: the phrase extras, for the time estimate
 * only. [phraseExtras]: shown under the list when any step is a phrase - the caller decides which extras exist (Anti-Bypass
 * has your own phrase, bedtime only capitals and the grid). [newPhraseLength]: a new phrase step's length.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun StepListEditor(
    steps: List<ChallengeStep>,
    onChange: (List<ChallengeStep>) -> Unit,
    modifier: Modifier = Modifier,
    kindsAllowed: List<String> = Challenges.KINDS,
    phrase: PhraseOpts = PhraseOpts(),
    newPhraseLength: Int = Challenges.DEFAULT_LENGTH,
    minSteps: Int = 1,
    phraseExtras: (@Composable () -> Unit)? = null,
) {
    val cs = MaterialTheme.colorScheme
    Column(modifier, verticalArrangement = Arrangement.spacedBy(10.dp)) {
        steps.forEachIndexed { i, s ->
            androidx.compose.runtime.key(s.id) {
                StepRow(i, s, steps.size, kindsAllowed, minSteps, newPhraseLength,
                    onMove = { d -> onChange(StepEdit.move(steps, i, d)) },
                    onRemove = { onChange(StepEdit.remove(steps, i, minSteps)) },
                    onKind = { onChange(StepEdit.setKind(steps, i, it, newPhraseLength)) },
                    onLevel = { onChange(StepEdit.setLevel(steps, i, it)) },
                    onTimes = { onChange(StepEdit.setTimes(steps, i, it)) })
            }
        }
        var menu by remember { mutableStateOf(false) }
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (steps.size < Challenges.MAX_STEPS) {
                TextButton(onClick = { menu = true }) { Text("+ Add a step") }
                DropdownMenu(menu, onDismissRequest = { menu = false }) {
                    for (k in kindsAllowed) DropdownMenuItem(text = { Text(Challenges.kindLabel(k)) },
                        onClick = { menu = false; onChange(StepEdit.add(steps, k, newPhraseLength)) })
                }
            } else Text("Up to ${Challenges.MAX_STEPS} steps.", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
        }
        if (phraseExtras != null && steps.any { it.kind == Challenges.PHRASE }) phraseExtras()
        val secs = Challenges.takesSeconds(steps, phrase)
        Text("How long: ${Challenges.takesText(secs)}", style = MaterialTheme.typography.titleSmall)
        Challenges.takesNote(secs).let { if (it.isNotEmpty()) Text(it, style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant) }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun StepRow(
    i: Int, s: ChallengeStep, count: Int, kinds: List<String>, minSteps: Int, newPhraseLength: Int,
    onMove: (Int) -> Unit, onRemove: () -> Unit, onKind: (String) -> Unit, onLevel: (Int) -> Unit, onTimes: (Int) -> Unit,
) {
    val cs = MaterialTheme.colorScheme
    androidx.compose.material3.Surface(color = cs.surfaceContainer, shape = androidx.compose.foundation.shape.RoundedCornerShape(14.dp)) {
        Column(Modifier.fillMaxWidth().padding(start = 12.dp, end = 4.dp, top = 4.dp, bottom = 10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("${i + 1}. ${Challenges.stepLabel(s)} ×${s.times}", style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                IconButton(onClick = { onMove(-1) }, enabled = i > 0) { Icon(Icons.Filled.KeyboardArrowUp, "move this step up") }
                IconButton(onClick = { onMove(1) }, enabled = i < count - 1) { Icon(Icons.Filled.KeyboardArrowDown, "move this step down") }
                IconButton(onClick = onRemove, enabled = count > minSteps) { Icon(Icons.Filled.Close, "remove this step") }
            }
            Column(Modifier.padding(end = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (kinds.size > 1) FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    for (k in kinds) Chip(Challenges.kindLabel(k), s.kind == k) { onKind(k) }
                }
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    val choices = Challenges.levelChoices(s.kind)
                    for (l in choices) Chip(if (s.kind == Challenges.PHRASE) "${Challenges.levelLabel(s.kind, l)} · $l" else Challenges.levelLabel(s.kind, l),
                        s.level == l) { onLevel(l) }
                    if (s.level !in choices) Chip(Challenges.levelLabel(s.kind, s.level), true) {}
                }
                if (s.kind == Challenges.PHRASE) {
                    var text by remember(s.level) { mutableStateOf(s.level.toString()) }
                    OutlinedTextField(text, { v ->
                            text = v.filter(Char::isDigit).take(4)
                            AntiBypass.parseLength(text)?.let(onLevel)
                        },
                        label = { Text("Or any length") }, singleLine = true, isError = AntiBypass.parseLength(text) == null,
                        supportingText = { Text("${AntiBypass.MIN_LENGTH} to ${AntiBypass.MAX_LENGTH} characters. Longer is free; shorter needs the challenge.") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.fillMaxWidth())
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Times", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                    IconButton(onClick = { onTimes(s.times - 1) }, enabled = s.times > 1) { Icon(Icons.Filled.Remove, "fewer times") }
                    Text("${s.times}", style = MaterialTheme.typography.titleMedium)
                    IconButton(onClick = { onTimes(s.times + 1) }, enabled = s.times < Challenges.MAX_TIMES) { Icon(Icons.Filled.Add, "more times") }
                }
            }
        }
    }
}
