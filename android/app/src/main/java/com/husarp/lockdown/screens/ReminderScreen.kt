package com.husarp.lockdown.screens

import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Bedtime
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Coffee
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.key
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.CustomCfg
import com.husarp.lockdown.engine.ReminderTier
import com.husarp.lockdown.engine.RemindersEngine
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.SleepCfg
import com.husarp.lockdown.guard.rememberGuard
import com.husarp.lockdown.remind.Grayscale
import com.husarp.lockdown.remind.ReminderRunner
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.EditorHeader
import com.husarp.lockdown.ui.Numeral
import com.husarp.lockdown.ui.SectionLabel
import com.husarp.lockdown.ui.Segmented
import com.husarp.lockdown.ui.StatusChip
import com.husarp.lockdown.ui.Status
import com.husarp.lockdown.ui.SwitchRowInline
import java.util.UUID

private sealed interface RemPage {
    data object List : RemPage
    data object Bedtime : RemPage
    data object Breaks : RemPage
    data class Custom(val c: CustomCfg) : RemPage
}

@Composable
fun ReminderScreen() {
    var page by remember { mutableStateOf<RemPage>(RemPage.List) }
    when (val p = page) {
        RemPage.List -> RemList(open = { page = it })
        RemPage.Bedtime -> BedtimeEditor { page = RemPage.List }
        RemPage.Breaks -> BreakEditor { page = RemPage.List }
        is RemPage.Custom -> CustomEditor(p.c) { page = RemPage.List }
    }
}

@Composable
private fun RemList(open: (RemPage) -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    val guard = rememberGuard()
    val cs = MaterialTheme.colorScheme

    Scaffold(
        containerColor = cs.surface,
        floatingActionButton = {
            FloatingActionButton(onClick = { open(RemPage.Custom(CustomCfg(UUID.randomUUID().toString().take(8), text = ""))) },
                containerColor = cs.primaryContainer, contentColor = cs.onPrimaryContainer) { Icon(Icons.Filled.Add, "Add") }
        },
    ) { pad ->
        Column(Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            // Bedtime hero
            Card(color = cs.surfaceContainer, shape = MaterialTheme.shapes.extraLarge, onClick = { open(RemPage.Bedtime) }) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.Bedtime, null, tint = cs.primary)
                    Spacer(Modifier.size(10.dp))
                    Text("Bedtime", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                    Switch(checked = cfg.sleep.on, onCheckedChange = { on ->
                        if (on) { Store.update { it.copy(sleep = it.sleep.copy(on = true)) }; Grayscale.sync(ctx) }
                        else guard(cfg.sleep.guarded) {
                            Store.update { it.copy(sleep = it.sleep.copy(on = false)) }
                            // Bedtime off by hand: colour back now, whoever turned grayscale on (only while bedtime grayscale is on)
                            if (Store.config.settings.bedtimeGrayscale) Grayscale.switchedOff(ctx) else Grayscale.sync(ctx)
                        }
                    })
                }
                Spacer(Modifier.height(10.dp))
                Numeral("${cfg.sleep.bedtime} → ${cfg.sleep.wake}", sizeSp = 36)
                Spacer(Modifier.height(8.dp))
                Text((if (cfg.sleep.before > 0) "Heads-up ${cfg.sleep.before} min before · " else "") +
                    "full-screen at bedtime · comes back ${stepsText(cfg.sleep)}",
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
            // Breaks
            Card(color = cs.surfaceContainerLow, onClick = { open(RemPage.Breaks) }) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.Coffee, null, tint = cs.onSurfaceVariant)
                    Spacer(Modifier.size(12.dp))
                    Column(Modifier.weight(1f)) {
                        Text("Breaks", style = MaterialTheme.typography.titleSmall)
                        Text("${cfg.breaks.length} min every ${cfg.breaks.every} min" + if (cfg.breaks.twenty) " · 20-20-20 on" else "",
                            style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                    }
                    Switch(checked = cfg.breaks.on, onCheckedChange = { on ->
                        if (on) Store.update { it.copy(breaks = it.breaks.copy(on = true)) }
                        else guard(cfg.breaks.guarded) { Store.update { it.copy(breaks = it.breaks.copy(on = false)) } }
                    })
                }
            }
            SectionLabel("Your reminders")
            cfg.customs.forEach { c ->
                Card(color = cs.surfaceContainerLow, onClick = { open(RemPage.Custom(c)) }) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text(c.text.ifBlank { "(no text)" }, style = MaterialTheme.typography.titleSmall)
                                if (c.guarded) StatusChip("Important", Status.BLOCKED)
                            }
                            Text(scheduleText(c), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                        }
                        Switch(checked = c.on, onCheckedChange = { on ->
                            val loosen = RemindersEngine.loosensReminder(c.on, c.guarded, on, c.guarded)
                            guard(loosen) { updateCustom(c.id) { it.copy(on = on) } }
                        })
                    }
                }
            }
            Text("Quiet pacing: a reminder backs off after a few ignored nudges and stops at its daily cap.",
                style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            Spacer(Modifier.height(72.dp))
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun BedtimeEditor(onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val modes by ModesStore.modes.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    val guard = rememberGuard()
    val cs = MaterialTheme.colorScheme
    val s = cfg.sleep
    // Steps, heads-up, texts and the mode are ordinary edits, as on the PC; only turning an important alert off
    // (or un-flagging it) asks for the challenge.
    fun set(block: (SleepCfg) -> SleepCfg) = Store.update { it.copy(sleep = block(it.sleep)) }
    // The step that sat at bedtime when the Bedtime box was entered: only it moves with the bedtime, so a time
    // passing through on the way (deleting the "2" of 23:00 gives 3:00) never drags other steps along.
    var anchor by remember { mutableStateOf(-1) }
    Column(Modifier.fillMaxSize()) {
        EditorHeader("Bedtime", onBack = onClose)
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Card(color = cs.surfaceContainerLow) {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    // a step at bedtime moves with it
                    TimeField("Bedtime", s.bedtime, Modifier.weight(1f).onFocusChanged { f ->
                        if (f.isFocused) anchor = Store.config.sleep.let { c -> c.tiers.indexOfFirst { it.from == c.bedtime } }
                    }) { t ->
                        set { c -> c.copy(bedtime = t, tiers = c.tiers.mapIndexed { j, x -> if (j == anchor) x.copy(from = t) else x }) }
                    }
                    TimeField("Wake", s.wake, Modifier.weight(1f)) { t -> set { it.copy(wake = t) } }
                }
                Spacer(Modifier.height(10.dp))
                Text(if (s.before > 0) "Heads-up ${s.before} min before" else "Heads-up: off", style = MaterialTheme.typography.bodyMedium)
                Row {
                    TextButton(onClick = { set { it.copy(before = (it.before - 5).coerceAtLeast(0)) } }) { Text("−5") }
                    TextButton(onClick = { set { it.copy(before = (it.before + 5).coerceAtMost(12 * 60)) } }) { Text("+5") }
                }
            }
            Card(color = cs.surfaceContainerLow) {
                Text("Comes back after you dismiss it", style = MaterialTheme.typography.titleSmall)
                s.tiers.forEachIndexed { i, t ->
                    key(i) {
                        TierRow(s, t, onChange = { n -> set { c -> c.copy(tiers = c.tiers.mapIndexed { j, x -> if (j == i) n else x }) } },
                            onRemove = {
                                anchor = if (anchor == i) -1 else if (anchor > i) anchor - 1 else anchor
                                set { c -> c.copy(tiers = c.tiers.filterIndexed { j, _ -> j != i }) }
                            })
                    }
                }
                if (s.tiers.isEmpty()) Text("Every ${s.repeat} min, all night.", style = MaterialTheme.typography.bodyMedium)
                TextButton(onClick = { set { c -> c.copy(tiers = c.tiers + newStep(c)) } }) { Text("+ Add step") }
                Text("The later it is, the more often the bedtime screen returns after you dismiss it. " +
                    "A step earlier in the evening than bedtime counts from bedtime.",
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
            Card(color = cs.surfaceContainerLow) {
                Text("Turn on a mode at bedtime, until wake-up", style = MaterialTheme.typography.titleSmall)
                Spacer(Modifier.height(8.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Chip("No mode", s.mode.isEmpty()) { set { it.copy(mode = "") } }
                    modes.forEach { m -> Chip(m.name, s.mode == m.id) { set { it.copy(mode = m.id) } } }
                }
            }
            Card(color = cs.surfaceContainerLow) {
                OutlinedTextField(s.warnText, { v -> set { it.copy(warnText = v) } }, label = { Text("Heads-up says") },
                    placeholder = { Text(RemindersEngine.SLEEP_WARN_TEXT) }, modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(s.text, { v -> set { it.copy(text = v) } }, label = { Text("Bedtime says") },
                    placeholder = { Text(RemindersEngine.SLEEP_TEXT) }, modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(6.dp))
                Text("Your own words, or leave empty for these. {bedtime}, {wake} and {time} are filled in.",
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
            Column {
                SwitchRowInline("Grayscale the screen at bedtime", cfg.settings.bedtimeGrayscale) { on ->
                    Store.update { it.copy(settings = it.settings.copy(bedtimeGrayscale = on)) }
                    if (on) Grayscale.sync(ctx) else Grayscale.switchedOff(ctx)
                }
                GrayscaleNote(cfg.settings.bedtimeGrayscale)
            }
            SwitchRowInline("Important (need the challenge to turn off)", s.guarded) { on ->
                guard(RemindersEngine.loosensReminder(s.on, s.guarded, s.on, on)) { set { it.copy(guarded = on) } }
            }
            Text("At bedtime the screen goes dark with \"Time for bed\": Dismiss, Disable alerts (asks for the challenge), " +
                "or an emergency unlock while you have one.", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
        }
    }
}

/** One escalation step: "from HH:MM, every N min", with × to remove it. */
@Composable
private fun TierRow(s: SleepCfg, t: ReminderTier, onChange: (ReminderTier) -> Unit, onRemove: () -> Unit) {
    var time by remember(t.from) { mutableStateOf(t.from) }
    var every by remember(t.every) { mutableStateOf(t.every.toString()) }
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(time, { v -> time = v; validTime(v)?.let { onChange(t.copy(from = it)) } },
            label = { Text("From") }, singleLine = true, isError = validTime(time) == null, modifier = Modifier.weight(1f))
        OutlinedTextField(every, { v -> every = v; v.trim().toIntOrNull()?.takeIf { it >= 1 }?.let { onChange(t.copy(every = minOf(it, 12 * 60))) } },
            label = { Text("Every (min)") }, singleLine = true, isError = (every.trim().toIntOrNull() ?: 0) < 1,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.weight(1f))
        IconButton(onClick = onRemove) { Icon(Icons.Filled.Close, "Remove step") }
    }
    if (RemindersEngine.stepFromBedtime(s, t.from))
        Text("Before bedtime: counts from ${s.bedtime}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

/** A new step: at bedtime if there is none, else an hour after the last one, as often. */
private fun newStep(s: SleepCfg): ReminderTier {
    val last = s.tiers.lastOrNull() ?: return ReminderTier(s.bedtime, 15)
    val t = runCatching { Rules.parseHhmm(last.from).plusHours(1) }.getOrNull() ?: return ReminderTier(s.bedtime, 15)
    return ReminderTier("%02d:%02d".format(t.hour, t.minute), last.every)
}

/** "9:30" / "09:30" as "09:30", or null while it isn't a time yet. */
internal fun validTime(v: String): String? {
    val m = Regex("""(\d{1,2}):(\d{2})""").matchEntire(v.trim()) ?: return null
    val h = m.groupValues[1].toInt(); val min = m.groupValues[2].toInt()
    return if (h < 24 && min < 60) "%02d:%02d".format(h, min) else null
}

/** A time box that saves only a real time (half-typed "2" or "22:" never reaches the settings). */
@Composable
internal fun TimeField(label: String, value: String, modifier: Modifier, onValid: (String) -> Unit) {
    var text by remember(value) { mutableStateOf(value) }
    OutlinedTextField(text, { v -> text = v; validTime(v)?.let { if (it != value) onValid(it) } }, label = { Text(label) },
        singleLine = true, isError = validTime(text) == null, modifier = modifier)
}

/** The bedtime screen's "Disable alerts": the challenge, then off for tonight or a 15-min snooze. Cancelled, or
 *  left too long (ReminderRunner), it counts as Dismiss: the bedtime screen comes back on its escalation. */
@Composable
fun BedtimeAsk() {
    val asking by ReminderRunner.asking.collectAsStateWithLifecycle()
    if (!asking) return
    val ctx = LocalContext.current
    var choose by remember { mutableStateOf(false) }
    val guard = rememberGuard(onCancel = { ReminderRunner.askAnswered(ctx, "dismiss") })
    LaunchedEffect(Unit) { guard(true) { choose = true } }
    if (choose) AlertDialog(
        onDismissRequest = { ReminderRunner.askAnswered(ctx, "dismiss") },
        title = { Text("Bedtime alerts off") },
        text = { Text("Off for the rest of tonight, or snooze a while?") },
        confirmButton = { TextButton(onClick = { ReminderRunner.askAnswered(ctx, "off_tonight") }) { Text("Off tonight") } },
        dismissButton = {
            Row {
                TextButton(onClick = { ReminderRunner.askAnswered(ctx, "dismiss") }) { Text("Cancel") }
                TextButton(onClick = { ReminderRunner.askAnswered(ctx, "snooze:15") }) { Text("Snooze 15 min") }
            }
        },
    )
}

@Composable
private fun BreakEditor(onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val guard = rememberGuard()
    val cs = MaterialTheme.colorScheme
    val b = cfg.breaks
    fun set(block: (com.husarp.lockdown.engine.BreakCfg) -> com.husarp.lockdown.engine.BreakCfg) = Store.update { it.copy(breaks = block(it.breaks)) }
    Column(Modifier.fillMaxSize()) {
        EditorHeader("Breaks", onBack = onClose)
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            StepperRow("Remind every", b.every, "min of use", 5, min = 5) { set { c -> c.copy(every = it) } }
            StepperRow("Break length", b.length, "min", 1, min = 1, max = 60) { set { c -> c.copy(length = it) } }
            StepperRow("Snooze", b.snooze, "min each time", 5, min = 5) { set { c -> c.copy(snooze = it) } }
            Card(color = cs.surfaceContainerLow) {
                SwitchRowInline("Strict break (covers the screen until it's over)", b.strict) { set { c -> c.copy(strict = it) } }
                SwitchRowInline("20-20-20 eye breaks", b.twenty) { set { c -> c.copy(twenty = it) } }
                SwitchRowInline("Important (need the challenge to turn off)", b.guarded) { on ->
                    guard(RemindersEngine.loosensReminder(b.on, b.guarded, b.on, on)) { set { it.copy(guarded = on) } }
                }
            }
            if (b.strict) StepperRow("Snoozes first", b.maxSnooze, "then it starts on its own", 1, min = 1) { set { c -> c.copy(maxSnooze = it) } }
            Card(color = cs.surfaceContainerLow) {
                OutlinedTextField(b.text, { v -> set { it.copy(text = v) } }, label = { Text("It says") },
                    placeholder = { Text(RemindersEngine.BREAK_TEXT) }, modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(6.dp))
                Text("Your own words, or leave empty for this one. {every} and {length} are filled in.",
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                if (b.twenty) {
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(b.twentyText, { v -> set { it.copy(twentyText = v) } }, label = { Text("20-20-20 says") },
                        placeholder = { Text(RemindersEngine.TWENTY_TEXT) }, modifier = Modifier.fillMaxWidth())
                }
            }
        }
    }
}

@Composable
private fun CustomEditor(c: CustomCfg, onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val guard = rememberGuard()
    val cs = MaterialTheme.colorScheme
    val ctx = LocalContext.current
    var draft by remember { mutableStateOf(cfg.customs.firstOrNull { it.id == c.id } ?: c) }
    var timesText by remember { mutableStateOf(draft.times.joinToString(", ")) }
    val kindIdx = when (draft.kind) { "times" -> 1; "random" -> 2; else -> 0 }
    val timesOk = draft.times.isNotEmpty() && draft.times.all { validTime(it) != null }
    val windowOk = draft.window.size == 2 && draft.window.all { validTime(it) != null }

    Column(Modifier.fillMaxSize()) {
        EditorHeader(draft.text.ifBlank { "New reminder" }, onBack = onClose, action = "Save") {
            // only real times are saved (a half-typed "9" used to be, and the reminder never came)
            if ((draft.kind == "times" && !timesOk) || ((draft.kind == "random" || draft.hours) && !windowOk)) {
                Toast.makeText(ctx, "Write the times as HH:MM, e.g. 09:30.", Toast.LENGTH_LONG).show()
                return@EditorHeader
            }
            draft = draft.copy(times = draft.times.map { validTime(it) ?: it }, window = draft.window.map { validTime(it) ?: it })
            val old = cfg.customs.firstOrNull { it.id == draft.id }
            val loosen = old != null && RemindersEngine.loosensReminder(old.on, old.guarded, draft.on, draft.guarded)
            guard(loosen) {
                Store.update { s -> if (s.customs.any { it.id == draft.id }) s.copy(customs = s.customs.map { if (it.id == draft.id) draft else it }) else s.copy(customs = s.customs + draft) }
                onClose()
            }
        }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedTextField(draft.text, { draft = draft.copy(text = it) }, label = { Text("What should it say?") }, modifier = Modifier.fillMaxWidth())
            SectionLabel("When")
            Segmented(listOf("Every…", "Set times", "Random once"), kindIdx) { draft = draft.copy(kind = when (it) { 1 -> "times"; 2 -> "random"; else -> "interval" }) }
            when (draft.kind) {
                "interval" -> StepperRow("Every", draft.every, "min of use", 15, min = 1) { draft = draft.copy(every = it) }
                "times" -> OutlinedTextField(timesText, { v -> timesText = v; draft = draft.copy(times = v.split(",").map { it.trim() }.filter { it.isNotEmpty() }) },
                    label = { Text("Times (e.g. 09:00, 13:00)") }, isError = !timesOk, modifier = Modifier.fillMaxWidth())
                else -> Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    val from = draft.window.getOrElse(0) { "10:00" }; val to = draft.window.getOrElse(1) { "18:00" }
                    OutlinedTextField(from, { draft = draft.copy(window = listOf(it, to)) }, label = { Text("From") }, singleLine = true, isError = validTime(from) == null, modifier = Modifier.weight(1f))
                    OutlinedTextField(to, { draft = draft.copy(window = listOf(from, it)) }, label = { Text("To") }, singleLine = true, isError = validTime(to) == null, modifier = Modifier.weight(1f))
                }
            }
            SectionLabel("Days")
            DaysRow(draft.days) { draft = draft.copy(days = it) }
            Card(color = cs.surfaceContainerLow) {
                if (draft.kind == "interval") SwitchRowInline("Only inside an hours window", draft.hours) { draft = draft.copy(hours = it) }
                SwitchRowInline("Ask \"did you do it?\" after", draft.check > 0) { on -> draft = draft.copy(check = if (on) 10 else 0) }
                SwitchRowInline("Important (challenge to turn off)", draft.guarded) { draft = draft.copy(guarded = it) }
                SwitchRowInline("On", draft.on) { draft = draft.copy(on = it) }
            }
            StepperRow("Stop after (per day, 0 = no cap)", draft.perDay, "×", 1) { draft = draft.copy(perDay = it) }
            TextButton(onClick = { guard(draft.guarded) { Store.update { s -> s.copy(customs = s.customs.filterNot { it.id == draft.id }) }; onClose() } }) { Text("Delete reminder") }
        }
    }
}

@Composable
private fun StepperRow(label: String, value: Int, suffix: String, step: Int, min: Int = 0, max: Int = Int.MAX_VALUE, onChange: (Int) -> Unit) {
    val cs = MaterialTheme.colorScheme
    Card(color = cs.surfaceContainerLow) {
        Text(label, style = MaterialTheme.typography.bodyMedium)
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(onClick = { onChange((value - step).coerceAtLeast(min)) }) { Text("−$step") }
            Numeral("$value", sizeSp = 30)
            Text(suffix, style = MaterialTheme.typography.bodyMedium)
            TextButton(onClick = { onChange((value + step).coerceAtMost(maxOf(max, value))) }) { Text("+$step") }
        }
    }
}

@Composable
private fun DaysRow(days: List<Int>, onChange: (List<Int>) -> Unit) {
    val labels = listOf("M", "T", "W", "T", "F", "S", "S")
    val cs = MaterialTheme.colorScheme
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        labels.forEachIndexed { i, l ->
            val on = i in days
            Box(
                Modifier.size(38.dp).clip(CircleShape)
                    .background(if (on) cs.primaryContainer else cs.surfaceContainerHigh)
                    .clickable { onChange(if (on) days - i else (days + i).sorted()) },
                contentAlignment = Alignment.Center,
            ) { Text(l, color = if (on) cs.onPrimaryContainer else cs.onSurface) }
        }
    }
}

/** "every 15 → 5 → 1 min" (the steps' intervals in order), or the flat one with no steps. */
private fun stepsText(s: SleepCfg): String =
    if (s.tiers.isEmpty()) "every ${s.repeat} min" else "every " + s.tiers.joinToString(" → ") { "${it.every}" } + " min"

private fun updateCustom(id: String, block: (CustomCfg) -> CustomCfg) =
    Store.update { s -> s.copy(customs = s.customs.map { if (it.id == id) block(it) else it }) }

private fun scheduleText(c: CustomCfg): String = when (c.kind) {
    "times" -> "At ${c.times.joinToString(", ")}"
    "random" -> "Once a day, ${c.window.getOrElse(0) { "" }}–${c.window.getOrElse(1) { "" }}"
    else -> "Every ${c.every} min of use" + if (c.hours) " · ${c.window.getOrElse(0) { "" }}–${c.window.getOrElse(1) { "" }}" else ""
} + if (c.perDay > 0) " · stops after ${c.perDay}×" else ""
