package com.husarp.lockdown.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material.icons.outlined.Shield
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.block.Apps
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.SchedMode
import com.husarp.lockdown.engine.Schedule
import com.husarp.lockdown.engine.SwitchCount
import com.husarp.lockdown.engine.Window
import com.husarp.lockdown.guard.rememberGuard
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.EditorHeader
import com.husarp.lockdown.ui.Segmented
import com.husarp.lockdown.ui.SectionLabel
import java.time.LocalDateTime
import java.util.UUID

/** Choose what to block: an installed app, or a site by hostname. Creates the item, then opens its editor. */
@Composable
fun AddItemSheet(onDone: () -> Unit, onEdit: (Item) -> Unit, onEditGroup: (Group) -> Unit) {
    val cs = MaterialTheme.colorScheme
    val ctx = LocalContext.current
    var kind by remember { mutableStateOf<ItemType?>(null) }

    Column(Modifier.fillMaxSize().padding(16.dp)) {
        EditorHeader(title = "Block something", onBack = onDone)
        when (kind) {
            null -> Row(Modifier.padding(top = 12.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedButton(onClick = { kind = ItemType.APP }) { Text("An app") }
                OutlinedButton(onClick = { kind = ItemType.SITE }) { Text("A website") }
                OutlinedButton(onClick = {
                    val g = Group(UUID.randomUUID().toString().take(8), "New group")
                    Store.update { it.copy(groups = it.groups + g) }
                    onEditGroup(g)
                }) { Text("A group") }
            }
            ItemType.APP -> {
                val apps = remember { Apps.launchable(ctx) }
                LazyColumn(Modifier.fillMaxSize()) {
                    items(apps) { a ->
                        com.husarp.lockdown.ui.ListRow(
                            title = a.label, subtitle = a.pkg,
                            leading = { com.husarp.lockdown.ui.AppIcon(a.pkg, a.label) },
                            onClick = {
                                val item = Item(UUID.randomUUID().toString().take(8), a.label, a.pkg, ItemType.APP,
                                    rules = listOf(Rule(RuleType.PERMANENT)))
                                Store.update { it.copy(items = it.items + item) }
                                onEdit(item)
                            },
                        )
                    }
                }
            }
            ItemType.SITE -> {
                var name by remember { mutableStateOf("") }
                var host by remember { mutableStateOf("") }
                Column(Modifier.padding(top = 12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    OutlinedTextField(name, { name = it }, label = { Text("Name") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(host, { host = it }, label = { Text("Hostname(s), space-separated") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    TextButton(onClick = {
                        val h = host.trim().lowercase().removePrefix("https://").removePrefix("http://").removePrefix("www.")
                        if (h.isNotEmpty()) {
                            val item = Item(UUID.randomUUID().toString().take(8), name.ifBlank { h.substringBefore(" ") }, h, ItemType.SITE,
                                rules = listOf(Rule(RuleType.PERMANENT)))
                            Store.update { it.copy(items = it.items + item) }
                            onEdit(item)
                        }
                    }) { Text("Add") }
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun RuleEditor(item: Item, onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val guard = rememberGuard()
    val cs = MaterialTheme.colorScheme
    var draft by remember { mutableStateOf(cfg.items.firstOrNull { it.id == item.id } ?: item) }

    Column(Modifier.fillMaxSize()) {
        EditorHeader(title = draft.name, onBack = onClose, action = "Save") {
            val original = cfg.items.firstOrNull { it.id == draft.id }
            val loosening = original != null && AntiBypass.itemLooser(original, draft, LocalDateTime.now())
            guard(loosening) {
                Store.update { c -> c.copy(items = c.items.map { if (it.id == draft.id) draft else it }) }
                onClose()
            }
        }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            // live sentence summary
            Card(color = cs.surfaceContainer, shape = MaterialTheme.shapes.extraLarge) {
                Text(sentence(draft), style = MaterialTheme.typography.bodyLarge)
            }

            SectionLabel("Blocking rules")
            RuleFields(draft.rules) { draft = draft.copy(rules = it) }

            SectionLabel(if (draft.type == ItemType.APP) "How it's blocked" else "How the site is blocked")
            BlockTypeChips(draft) { draft = draft.copy(blockType = it) }

            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                com.husarp.lockdown.ui.SwitchRowInline("Paused", draft.disabled) { draft = draft.copy(disabled = it) }
            }

            TextButton(onClick = {
                guard(true) {
                    Store.update { c -> c.copy(items = c.items.filterNot { it.id == draft.id }) }
                    onClose()
                }
            }) { Icon(Icons.Filled.Delete, null); Spacer(Modifier.size(6.dp)); Text("Remove this block") }

            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Icon(Icons.Outlined.Shield, null, tint = cs.onSurfaceVariant, modifier = Modifier.size(18.dp))
                Text("Tightening saves instantly. Loosening asks for a quick challenge first.",
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

/** The shared multi-rule editor: toggle ANY combination of rule types (an item or a group can have several -
 *  e.g. scheduled hours AND a daily time limit), each with its own fields. Used by items and groups alike. */
@Composable
fun RuleFields(rules: List<Rule>, onChange: (List<Rule>) -> Unit) {
    fun set(type: RuleType, r: Rule?) { onChange(rules.filterNot { it.type == type } + listOfNotNull(r)) }
    fun of(type: RuleType) = rules.firstOrNull { it.type == type }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        RuleTypeSection("Always blocked", of(RuleType.PERMANENT) != null,
            { on -> set(RuleType.PERMANENT, if (on) Rule(RuleType.PERMANENT) else null) }) {}
        RuleTypeSection("Scheduled hours", of(RuleType.SCHEDULED) != null,
            { on -> set(RuleType.SCHEDULED, if (on) Rule(RuleType.SCHEDULED, schedule = Schedule(SchedMode.BLOCK, listOf(Window((0..6).toList(), "22:00", "07:00")))) else null) }) {
            of(RuleType.SCHEDULED)?.let { ScheduleFields(it) { r -> set(RuleType.SCHEDULED, r) } }
        }
        RuleTypeSection("Time limit", of(RuleType.TIME_LIMIT) != null,
            { on -> set(RuleType.TIME_LIMIT, if (on) Rule(RuleType.TIME_LIMIT, dailyLimitMin = 60) else null) }) {
            of(RuleType.TIME_LIMIT)?.let { TimeLimitFields(it) { r -> set(RuleType.TIME_LIMIT, r) } }
        }
        RuleTypeSection("Opening limit", of(RuleType.SWITCH_LIMIT) != null,
            { on -> set(RuleType.SWITCH_LIMIT, if (on) Rule(RuleType.SWITCH_LIMIT, dailySwitchLimit = 10) else null) }) {
            of(RuleType.SWITCH_LIMIT)?.let { SwitchLimitFields(it) { r -> set(RuleType.SWITCH_LIMIT, r) } }
        }
        RuleTypeSection("Temporary", of(RuleType.TEMPORARY) != null,
            { on -> set(RuleType.TEMPORARY, if (on) Rule(RuleType.TEMPORARY, tempUntil = LocalDateTime.now().plusHours(1).toString()) else null) }) {
            of(RuleType.TEMPORARY)?.let { TemporaryFields(it) { r -> set(RuleType.TEMPORARY, r) } }
        }
    }
}

@Composable
private fun RuleTypeSection(name: String, on: Boolean, onToggle: (Boolean) -> Unit, fields: @Composable () -> Unit) {
    Card(color = MaterialTheme.colorScheme.surfaceContainerLow) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(name, style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
            androidx.compose.material3.Switch(checked = on, onCheckedChange = onToggle)
        }
        if (on) { Spacer(Modifier.height(10.dp)); fields() }
    }
}

@Composable
private fun Stepper(value: Int, suffix: String, step: Int, onChange: (Int) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        IconButton(onClick = { onChange((value - step).coerceAtLeast(0)) }) { Icon(Icons.Filled.Remove, "less") }
        com.husarp.lockdown.ui.Numeral("$value", sizeSp = 36)
        Text(suffix, style = MaterialTheme.typography.titleMedium)
        IconButton(onClick = { onChange(value + step) }) { Icon(Icons.Filled.Add, "more") }
    }
}

@Composable
private fun TimeLimitFields(rule: Rule, onChange: (Rule) -> Unit) {
    val period = when { rule.weeklyLimitMin != null -> 1; rule.monthlyLimitMin != null -> 2; else -> 0 }
    val value = rule.dailyLimitMin ?: rule.weeklyLimitMin ?: rule.monthlyLimitMin ?: 60
    Column {
        Stepper(value, "min", 15) { v -> onChange(withPeriod(RuleType.TIME_LIMIT, period, v)) }
        Spacer(Modifier.height(12.dp))
        Segmented(listOf("Day", "Week", "Month"), period) { onChange(withPeriod(RuleType.TIME_LIMIT, it, value)) }
    }
}

@Composable
private fun SwitchLimitFields(rule: Rule, onChange: (Rule) -> Unit) {
    val period = when { rule.weeklySwitchLimit != null -> 1; rule.monthlySwitchLimit != null -> 2; else -> 0 }
    val value = rule.dailySwitchLimit ?: rule.weeklySwitchLimit ?: rule.monthlySwitchLimit ?: 10
    val visit = rule.switchMode == SwitchCount.VISIT
    Column {
        Stepper(value, "opens", 1) { v -> onChange(withSwitchPeriod(period, v, rule.switchMode)) }
        Spacer(Modifier.height(12.dp))
        Segmented(listOf("Day", "Week", "Month"), period) { onChange(withSwitchPeriod(it, value, rule.switchMode)) }
        Spacer(Modifier.height(12.dp))
        Segmented(listOf("New visits", "Every open"), if (visit) 0 else 1) {
            onChange(withSwitchPeriod(period, value, if (it == 0) SwitchCount.VISIT else SwitchCount.SWITCH))
        }
    }
}

@Composable
private fun ScheduleFields(rule: Rule, onChange: (Rule) -> Unit) {
    val sched = rule.schedule ?: Schedule(SchedMode.BLOCK, listOf(Window((0..6).toList(), "22:00", "07:00")))
    val win = sched.windows.firstOrNull() ?: Window((0..6).toList(), "22:00", "07:00")
    Column {
        Segmented(listOf("Block during", "Allow only"), if (sched.mode == SchedMode.BLOCK) 0 else 1) {
            onChange(rule.copy(schedule = sched.copy(mode = if (it == 0) SchedMode.BLOCK else SchedMode.ALLOW)))
        }
        Spacer(Modifier.height(12.dp))
        DaysRow(win.days) { onChange(rule.copy(schedule = sched.copy(windows = listOf(win.copy(days = it))))) }
        Spacer(Modifier.height(12.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedTextField(win.start, { onChange(rule.copy(schedule = sched.copy(windows = listOf(win.copy(start = it))))) },
                label = { Text("From") }, singleLine = true, modifier = Modifier.weight(1f))
            OutlinedTextField(win.end, { onChange(rule.copy(schedule = sched.copy(windows = listOf(win.copy(end = it))))) },
                label = { Text("To") }, singleLine = true, modifier = Modifier.weight(1f))
        }
        Spacer(Modifier.height(12.dp))
        Text("Allowance: ${rule.allowanceMin ?: 0} min of grace", style = MaterialTheme.typography.bodyMedium)
        Stepper(rule.allowanceMin ?: 0, "min", 5) { onChange(rule.copy(allowanceMin = it)) }
    }
}

@Composable
private fun TemporaryFields(rule: Rule, onChange: (Rule) -> Unit) {
    val until = runCatching { LocalDateTime.parse(rule.tempUntil) }.getOrNull() ?: LocalDateTime.now().plusHours(1)
    val hours = java.time.Duration.between(LocalDateTime.now(), until).toHours().toInt().coerceAtLeast(1)
    Column {
        Text("Block for", style = MaterialTheme.typography.bodyMedium)
        Stepper(hours, "hours", 1) { onChange(rule.copy(tempUntil = LocalDateTime.now().plusHours(it.toLong()).toString())) }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun DaysRow(days: List<Int>, onChange: (List<Int>) -> Unit) {
    val labels = listOf("M", "T", "W", "T", "F", "S", "S")
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        labels.forEachIndexed { i, l ->
            val on = i in days
            val cs = MaterialTheme.colorScheme
            Surface(
                shape = CircleShape,
                color = if (on) cs.primaryContainer else cs.surface,
                border = androidx.compose.foundation.BorderStroke(1.dp, if (on) cs.primaryContainer else cs.outlineVariant),
                modifier = Modifier.size(38.dp).clickable { onChange(if (on) days - i else (days + i).sorted()) },
            ) {
                Box(contentAlignment = Alignment.Center) { Text(l, color = if (on) cs.onPrimaryContainer else cs.onSurface) }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun BlockTypeChips(item: Item, onChange: (String) -> Unit) {
    val flags = item.blockType.split(",").map { it.trim() }.filter { it.isNotEmpty() }.toMutableSet()
    val options = if (item.type == ItemType.APP)
        listOf("close" to "Close", "background" to "Kill background", "minimize" to "Minimise", "internet" to "Cut internet")
    else listOf("dns" to "Can't load", "close" to "Close tab", "back" to "Go back")
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        options.forEach { (key, label) ->
            Chip(label, key in flags) {
                val f = flags.toMutableSet(); if (key in f) f.remove(key) else f.add(key)
                onChange(f.joinToString(","))
            }
        }
    }
}

private fun withPeriod(type: RuleType, period: Int, value: Int) = Rule(
    type,
    dailyLimitMin = if (period == 0) value else null,
    weeklyLimitMin = if (period == 1) value else null,
    monthlyLimitMin = if (period == 2) value else null,
)

private fun withSwitchPeriod(period: Int, value: Int, mode: SwitchCount) = Rule(
    RuleType.SWITCH_LIMIT,
    dailySwitchLimit = if (period == 0) value else null,
    weeklySwitchLimit = if (period == 1) value else null,
    monthlySwitchLimit = if (period == 2) value else null,
    switchMode = mode,
)

/** A plain-English summary of the item + its rule, updated live. */
fun sentence(item: Item): String {
    val name = item.name
    val r = item.rules.firstOrNull() ?: return "$name isn't limited yet — pick a rule below."
    return when (r.type) {
        RuleType.PERMANENT -> "Block $name completely."
        RuleType.TEMPORARY -> "Block $name for a while."
        RuleType.TIME_LIMIT -> {
            val (v, p) = when { r.weeklyLimitMin != null -> r.weeklyLimitMin to "a week"; r.monthlyLimitMin != null -> r.monthlyLimitMin to "a month"; else -> (r.dailyLimitMin ?: 0) to "a day" }
            "Block $name after ${v} min $p."
        }
        RuleType.SWITCH_LIMIT -> {
            val v = r.dailySwitchLimit ?: r.weeklySwitchLimit ?: r.monthlySwitchLimit ?: 0
            "Block $name after $v opens."
        }
        RuleType.SCHEDULED -> {
            val w = r.schedule?.windows?.firstOrNull()
            val verb = if (r.schedule?.mode == SchedMode.BLOCK) "Block" else "Allow"
            "$verb $name ${w?.start ?: ""}–${w?.end ?: ""}."
        }
    }
}
