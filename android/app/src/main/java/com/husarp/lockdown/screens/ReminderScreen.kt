package com.husarp.lockdown.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.material.icons.filled.Coffee
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.CustomCfg
import com.husarp.lockdown.engine.RemindersEngine
import com.husarp.lockdown.guard.rememberGuard
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
                        if (on) Store.update { it.copy(sleep = it.sleep.copy(on = true)) }
                        else guard(cfg.sleep.guarded) { Store.update { it.copy(sleep = it.sleep.copy(on = false)) } }
                    })
                }
                Spacer(Modifier.height(10.dp))
                Numeral("${cfg.sleep.bedtime} → ${cfg.sleep.wake}", sizeSp = 36)
                Spacer(Modifier.height(8.dp))
                Text("Heads-up ${cfg.sleep.before} min before · escalating nudges · full-screen at bedtime",
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

@Composable
private fun BedtimeEditor(onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val cs = MaterialTheme.colorScheme
    Column(Modifier.fillMaxSize()) {
        EditorHeader("Bedtime", onBack = onClose)
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Card(color = cs.surfaceContainerLow) {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    OutlinedTextField(cfg.sleep.bedtime, { Store.update { c -> c.copy(sleep = c.sleep.copy(bedtime = it)) } }, label = { Text("Bedtime") }, singleLine = true, modifier = Modifier.weight(1f))
                    OutlinedTextField(cfg.sleep.wake, { Store.update { c -> c.copy(sleep = c.sleep.copy(wake = it)) } }, label = { Text("Wake") }, singleLine = true, modifier = Modifier.weight(1f))
                }
                Spacer(Modifier.height(10.dp))
                Text("Heads-up ${cfg.sleep.before} min before", style = MaterialTheme.typography.bodyMedium)
                Row {
                    TextButton(onClick = { Store.update { c -> c.copy(sleep = c.sleep.copy(before = (c.sleep.before - 5).coerceAtLeast(0))) } }) { Text("−5") }
                    TextButton(onClick = { Store.update { c -> c.copy(sleep = c.sleep.copy(before = c.sleep.before + 5)) } }) { Text("+5") }
                }
            }
            Card(color = cs.surfaceContainerLow) {
                Text("Escalating nudges", style = MaterialTheme.typography.titleSmall)
                cfg.sleep.tiers.forEach { Text("From ${it.from}: every ${it.every} min", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant) }
            }
            SwitchRowInline("Grayscale the screen at bedtime", cfg.settings.bedtimeGrayscale) { on -> Store.update { it.copy(settings = it.settings.copy(bedtimeGrayscale = on)) } }
            SwitchRowInline("Important (need the challenge to turn off)", cfg.sleep.guarded) { on -> Store.update { it.copy(sleep = it.sleep.copy(guarded = on)) } }
        }
    }
}

@Composable
private fun BreakEditor(onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val cs = MaterialTheme.colorScheme
    val b = cfg.breaks
    fun set(block: (com.husarp.lockdown.engine.BreakCfg) -> com.husarp.lockdown.engine.BreakCfg) = Store.update { it.copy(breaks = block(it.breaks)) }
    Column(Modifier.fillMaxSize()) {
        EditorHeader("Breaks", onBack = onClose)
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            StepperRow("Remind every", b.every, "min", 5) { set { c -> c.copy(every = it) } }
            StepperRow("Break length", b.length, "min", 1) { set { c -> c.copy(length = it) } }
            Card(color = cs.surfaceContainerLow) {
                SwitchRowInline("Strict break (starts on its own)", b.strict) { set { c -> c.copy(strict = it) } }
                SwitchRowInline("20-20-20 eye breaks", b.twenty) { set { c -> c.copy(twenty = it) } }
            }
        }
    }
}

@Composable
private fun CustomEditor(c: CustomCfg, onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val guard = rememberGuard()
    val cs = MaterialTheme.colorScheme
    var draft by remember { mutableStateOf(cfg.customs.firstOrNull { it.id == c.id } ?: c) }
    val kindIdx = when (draft.kind) { "times" -> 1; "random" -> 2; else -> 0 }

    Column(Modifier.fillMaxSize()) {
        EditorHeader(draft.text.ifBlank { "New reminder" }, onBack = onClose, action = "Save") {
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
                "interval" -> StepperRow("Every", draft.every, "min of use", 15) { draft = draft.copy(every = it) }
                "times" -> OutlinedTextField(draft.times.joinToString(", "), { v -> draft = draft.copy(times = v.split(",").map { it.trim() }.filter { it.isNotEmpty() }) }, label = { Text("Times (e.g. 09:00, 13:00)") }, modifier = Modifier.fillMaxWidth())
                else -> Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    OutlinedTextField(draft.window.getOrElse(0) { "10:00" }, { draft = draft.copy(window = listOf(it, draft.window.getOrElse(1) { "18:00" })) }, label = { Text("From") }, singleLine = true, modifier = Modifier.weight(1f))
                    OutlinedTextField(draft.window.getOrElse(1) { "18:00" }, { draft = draft.copy(window = listOf(draft.window.getOrElse(0) { "10:00" }, it)) }, label = { Text("To") }, singleLine = true, modifier = Modifier.weight(1f))
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
private fun StepperRow(label: String, value: Int, suffix: String, step: Int, onChange: (Int) -> Unit) {
    val cs = MaterialTheme.colorScheme
    Card(color = cs.surfaceContainerLow) {
        Text(label, style = MaterialTheme.typography.bodyMedium)
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(onClick = { onChange((value - step).coerceAtLeast(0)) }) { Text("−$step") }
            Numeral("$value", sizeSp = 30)
            Text(suffix, style = MaterialTheme.typography.bodyMedium)
            TextButton(onClick = { onChange(value + step) }) { Text("+$step") }
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

private fun updateCustom(id: String, block: (CustomCfg) -> CustomCfg) =
    Store.update { s -> s.copy(customs = s.customs.map { if (it.id == id) block(it) else it }) }

private fun scheduleText(c: CustomCfg): String = when (c.kind) {
    "times" -> "At ${c.times.joinToString(", ")}"
    "random" -> "Once a day, ${c.window.getOrElse(0) { "" }}–${c.window.getOrElse(1) { "" }}"
    else -> "Every ${c.every} min of use" + if (c.hours) " · ${c.window.getOrElse(0) { "" }}–${c.window.getOrElse(1) { "" }}" else ""
} + if (c.perDay > 0) " · stops after ${c.perDay}×" else ""
