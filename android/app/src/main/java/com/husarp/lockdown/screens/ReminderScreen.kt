package com.husarp.lockdown.screens

import android.app.TimePickerDialog
import android.content.Context
import android.text.format.DateFormat
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.PressInteraction
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
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Bedtime
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Coffee
import androidx.compose.material.icons.filled.HourglassTop
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.NotificationsActive
import androidx.compose.material.icons.filled.Repeat
import androidx.compose.material.icons.filled.TextFields
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
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.CustomCfg
import com.husarp.lockdown.engine.Interval
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
import com.husarp.lockdown.ui.SaveBar
import com.husarp.lockdown.ui.SectionLabel
import com.husarp.lockdown.ui.Segmented
import com.husarp.lockdown.ui.IconTile
import com.husarp.lockdown.ui.Kinds
import com.husarp.lockdown.ui.Pill
import com.husarp.lockdown.ui.theme.Hue
import com.husarp.lockdown.ui.theme.tone
import com.husarp.lockdown.ui.SwitchRowInline
import com.husarp.lockdown.ui.UnsavedChanges
import com.husarp.lockdown.ui.rememberDraft
import java.time.LocalTime
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
            val indigo = tone(Hue.INDIGO)
            Card(color = indigo.container, contentColor = indigo.onContainer, shape = MaterialTheme.shapes.extraLarge, onClick = { open(RemPage.Bedtime) }) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(40.dp).background(cs.surface.copy(alpha = 0.6f), RoundedCornerShape(12.dp)), contentAlignment = Alignment.Center) {
                        Icon(Icons.Filled.Bedtime, null, tint = indigo.accent, modifier = Modifier.size(22.dp))
                    }
                    Spacer(Modifier.size(12.dp))
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
                Numeral("${cfg.sleep.bedtime} → ${cfg.sleep.wake}", sizeSp = 36, color = indigo.onContainer)
                Spacer(Modifier.height(8.dp))
                Text((if (cfg.sleep.before > 0) "Heads-up ${cfg.sleep.before} min before · " else "") +
                    "full-screen at bedtime · comes back ${stepsText(cfg.sleep)}",
                    style = MaterialTheme.typography.bodySmall, color = indigo.onContainer)
            }
            // Breaks
            Card(color = cs.surfaceContainerLow, onClick = { open(RemPage.Breaks) }) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    IconTile(Icons.Filled.Coffee, Hue.TEAL)
                    Spacer(Modifier.size(14.dp))
                    Column(Modifier.weight(1f)) {
                        Text("Breaks", style = MaterialTheme.typography.titleSmall)
                        Text("${cfg.breaks.length} min every ${Interval.text(cfg.breaks.everySeconds)}" + if (cfg.breaks.twenty) " · 20-20-20 on" else "",
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
                        IconTile(Icons.Filled.NotificationsActive, Hue.AMBER, on = c.on)
                        Spacer(Modifier.size(14.dp))
                        Column(Modifier.weight(1f)) {
                            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text(c.text.ifBlank { "(no text)" }, style = MaterialTheme.typography.titleSmall,
                                    modifier = Modifier.weight(1f, fill = false), maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                                if (c.guarded) Pill("Important", Hue.VIOLET, Icons.Filled.Lock)
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

/** What the bedtime editor edits, as a draft: bedtime (its on/off stays on the list) and bedtime grayscale. */
private data class BedtimeDraft(val sleep: SleepCfg, val grayscale: Boolean)

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun BedtimeEditor(onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val modes by ModesStore.modes.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    val guard = rememberGuard()
    val cs = MaterialTheme.colorScheme
    // A draft, saved with Save (one challenge for the whole edit). Steps, heads-up, texts and the mode are ordinary
    // edits, as on the PC; only un-flagging Important, or a shorter wait before Dismiss, asks for the challenge.
    val d = rememberDraft(BedtimeDraft(cfg.sleep, cfg.settings.bedtimeGrayscale))
    val s = d.value.sleep
    // the steps re-order as soon as a step is added or its time changes
    fun set(block: (SleepCfg) -> SleepCfg) = d.update { it.copy(sleep = block(it.sleep).let { c -> c.copy(tiers = RemindersEngine.sortSteps(c.tiers, c.bedtime)) }) }
    fun save(then: () -> Unit) {
        val new = d.value.let { it.copy(sleep = it.sleep.copy(tiers = RemindersEngine.sortSteps(it.sleep.tiers, it.sleep.bedtime))) }
        val old = Store.config.let { BedtimeDraft(it.sleep, it.settings.bedtimeGrayscale) }
        guard(RemindersEngine.bedtimeLooser(old.sleep, new.sleep)) {
            Store.update { c -> c.copy(sleep = new.sleep.copy(on = c.sleep.on), settings = c.settings.copy(bedtimeGrayscale = new.grayscale)) }
            if (new.grayscale != old.grayscale) { if (new.grayscale) Grayscale.sync(ctx) else Grayscale.switchedOff(ctx) }
            d.clear()
            then()
        }
    }
    UnsavedChanges(d.dirty, onClose, ::save, d::clear)
    Column(Modifier.fillMaxSize()) {
        EditorHeader("Bedtime", onBack = { com.husarp.lockdown.ui.Unsaved.leave(onClose) })
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Card(color = cs.surfaceContainerLow) {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    // the steps move with the bedtime
                    TimeField("Bedtime", s.bedtime, Modifier.weight(1f)) { t ->
                        set { c -> c.copy(bedtime = t, tiers = RemindersEngine.shiftSteps(c.tiers, c.bedtime, t)) }
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
                CardTitle(Icons.Filled.Repeat, "Comes back after you dismiss it")
                s.tiers.forEachIndexed { i, t ->
                    key(i, d.resets) {
                        TierRow(s, t, onChange = { n -> set { c -> c.copy(tiers = c.tiers.mapIndexed { j, x -> if (j == i) n else x }) } },
                            onRemove = { set { c -> c.copy(tiers = c.tiers.filterIndexed { j, _ -> j != i }) } })
                    }
                }
                if (s.tiers.isEmpty()) Text("Every ${Interval.text(s.repeatSeconds)}, all night.", style = MaterialTheme.typography.bodyMedium)
                TextButton(onClick = { set { c -> c.copy(tiers = c.tiers + newStep(c)) } }) { Text("+ Add step") }
                Text("The later it is, the more often the bedtime screen returns after you dismiss it. " +
                    "A step earlier in the evening than bedtime counts from bedtime. Type how often like 30 s, 5 min or 1h (at least 5 s).",
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
            Card(color = cs.surfaceContainerLow) {
                // shorter (or off) loosens bedtime: the challenge on Save; longer is free
                CardTitle(Icons.Filled.HourglassTop, "Wait before Dismiss")
                Spacer(Modifier.height(8.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    for (n in RemindersEngine.DISMISS_WAITS) Chip(if (n == 0) "Off" else "$n s", s.dismissWait == n) { set { it.copy(dismissWait = n) } }
                }
                var waitText by remember(s.dismissWait, d.resets) { mutableStateOf(if (s.dismissWait > 0) s.dismissWait.toString() else "") }
                OutlinedTextField(waitText, { v ->
                        waitText = v.filter(Char::isDigit).take(3)
                        RemindersEngine.parseDismissWait(waitText)?.let { n -> set { it.copy(dismissWait = n) } }
                    },
                    label = { Text("Or any (seconds)") }, singleLine = true,
                    isError = waitText.isNotEmpty() && RemindersEngine.parseDismissWait(waitText) == null,
                    supportingText = { Text("1 to ${RemindersEngine.DISMISS_WAIT_MAX} seconds.") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.fillMaxWidth())
                Text("Dismiss on the bedtime screen counts down and works only when it ends, from the start each time " +
                    "the screen comes up. Disable alerts and Emergency don't wait. Longer is free; shorter or off needs the challenge.",
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
            Card(color = cs.surfaceContainerLow) {
                CardTitle(Kinds.MODE.icon, "Turn on a mode at bedtime, until wake-up", tone(Kinds.MODE.hue).accent)
                Spacer(Modifier.height(8.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Chip("No mode", s.mode.isEmpty()) { set { it.copy(mode = "") } }
                    modes.forEach { m -> Chip(m.name, s.mode == m.id, icon = Kinds.modeIcon(m.id), hue = Kinds.MODE.hue) { set { it.copy(mode = m.id) } } }
                }
            }
            Card(color = cs.surfaceContainerLow) {
                CardTitle(Icons.Filled.TextFields, "What it says")
                Spacer(Modifier.height(8.dp))
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
                SwitchRowInline("Grayscale the screen at bedtime", d.value.grayscale) { on -> d.update { it.copy(grayscale = on) } }
                GrayscaleNote(d.value.grayscale)
            }
            SwitchRowInline("Important (need the challenge to turn off)", s.guarded) { on -> set { it.copy(guarded = on) } }
            Text("At bedtime the screen goes dark with \"Time for bed\": Dismiss, Disable alerts (asks for the challenge), " +
                "or an emergency unlock while you have one.", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
        }
        SaveBar(d.dirty, onDiscard = d::clear) { save {} }
    }
}

/** An editor card's title with its icon (grey, or [tint]). */
@Composable
private fun CardTitle(icon: androidx.compose.ui.graphics.vector.ImageVector, text: String, tint: androidx.compose.ui.graphics.Color? = null) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Icon(icon, null, Modifier.size(20.dp), tint = tint ?: MaterialTheme.colorScheme.onSurfaceVariant)
        Text(text, style = MaterialTheme.typography.titleSmall)
    }
}

/** One escalation step: "from HH:MM, every 5 min / 30 s", with × to remove it. */
@Composable
private fun TierRow(s: SleepCfg, t: ReminderTier, onChange: (ReminderTier) -> Unit, onRemove: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        TimeField("From", t.from, Modifier.weight(1f)) { onChange(t.copy(from = it)) }
        IntervalField("Every", t.seconds, Modifier.weight(1f), hint = null) { onChange(t.copy(everySec = it)) }
        IconButton(onClick = onRemove) { Icon(Icons.Filled.Close, "Remove step") }
    }
    if (RemindersEngine.stepFromBedtime(s, t.from))
        Text("Before bedtime: counts from ${s.bedtime}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

/** A new step: at bedtime if there is none, else an hour after the last one, as often. */
private fun newStep(s: SleepCfg): ReminderTier {
    val last = s.tiers.lastOrNull() ?: return ReminderTier(s.bedtime, everySec = 15 * 60)
    val t = runCatching { Rules.parseHhmm(last.from).plusHours(1) }.getOrNull() ?: return ReminderTier(s.bedtime, everySec = 15 * 60)
    return ReminderTier("%02d:%02d".format(t.hour, t.minute), everySec = last.seconds)
}

/** A "how often" box: "30 s", "5 min", "1h30" (a bare number is minutes, as before), 5 s to 24 h. Only a real time
 *  reaches [onChange]; while the box holds something else it shows the error and the setting keeps its last value.
 *  [optional]: empty is fine (a choice next to it covers it, e.g. Never). */
@Composable
internal fun IntervalField(label: String, seconds: Int, modifier: Modifier = Modifier.fillMaxWidth(), optional: Boolean = false,
                           hint: String? = "e.g. 30 s, 5 min or 1h30 - at least 5 s", onChange: (Int) -> Unit) {
    fun shown(sec: Int) = if (sec > 0) Interval.text(sec) else ""
    var text by remember { mutableStateOf(shown(seconds)) }
    // a change from outside (Discard, a chip) shows, unless what's typed already says it ("3" while typing 3 min)
    LaunchedEffect(seconds) { if (Interval.parse(text) != seconds) text = shown(seconds) }
    val bad = if (text.isBlank()) !optional else Interval.parse(text) == null
    OutlinedTextField(text, { v -> text = v; Interval.parse(v)?.let { if (it != seconds) onChange(it) } },
        label = { Text(label) }, singleLine = true, isError = bad,
        supportingText = if (hint != null) { { Text(hint) } } else null, modifier = modifier)
}

/** "9:30" / "09:30" as "09:30", or null while it isn't a time yet. */
internal fun validTime(v: String): String? {
    val m = Regex("""(\d{1,2}):(\d{2})""").matchEntire(v.trim()) ?: return null
    val h = m.groupValues[1].toInt(); val min = m.groupValues[2].toInt()
    return if (h < 24 && min < 60) "%02d:%02d".format(h, min) else null
}

/** Android's own clock dialog (24 h when the phone uses it), starting at [value] (or now if it isn't a time). */
internal fun pickTime(ctx: Context, value: String, onPick: (String) -> Unit) {
    val t = validTime(value)?.let { Rules.parseHhmm(it) } ?: LocalTime.now()
    TimePickerDialog(ctx, { _, h, m -> onPick("%02d:%02d".format(h, m)) }, t.hour, t.minute, DateFormat.is24HourFormat(ctx)).show()
}

/** A time you tap to change with the system clock dialog: no typing, so only a real time ever reaches the settings. */
@Composable
internal fun TimeField(label: String, value: String, modifier: Modifier, onValid: (String) -> Unit) {
    val ctx = LocalContext.current
    val src = remember { MutableInteractionSource() }
    val cur by rememberUpdatedState(value)
    val pick by rememberUpdatedState(onValid)
    LaunchedEffect(src) {
        src.interactions.collect { if (it is PressInteraction.Release) pickTime(ctx, cur) { t -> if (t != cur) pick(t) } }
    }
    OutlinedTextField(validTime(value) ?: value, {}, label = { Text(label) }, readOnly = true, singleLine = true,
        isError = validTime(value) == null, interactionSource = src, modifier = modifier)
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
    // A draft, saved with Save; un-flagging Important or adding an app to "Allowed during breaks" asks for the challenge (on/off stays on the list).
    val d = rememberDraft(cfg.breaks)
    val b = d.value
    fun set(block: (com.husarp.lockdown.engine.BreakCfg) -> com.husarp.lockdown.engine.BreakCfg) = d.update(block)
    fun save(then: () -> Unit) {
        val new = d.value
        guard(RemindersEngine.breaksLooser(Store.config.breaks, new)) {
            Store.update { c -> c.copy(breaks = new.copy(on = c.breaks.on, allowed = com.husarp.lockdown.engine.BreakAllow.clean(new.allowed))) }
            d.clear()
            then()
        }
    }
    UnsavedChanges(d.dirty, onClose, ::save, d::clear)
    var picking by remember { mutableStateOf(false) }
    if (picking) {
        BreakAppsPicker(b.allowed, { list -> set { c -> c.copy(allowed = list) } }) { picking = false }
        return
    }
    Column(Modifier.fillMaxSize()) {
        EditorHeader("Breaks", onBack = { com.husarp.lockdown.ui.Unsaved.leave(onClose) })
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Card(color = cs.surfaceContainerLow) {
                IntervalField("Remind me after this much use", b.everySeconds) { v -> set { c -> c.copy(everySec = v) } }
            }
            StepperRow("Break length", b.length, "min", 1, min = 1, max = 60) { set { c -> c.copy(length = it) } }
            Card(color = cs.surfaceContainerLow) {
                IntervalField("Snooze, each time", b.snoozeSeconds) { v -> set { c -> c.copy(snoozeSec = v) } }
            }
            Card(color = cs.surfaceContainerLow) {
                SwitchRowInline("Strict break (covers the screen until it's over)", b.strict) { set { c -> c.copy(strict = it) } }
                SwitchRowInline("20-20-20 eye breaks", b.twenty) { set { c -> c.copy(twenty = it) } }
                SwitchRowInline("Important (need the challenge to turn off)", b.guarded) { on -> set { it.copy(guarded = on) } }
            }
            if (b.strict) StepperRow("Snoozes first", b.maxSnooze, "then it starts on its own", 1, min = 1) { set { c -> c.copy(maxSnooze = it) } }
            if (b.strict) AllowedDuringBreaks(b.allowed, { list -> set { c -> c.copy(allowed = list) } }) { picking = true }
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
        SaveBar(d.dirty, onDiscard = d::clear) { save {} }
    }
}

/** "Allowed during breaks": the apps a strict break leaves usable, on top of the built-in ones (Lockdown, the home
 *  screen, Settings, phone, clock, keyboard, emergency apps). Removing one is free; adding needs the challenge on Save. */
@Composable
private fun AllowedDuringBreaks(allowed: List<String>, onChange: (List<String>) -> Unit, onAdd: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    val pm = LocalContext.current.packageManager
    Card(color = cs.surfaceContainerLow) {
        Text("Allowed during breaks", style = MaterialTheme.typography.titleSmall)
        Spacer(Modifier.height(4.dp))
        Text("Dismiss on the break screen goes home, and these apps stay usable. Always allowed: Lockdown, the home screen, " +
            "Settings, phone calls, the clock, the keyboard and emergency apps. Adding an app needs the challenge when you save.",
            style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
        for (pkg in allowed) {
            val label = remember(pkg) { com.husarp.lockdown.block.Apps.label(pm, pkg) }
            com.husarp.lockdown.ui.ListRow(title = label, subtitle = pkg, leading = { com.husarp.lockdown.ui.AppIcon(pkg, label) },
                trailing = { IconButton(onClick = { onChange(allowed - pkg) }) { Icon(Icons.Filled.Close, "Remove $label") } })
        }
        TextButton(onClick = onAdd) { Text("Add apps") }
    }
}

/** The installed apps with a tick for each one allowed during breaks (search at the top). Edits the draft only. */
@Composable
private fun BreakAppsPicker(allowed: List<String>, onChange: (List<String>) -> Unit, onDone: () -> Unit) {
    androidx.activity.compose.BackHandler(onBack = onDone)
    val apps = rememberInstalledApps()
    var q by remember { mutableStateOf("") }
    val shown = apps.filter { it.label.contains(q, true) || it.pkg.contains(q, true) }
    Column(Modifier.fillMaxSize()) {
        EditorHeader("Allowed during breaks", onBack = onDone, action = "Done", onAction = onDone)
        OutlinedTextField(q, { q = it }, placeholder = { Text("Search apps") }, singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp))
        androidx.compose.foundation.lazy.LazyColumn(Modifier.fillMaxSize()) {
            items(shown, key = { it.pkg }) { a ->
                val on = a.pkg in allowed
                fun toggle() = onChange(if (on) allowed - a.pkg else allowed + a.pkg)
                com.husarp.lockdown.ui.ListRow(title = a.label, subtitle = a.pkg,
                    leading = { com.husarp.lockdown.ui.AppIcon(a.pkg, a.label) },
                    trailing = { androidx.compose.material3.Checkbox(checked = on, onCheckedChange = { toggle() }) },
                    onClick = { toggle() })
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CustomEditor(c: CustomCfg, onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val guard = rememberGuard()
    val cs = MaterialTheme.colorScheme
    val ctx = LocalContext.current
    var draft by remember { mutableStateOf(cfg.customs.firstOrNull { it.id == c.id } ?: c) }
    val kindIdx = when (draft.kind) { "times" -> 1; "random" -> 2; else -> 0 }
    val timesOk = draft.times.isNotEmpty() && draft.times.all { validTime(it) != null }
    val windowOk = draft.window.size == 2 && draft.window.all { validTime(it) != null }

    fun save(then: () -> Unit) {
        // only real times are saved (a half-typed "9" used to be, and the reminder never came)
        if ((draft.kind == "times" && !timesOk) || ((draft.kind == "random" || draft.hours) && !windowOk)) {
            Toast.makeText(ctx, "Write the times as HH:MM, e.g. 09:30.", Toast.LENGTH_LONG).show()
            return
        }
        draft = draft.copy(times = draft.times.map { validTime(it) ?: it }, window = draft.window.map { validTime(it) ?: it })
        val old = Store.config.customs.firstOrNull { it.id == draft.id }
        val loosen = old != null && RemindersEngine.loosensReminder(old.on, old.guarded, draft.on, draft.guarded)
        guard(loosen) {
            Store.update { s -> if (s.customs.any { it.id == draft.id }) s.copy(customs = s.customs.map { if (it.id == draft.id) draft else it }) else s.copy(customs = s.customs + draft) }
            then()
        }
    }
    UnsavedChanges(draft != (cfg.customs.firstOrNull { it.id == c.id } ?: c), onClose, ::save)

    Column(Modifier.fillMaxSize()) {
        EditorHeader(draft.text.ifBlank { "New reminder" }, onBack = { com.husarp.lockdown.ui.Unsaved.leave(onClose) }, action = "Save") { save(onClose) }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedTextField(draft.text, { draft = draft.copy(text = it) }, label = { Text("What should it say?") }, modifier = Modifier.fillMaxWidth())
            SectionLabel("When")
            Segmented(listOf("Every…", "Set times", "Random once"), kindIdx) { draft = draft.copy(kind = when (it) { 1 -> "times"; 2 -> "random"; else -> "interval" }) }
            when (draft.kind) {
                "interval" -> Card(color = cs.surfaceContainerLow) {
                    IntervalField("Every (of use)", draft.everySeconds) { draft = draft.copy(everySec = it) }
                }
                "times" -> FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    // tap a time to change it, × to remove it, + to add one (all with the system clock dialog)
                    draft.times.forEachIndexed { i, tm ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Chip(validTime(tm) ?: tm, true) { pickTime(ctx, tm) { v -> draft = draft.copy(times = draft.times.mapIndexed { j, x -> if (j == i) v else x }) } }
                            IconButton(onClick = { draft = draft.copy(times = draft.times.filterIndexed { j, _ -> j != i }) }) { Icon(Icons.Filled.Close, "Remove time") }
                        }
                    }
                    TextButton(onClick = { pickTime(ctx, "09:00") { v -> draft = draft.copy(times = draft.times + v) } }) { Text("+ Add time") }
                }
                else -> Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    val from = draft.window.getOrElse(0) { "10:00" }; val to = draft.window.getOrElse(1) { "18:00" }
                    TimeField("From", from, Modifier.weight(1f)) { draft = draft.copy(window = listOf(it, to)) }
                    TimeField("To", to, Modifier.weight(1f)) { draft = draft.copy(window = listOf(from, it)) }
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

/** "every 15 min → 5 min → 30 s" (the steps' intervals in order), or the flat one with no steps. */
private fun stepsText(s: SleepCfg): String =
    "every " + if (s.tiers.isEmpty()) Interval.text(s.repeatSeconds) else s.tiers.joinToString(" → ") { Interval.text(it.seconds) }

private fun updateCustom(id: String, block: (CustomCfg) -> CustomCfg) =
    Store.update { s -> s.copy(customs = s.customs.map { if (it.id == id) block(it) else it }) }

private fun scheduleText(c: CustomCfg): String = when (c.kind) {
    "times" -> "At ${c.times.joinToString(", ")}"
    "random" -> "Once a day, ${c.window.getOrElse(0) { "" }}–${c.window.getOrElse(1) { "" }}"
    else -> "Every ${Interval.text(c.everySeconds)} of use" + if (c.hours) " · ${c.window.getOrElse(0) { "" }}–${c.window.getOrElse(1) { "" }}" else ""
} + if (c.perDay > 0) " · stops after ${c.perDay}×" else ""
