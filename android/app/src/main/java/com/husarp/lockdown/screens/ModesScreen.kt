package com.husarp.lockdown.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Mode
import com.husarp.lockdown.engine.Modes
import com.husarp.lockdown.engine.Pomodoro
import com.husarp.lockdown.guard.rememberGuard
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.EditorHeader
import com.husarp.lockdown.ui.ListRow
import com.husarp.lockdown.ui.SectionLabel
import com.husarp.lockdown.ui.SwitchRowInline

private val CATS = listOf("distracting" to "Distracting", "neutral" to "Neutral", "productive" to "Productive")

@Composable
fun ModesScreen() {
    var editing by remember { mutableStateOf<Mode?>(null) }
    val e = editing
    if (e == null) ModeList(onEdit = { editing = it }) else ModeEditor(e, onClose = { editing = null })
}

@Composable
private fun ModeList(onEdit: (Mode) -> Unit) {
    val modes by ModesStore.modes.collectAsStateWithLifecycle()
    val active by ModesStore.active.collectAsStateWithLifecycle()
    val guard = rememberGuard()
    val cs = MaterialTheme.colorScheme
    val now = com.husarp.lockdown.guard.TrustedTime.local(androidx.compose.ui.platform.LocalContext.current)     // trusted time, as the blocking uses
    val current = ModesStore.current(now)

    LazyColumn(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        items(modes) { mode ->
            val isOn = current?.mode?.id == mode.id
            Card(color = if (isOn) cs.primaryContainer else cs.surfaceContainerLow, contentColor = if (isOn) cs.onPrimaryContainer else cs.onSurface, onClick = { onEdit(mode) }) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(mode.name, style = MaterialTheme.typography.titleMedium)
                        Text(Modes.describe(mode), style = MaterialTheme.typography.bodySmall, color = if (isOn) cs.onPrimaryContainer else cs.onSurfaceVariant)
                    }
                    if (isOn) {
                        Button(onClick = {
                            val locked = current?.locked == true && current?.scheduled == false
                            guard(locked) { ModesStore.stop(force = true) }
                        }, colors = ButtonDefaults.buttonColors(containerColor = cs.primary)) {
                            Icon(Icons.Filled.Stop, null); Spacer(Modifier.width(6.dp)); Text("Stop")
                        }
                    }
                }
            }
        }
        item {
            Card(color = cs.surfaceContainerLow, onClick = {
                onEdit(Mode(Modes.newId(modes), "New mode", categories = listOf("distracting")))
            }) { Text("+ New mode", style = MaterialTheme.typography.titleSmall, color = cs.primary) }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ModeEditor(mode: Mode, onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val cs = MaterialTheme.colorScheme
    var draft by remember { mutableStateOf(mode) }
    var byHand by remember { mutableStateOf(mode.schedule == null) }
    var durationMin by remember { mutableStateOf(90) }
    var locked by remember { mutableStateOf(false) }
    val ctx = androidx.compose.ui.platform.LocalContext.current

    fun persist() = ModesStore.saveModes(ModesStore.modes.value.filter { it.id != draft.id } + draft)

    Column(Modifier.fillMaxSize()) {
        EditorHeader(draft.name, onBack = onClose, action = "Save") { persist(); onClose() }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Card(color = cs.surfaceContainer, shape = MaterialTheme.shapes.extraLarge) {
                Text(Modes.describe(draft) + (if (draft.pomodoro != null) " · Pomodoro" else ""), style = MaterialTheme.typography.bodyLarge)
            }
            if (!draft.builtin) OutlinedTextField(draft.name, { draft = draft.copy(name = it) }, label = { Text("Name") }, singleLine = true, modifier = Modifier.fillMaxWidth())

            SectionLabel("What it blocks")
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                CATS.forEach { (key, label) ->
                    Chip(label, key in draft.categories) {
                        draft = draft.copy(categories = if (key in draft.categories) draft.categories - key else draft.categories + key)
                    }
                }
            }

            SectionLabel("Also block these")
            var pick by remember { mutableStateOf("") }
            OutlinedTextField(pick, { pick = it }, placeholder = { Text("Search apps & items") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            val shown = cfg.items.filter { it.name.contains(pick, true) || it.target.contains(pick, true) }
            // Installed apps that aren't on the blocklist can be blocked by the mode too (as a mode extra): the
            // search used to look only at the list, so an app not on it never came up.
            // (Shown once something is typed - the hundreds of installed apps would bury the list - or when already picked.)
            val installed = rememberInstalledApps()
            val listedPkgs = remember(cfg.items) { cfg.items.filter { it.type == ItemType.APP }.mapTo(HashSet()) { it.target.lowercase() } }
            fun isExtra(pkg: String) = draft.extra.any { it.kind == ItemType.APP && it.targets == listOf(pkg) }
            val apps = installed.filter { a -> a.pkg.lowercase() !in listedPkgs &&
                ((pick.isNotBlank() && (a.label.contains(pick, true) || a.pkg.contains(pick, true))) || isExtra(a.pkg)) }
            Card(color = cs.surfaceContainerLow, padding = androidx.compose.foundation.layout.PaddingValues(vertical = 4.dp)) {
                if (shown.isEmpty() && apps.isEmpty()) Text("Nothing matches.", Modifier.padding(16.dp), color = cs.onSurfaceVariant)
                androidx.compose.foundation.lazy.LazyColumn(Modifier.fillMaxWidth().heightIn(max = 280.dp)) {
                    items(shown, key = { it.id }) { it2 ->
                        ListRow(title = it2.name, subtitle = it2.target, trailing = {
                            Checkbox(checked = it2.id in draft.items, onCheckedChange = { on ->
                                draft = draft.copy(items = if (on) draft.items + it2.id else draft.items - it2.id)
                            })
                        })
                    }
                    items(apps, key = { "pkg:" + it.pkg }) { a ->
                        ListRow(title = a.label, subtitle = "App · not on your list", trailing = {
                            Checkbox(checked = isExtra(a.pkg), onCheckedChange = { on ->
                                draft = draft.copy(extra = if (on) draft.extra + com.husarp.lockdown.engine.ModeExtra(a.label, ItemType.APP, listOf(a.pkg))
                                    else draft.extra.filterNot { it.kind == ItemType.APP && it.targets == listOf(a.pkg) })
                            })
                        })
                    }
                }
            }

            Card(color = cs.surfaceContainerLow) {
                SwitchRowInline("Mute notifications", draft.mute) { draft = draft.copy(mute = it) }
                SwitchRowInline("Focus (Pomodoro)", draft.pomodoro != null) { on -> draft = draft.copy(pomodoro = if (on) Pomodoro() else null) }
                draft.pomodoro?.let { p ->
                    Text("${p.work} min work · ${p.brk} min break · ${p.rounds} rounds · ${p.long} min long break",
                        style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                }
            }

            SectionLabel("How it starts")
            com.husarp.lockdown.ui.Segmented(listOf("By hand", "On a schedule"), if (byHand) 0 else 1) { byHand = it == 0 }
            if (byHand) {
                Card(color = cs.surfaceContainerLow) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        androidx.compose.material3.TextButton(onClick = { durationMin = (durationMin - 15).coerceAtLeast(0) }) { Text("−15") }
                        com.husarp.lockdown.ui.Numeral(if (durationMin == 0) "∞" else "$durationMin", sizeSp = 30)
                        Text(if (durationMin == 0) "until stopped" else "min", style = MaterialTheme.typography.bodyMedium)
                        androidx.compose.material3.TextButton(onClick = { durationMin += 15 }) { Text("+15") }
                    }
                    if (durationMin > 0) SwitchRowInline("Lock until it ends", locked) { locked = it }
                }
                Button(onClick = {
                    persist()
                    val now = com.husarp.lockdown.guard.TrustedTime.local(ctx)   // trusted time: a changed clock doesn't shorten it
                    val until = if (durationMin > 0) now.plusMinutes(durationMin.toLong()) else null
                    ModesStore.start(draft.id, until, locked, now)
                    onClose()
                }, modifier = Modifier.fillMaxWidth()) {
                    Icon(Icons.Filled.PlayArrow, null); Spacer(Modifier.width(8.dp)); Text("Start now")
                }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}
