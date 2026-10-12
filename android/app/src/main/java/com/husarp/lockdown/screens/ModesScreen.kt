package com.husarp.lockdown.screens

import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.contentDescription
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
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
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
import com.husarp.lockdown.engine.Schedule
import com.husarp.lockdown.engine.Window
import com.husarp.lockdown.guard.rememberGuard
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.EditorHeader
import com.husarp.lockdown.ui.IconTile
import com.husarp.lockdown.ui.Kinds
import com.husarp.lockdown.ui.theme.tone
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
    val blue = tone(Kinds.MODE.hue)

    LazyColumn(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        items(modes) { mode ->
            val isOn = current?.mode?.id == mode.id
            Card(color = if (isOn) blue.container else cs.surfaceContainerLow, contentColor = if (isOn) blue.onContainer else cs.onSurface, onClick = { onEdit(mode) }) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    IconTile(Kinds.modeIcon(mode.id), Kinds.MODE.hue)
                    Column(Modifier.weight(1f)) {
                        Text(mode.name, style = MaterialTheme.typography.titleMedium)
                        Text(Modes.describe(mode), style = MaterialTheme.typography.bodySmall, color = if (isOn) blue.onContainer else cs.onSurfaceVariant)
                        scheduleLine(mode, now)?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = if (isOn) blue.onContainer else cs.onSurfaceVariant, maxLines = 2, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis) }
                    }
                    if (isOn) {
                        Button(onClick = {
                            val locked = current?.locked == true && current?.scheduled == false
                            guard(locked) { ModesStore.stop(force = true) }
                        }, colors = ButtonDefaults.buttonColors(containerColor = blue.onContainer, contentColor = blue.container)) {
                            Icon(Icons.Filled.Stop, null); Spacer(Modifier.width(6.dp)); Text("Stop")
                        }
                    }
                }
            }
        }
        item {
            Card(color = cs.surfaceContainerLow, onClick = {
                onEdit(Mode(Modes.newId(modes), "New mode", categories = listOf("distracting")))
            }) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Icon(Icons.Filled.Add, null, tint = blue.accent)
                    Text("New mode", style = MaterialTheme.typography.titleSmall, color = blue.accent)
                }
            }
        }
    }
}

/** "Runs 09:00–17:00 Mon–Fri · next Mon 09:00" for a scheduled mode, else null. */
private fun scheduleLine(mode: Mode, now: java.time.LocalDateTime): String? {
    val base = Modes.scheduleText(mode.schedule) ?: return null
    val next = Modes.nextStart(mode, now) ?: return base
    return base + " · next " + next.dayOfWeek.getDisplayName(java.time.format.TextStyle.SHORT, java.util.Locale.ENGLISH) +
        " " + "%02d:%02d".format(next.hour, next.minute)
}

@Composable
private fun PomStepper(label: String, value: Int, range: IntRange, step: Int, onChange: (Int) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(label, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
        androidx.compose.material3.TextButton(onClick = { onChange((value - step).coerceIn(range)) },
            Modifier.semantics { contentDescription = "$label, less by $step" }) { Text("−$step") }
        Text("$value", Modifier.width(36.dp), style = MaterialTheme.typography.titleMedium, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
        androidx.compose.material3.TextButton(onClick = { onChange((value + step).coerceIn(range)) },
            Modifier.semantics { contentDescription = "$label, more by $step" }) { Text("+$step") }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ScheduleEditor(schedule: Schedule, onChange: (Schedule) -> Unit) {
    val windows = schedule.windows
    fun setWin(i: Int, w: Window?) = onChange(schedule.copy(windows = windows.toMutableList().also { if (w == null) it.removeAt(i) else it[i] = w }))
    val labels = listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    windows.forEachIndexed { i, win ->
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            labels.forEachIndexed { d, l ->
                Chip(l, d in win.days) { setWin(i, win.copy(days = if (d in win.days) win.days - d else (win.days + d).sorted())) }
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            TimeField("From", win.start, Modifier.weight(1f)) { setWin(i, win.copy(start = it)) }
            TimeField("To", win.end, Modifier.weight(1f)) { setWin(i, win.copy(end = it)) }
            if (windows.size > 1) androidx.compose.material3.IconButton(onClick = { setWin(i, null) }) {
                Icon(Icons.Filled.Close, "remove these hours")
            }
        }
    }
    androidx.compose.material3.TextButton(onClick = { onChange(schedule.copy(windows = windows + Window((0..4).toList(), "09:00", "17:00"))) }) { Text("+ Add hours") }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ModeEditor(mode: Mode, onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val cs = MaterialTheme.colorScheme
    var draft by remember { mutableStateOf(mode) }
    val guard = rememberGuard()
    var untilMode by remember { mutableStateOf(false) }     // start "until a time" instead of "for N minutes"
    var untilAt by remember { mutableStateOf("17:00") }
    var durationMin by remember { mutableStateOf(90) }
    var locked by remember { mutableStateOf(false) }
    val ctx = androidx.compose.ui.platform.LocalContext.current

    val modes by ModesStore.modes.collectAsStateWithLifecycle()
    fun write() = ModesStore.saveModes(ModesStore.modes.value.map { if (it.id == draft.id) draft else it }.let { if (it.any { m -> m.id == draft.id }) it else it + draft })
    // A schedule that runs less, a lighter Pomodoro on a scheduled mode, or (while it is held: locked / its window running) fewer things blocked, is a loosening: the challenge.
    fun persist(then: () -> Unit) {
        val old = ModesStore.modes.value.firstOrNull { it.id == draft.id }
        val lockedNow = Modes.holdsNow(com.husarp.lockdown.guard.TrustedTime.local(ctx), ModesStore.modes.value, ModesStore.active.value, draft.id)
        guard(old != null && Modes.modeLooser(old, draft, lockedNow)) { write(); then() }
    }
    com.husarp.lockdown.ui.UnsavedChanges(draft != (modes.firstOrNull { it.id == mode.id } ?: mode), onClose, { then -> persist(then) })

    Column(Modifier.fillMaxSize()) {
        EditorHeader(draft.name, onBack = { com.husarp.lockdown.ui.Unsaved.leave(onClose) }, action = "Save") { persist(onClose) }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Card(color = cs.surfaceContainer, shape = MaterialTheme.shapes.extraLarge) {
                Text(Modes.describe(draft) + (if (draft.pomodoro != null) " · Pomodoro" else ""), style = MaterialTheme.typography.bodyLarge)
            }
            if (!draft.builtin) OutlinedTextField(draft.name, { draft = draft.copy(name = it) }, label = { Text("Name") }, singleLine = true, modifier = Modifier.fillMaxWidth())

            SectionLabel("What it blocks")
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                CATS.forEach { (key, label) ->
                    val look = Kinds.category(key)
                    Chip(label, key in draft.categories, icon = look.icon, hue = look.hue) {
                        draft = draft.copy(categories = if (key in draft.categories) draft.categories - key else draft.categories + key)
                    }
                }
            }

            SectionLabel("Also block these")
            var pick by remember { mutableStateOf("") }
            OutlinedTextField(pick, { pick = it }, placeholder = { Text("Search apps & items") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            // With nothing typed only what this mode already blocks shows; the rest comes up as suggestions once you type.
            val shown = cfg.items.filter { if (pick.isBlank()) it.id in draft.items else it.name.contains(pick, true) || it.target.contains(pick, true) }
            // Installed apps that aren't on the blocklist can be blocked by the mode too (as a mode extra): the
            // search used to look only at the list, so an app not on it never came up.
            // (Shown once something is typed - the hundreds of installed apps would bury the list - or when already picked.)
            val installed = rememberInstalledApps()
            val listedPkgs = remember(cfg.items) { cfg.items.filter { it.type == ItemType.APP }.mapTo(HashSet()) { it.target.lowercase() } }
            fun isExtra(pkg: String) = draft.extra.any { it.kind == ItemType.APP && it.targets == listOf(pkg) }
            val apps = installed.filter { a -> a.pkg.lowercase() !in listedPkgs &&
                (if (pick.isBlank()) isExtra(a.pkg) else a.label.contains(pick, true) || a.pkg.contains(pick, true)) }
            Card(color = cs.surfaceContainerLow, padding = androidx.compose.foundation.layout.PaddingValues(vertical = 4.dp)) {
                if (pick.isBlank()) Text("Type to find apps or sites.", Modifier.padding(16.dp), color = cs.onSurfaceVariant)
                else if (shown.isEmpty() && apps.isEmpty()) Text("Nothing matches.", Modifier.padding(16.dp), color = cs.onSurfaceVariant)
                // one list in name order: an app already on your list sits in its place among the others, not apart
                val rows = (shown.map { it.name to it } + apps.map { it.label to it }).sortedBy { it.first.lowercase() }.map { it.second }
                androidx.compose.foundation.lazy.LazyColumn(Modifier.fillMaxWidth().heightIn(max = 280.dp)) {
                    items(rows, key = { if (it is com.husarp.lockdown.engine.Item) it.id else "pkg:" + (it as com.husarp.lockdown.block.InstalledApp).pkg }) { row ->
                        if (row is com.husarp.lockdown.engine.Item) ListRow(title = row.name, subtitle = row.target,
                            leading = { com.husarp.lockdown.ui.ItemIcon(row.type, row.target, row.name) }, trailing = {
                            Checkbox(checked = row.id in draft.items, onCheckedChange = { on ->
                                draft = draft.copy(items = if (on) draft.items + row.id else draft.items - row.id)
                            })
                        }) else {
                            val a = row as com.husarp.lockdown.block.InstalledApp
                            ListRow(title = a.label, subtitle = "App · not on your list", subtitlePill = true,
                                leading = { com.husarp.lockdown.ui.AppIcon(a.pkg, a.label) }, trailing = {
                                Checkbox(checked = isExtra(a.pkg), onCheckedChange = { on ->
                                    draft = draft.copy(extra = if (on) draft.extra + com.husarp.lockdown.engine.ModeExtra(a.label, ItemType.APP, listOf(a.pkg))
                                        else draft.extra.filterNot { it.kind == ItemType.APP && it.targets == listOf(a.pkg) })
                                })
                            })
                        }
                    }
                }
            }

            // Popular sites that aren't on the blocklist can be blocked by the mode too (as a mode extra with all their host
            // names): the suggestions follow what's typed; with nothing typed, the sites already picked this way show.
            val siteHits = com.husarp.lockdown.ui.rememberSiteSuggestions(pick)
            fun isSiteExtra(site: com.husarp.lockdown.engine.ListedSite) = draft.extra.any { it.kind == ItemType.SITE && it.targets == site.domains }
            val siteRows = if (pick.isBlank()) draft.extra.filter { it.kind == ItemType.SITE }.map { com.husarp.lockdown.engine.ListedSite(it.name, it.targets) }
                else siteHits.filter { s -> cfg.items.none { it.type == ItemType.SITE && s.main in it.target.split(' ') } }
            if (siteRows.isNotEmpty()) Card(color = cs.surfaceContainerLow, padding = androidx.compose.foundation.layout.PaddingValues(vertical = 4.dp)) {
                siteRows.forEach { s ->
                    ListRow(title = s.name, subtitle = s.main + (if (s.category.isNotEmpty()) " · " + s.category else ""),
                        leading = { com.husarp.lockdown.ui.ItemIcon(ItemType.SITE, s.main, s.name) }, trailing = {
                        Checkbox(checked = isSiteExtra(s), onCheckedChange = { on ->
                            draft = draft.copy(extra = if (on) draft.extra + com.husarp.lockdown.engine.ModeExtra(s.name, ItemType.SITE, s.domains)
                                else draft.extra.filterNot { it.kind == ItemType.SITE && it.targets == s.domains })
                        })
                    })
                }
            }

            Card(color = cs.surfaceContainerLow) {
                SwitchRowInline("Mute notifications", draft.mute) { draft = draft.copy(mute = it) }
                val fresh = com.husarp.lockdown.guard.rememberFresh()      // re-read on every return from Android's settings
                if (draft.mute && !remember(fresh) { com.husarp.lockdown.guard.Perms.dnd(ctx) }) {
                    Column(Modifier.padding(vertical = 4.dp)) {
                        Text("Do Not Disturb access is off, so only Lockdown's own warnings and reminders are muted, not other apps.",
                            style = MaterialTheme.typography.bodySmall, color = cs.error)
                        androidx.compose.material3.TextButton(onClick = { runCatching { ctx.startActivity(com.husarp.lockdown.guard.Perms.dndIntent().addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)) } }) {
                            Text("Allow Do Not Disturb access")
                        }
                    }
                }
                SwitchRowInline("Focus (Pomodoro)", draft.pomodoro != null) { on -> draft = draft.copy(pomodoro = if (on) Pomodoro() else null) }
                draft.pomodoro?.let { p ->
                    PomStepper("Work (min)", p.work, Modes.WORK_RANGE, 5) { draft = draft.copy(pomodoro = p.copy(work = it)) }
                    PomStepper("Break (min)", p.brk, Modes.BREAK_RANGE, 1) { draft = draft.copy(pomodoro = p.copy(brk = it)) }
                    PomStepper("Rounds", p.rounds, Modes.ROUNDS_RANGE, 1) { draft = draft.copy(pomodoro = p.copy(rounds = it)) }
                    PomStepper("Long break (min)", p.long, Modes.LONG_RANGE, 5) { draft = draft.copy(pomodoro = p.copy(long = it)) }
                }
            }

            SectionLabel("How it starts")
            com.husarp.lockdown.ui.Segmented(listOf("By hand", "On a schedule"), if (draft.schedule == null) 0 else 1) {
                draft = draft.copy(schedule = if (it == 0) null else (draft.schedule ?: ModesStore.modes.value.firstOrNull { m -> m.id == draft.id }?.schedule ?: Schedule(windows = listOf(Window((0..4).toList(), "09:00", "17:00")))))
            }
            draft.schedule?.let { sched ->
                Card(color = cs.surfaceContainerLow) {
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        ScheduleEditor(sched) { draft = draft.copy(schedule = it) }
                        Text(Modes.scheduleText(sched) ?: "Add hours for it to run.", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                    }
                }
            }
            if (draft.schedule == null) {
                Card(color = cs.surfaceContainerLow) {
                    com.husarp.lockdown.ui.Segmented(listOf("For a while", "Until a time"), if (untilMode) 1 else 0) { untilMode = it == 1 }
                    if (untilMode) TimeField("Until", untilAt, Modifier.fillMaxWidth()) { untilAt = it } else Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        androidx.compose.material3.TextButton(onClick = { durationMin = (durationMin - 15).coerceAtLeast(0) }) { Text("−15") }
                        com.husarp.lockdown.ui.Numeral(if (durationMin == 0) "∞" else "$durationMin", sizeSp = 30)
                        Text(if (durationMin == 0) "until stopped" else "min", style = MaterialTheme.typography.bodyMedium)
                        androidx.compose.material3.TextButton(onClick = { durationMin += 15 }) { Text("+15") }
                    }
                    if (untilMode || durationMin > 0) SwitchRowInline("Lock until it ends", locked) { locked = it }
                    if (untilMode) Text("A time that's already gone today means tomorrow.", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                }
                Button(onClick = {
                    val now = com.husarp.lockdown.guard.TrustedTime.local(ctx)   // trusted time: a changed clock doesn't shorten it
                    val until = if (untilMode) Modes.untilTime(now, untilAt) else if (durationMin > 0) now.plusMinutes(durationMin.toLong()) else null
                    // A loosened draft, replacing a locked mode, or overriding a scheduled one: one challenge, before anything is saved.
                    val replacing = Modes.startNeedsChallenge(now, ModesStore.modes.value, ModesStore.active.value,
                        ModesStore.modes.value.firstOrNull { it.id == draft.id }, draft, until, locked && until != null)
                    guard(replacing) {
                        write()
                        ModesStore.start(draft.id, until, locked, now)
                        onClose()
                    }
                }, modifier = Modifier.fillMaxWidth()) {
                    Icon(Icons.Filled.PlayArrow, null); Spacer(Modifier.width(8.dp)); Text("Start now")
                }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}
