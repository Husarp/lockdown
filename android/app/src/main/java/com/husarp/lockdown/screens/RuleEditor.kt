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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material.icons.outlined.Shield
import androidx.compose.material3.Checkbox
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
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.block.Apps
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.BlockMethod
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.Rules
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
                val apps = rememberInstalledApps()
                var q by remember { mutableStateOf("") }
                OutlinedTextField(q, { q = it }, placeholder = { Text("Search apps") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth().padding(top = 12.dp))
                val shown = apps.filter { it.label.contains(q, true) || it.pkg.contains(q, true) }
                LazyColumn(Modifier.fillMaxSize()) {
                    items(shown, key = { it.pkg }) { a ->
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
                        // each hostname cleaned, not just the first ("www.a.com www.b.com" used to keep the second www.)
                        val h = host.trim().lowercase().split(Regex("\\s+")).map {
                            it.removePrefix("https://").removePrefix("http://").removePrefix("www.").substringBefore("/")
                        }.filter { it.isNotEmpty() }.distinct().joinToString(" ")
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
    val ctx = LocalContext.current
    val guard = rememberGuard()
    val cs = MaterialTheme.colorScheme
    var draft by remember { mutableStateOf(cfg.items.firstOrNull { it.id == item.id } ?: item) }

    Column(Modifier.fillMaxSize()) {
        EditorHeader(title = draft.name, onBack = onClose, action = "Save") {
            val original = cfg.items.firstOrNull { it.id == draft.id }
            val loosening = original != null && AntiBypass.itemLooser(original, draft, com.husarp.lockdown.guard.TrustedTime.local(ctx))
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

            SectionLabel("In modes it counts as")
            CategoryChips(draft)

            SectionLabel("Block notice")
            NotifyChips(draft.notify) { draft = draft.copy(notify = it) }

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
 *  e.g. scheduled hours AND a daily time limit), each with its own fields. Used by items and groups alike.
 *  [group]: a group's own rules (its allowance in blocked hours can be one shared pot or each member's own). */
@Composable
fun RuleFields(rules: List<Rule>, group: Boolean = false, onChange: (List<Rule>) -> Unit) {
    fun set(type: RuleType, r: Rule?) { onChange(rules.filterNot { it.type == type } + listOfNotNull(r)) }
    fun of(type: RuleType) = rules.firstOrNull { it.type == type }
    val ctx = LocalContext.current
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        RuleTypeSection("Always blocked", of(RuleType.PERMANENT) != null,
            { on -> set(RuleType.PERMANENT, if (on) Rule(RuleType.PERMANENT) else null) }) {}
        RuleTypeSection("Scheduled hours", of(RuleType.SCHEDULED) != null,
            { on -> set(RuleType.SCHEDULED, if (on) Rule(RuleType.SCHEDULED, schedule = Schedule(SchedMode.BLOCK, listOf(Window((0..6).toList(), "22:00", "07:00")))) else null) }) {
            of(RuleType.SCHEDULED)?.let { ScheduleFields(it, group) { r -> set(RuleType.SCHEDULED, r) } }
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
            { on -> set(RuleType.TEMPORARY, if (on) Rule(RuleType.TEMPORARY, tempUntil = com.husarp.lockdown.guard.TrustedTime.local(ctx).plusHours(1).toString()) else null) }) {
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
private fun Stepper(value: Int, suffix: String, step: Int, min: Int = 0, onChange: (Int) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        IconButton(onClick = { onChange((value - step).coerceAtLeast(min)) }) { Icon(Icons.Filled.Remove, "less") }
        com.husarp.lockdown.ui.Numeral("$value", sizeSp = 36)
        Text(suffix, style = MaterialTheme.typography.titleMedium)
        IconButton(onClick = { onChange(value + step) }) { Icon(Icons.Filled.Add, "more") }
    }
}

/** A small − amount + for a row (the weekday grid, the fill amount). */
@Composable
private fun SmallStepper(text: String, onLess: () -> Unit, onMore: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onLess) { Icon(Icons.Filled.Remove, "less") }
        Text(text, style = MaterialTheme.typography.titleSmall, modifier = Modifier.width(64.dp), textAlign = TextAlign.Center)
        IconButton(onClick = onMore) { Icon(Icons.Filled.Add, "more") }
    }
}

/** "Per day" / "Per week" / "Per month" with its switch: the periods stack (any combination). The [last] one
 *  on can't be switched off (a limit with no period would enforce nothing) - [onLast] says how to remove it. */
@Composable
private fun PeriodRow(label: String, on: Boolean, last: Boolean = false, onLast: () -> Unit = {}, onToggle: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
        androidx.compose.material3.Switch(checked = on, onCheckedChange = { if (!it && last) onLast() else onToggle(it) })
    }
}

@Composable
private fun LastPeriodNote(show: Boolean, name: String) {
    if (show) Text("Turn off $name above to remove it.", style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(top = 4.dp))
}

private val DAY_NAMES = listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

@Composable
private fun TimeLimitFields(rule: Rule, onChange: (Rule) -> Unit) {
    val days = Rules.dayLimits(rule)
    var perWeekday by remember { mutableStateOf(days != null) }
    val dayOn = rule.dailyLimitMin != null || days != null
    val last = listOf(dayOn, rule.weeklyLimitMin != null, rule.monthlyLimitMin != null).count { it } == 1
    var note by remember { mutableStateOf(false) }
    Column {
        PeriodRow("Per day", dayOn, last, { note = true }) { on ->
            perWeekday = false; note = false
            onChange(rule.copy(dailyLimitMin = if (on) 60 else null, dailyLimitDays = null))
        }
        if (dayOn) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = !perWeekday, onCheckedChange = { same ->
                    // back to one amount: Monday's (or the first day with one); a bigger one than a day had asks on Save
                    if (same) onChange(rule.copy(dailyLimitMin = days?.firstOrNull { it != null } ?: rule.dailyLimitMin ?: 60, dailyLimitDays = null))
                    perWeekday = !same
                })
                Text("Same every day", style = MaterialTheme.typography.bodyMedium)
            }
            if (!perWeekday) Stepper(rule.dailyLimitMin ?: 60, "min", 15) { onChange(rule.copy(dailyLimitMin = it, dailyLimitDays = null)) }
            else WeekdayGrid(days ?: List(7) { rule.dailyLimitMin }) { grid ->
                val (one, list) = Rules.makeDayLimits(grid)
                onChange(rule.copy(dailyLimitMin = one, dailyLimitDays = list))
            }
        }
        Spacer(Modifier.height(6.dp))
        PeriodRow("Per week", rule.weeklyLimitMin != null, last, { note = true }) { on -> note = false; onChange(rule.copy(weeklyLimitMin = if (on) 600 else null)) }
        rule.weeklyLimitMin?.let { Stepper(it, "min", 60) { v -> onChange(rule.copy(weeklyLimitMin = v)) } }
        PeriodRow("Per month", rule.monthlyLimitMin != null, last, { note = true }) { on -> note = false; onChange(rule.copy(monthlyLimitMin = if (on) 2400 else null)) }
        rule.monthlyLimitMin?.let { Stepper(it, "min", 120) { v -> onChange(rule.copy(monthlyLimitMin = v)) } }
        LastPeriodNote(note && last, "Time limit")
        if (dayOn && (rule.weeklyLimitMin != null || rule.monthlyLimitMin != null))
            Text("They stack: whichever runs out first blocks.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** Mon..Sun, each with its own amount or no limit that day, plus "Fill [ ] into Mon–Fri / Sat–Sun / Every day". */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun WeekdayGrid(days: List<Int?>, onChange: (List<Int?>) -> Unit) {
    val cs = MaterialTheme.colorScheme
    var fill by remember { mutableIntStateOf(days.firstOrNull { it != null } ?: 60) }
    fun with(i: Int, v: Int?) = days.toMutableList().also { it[i] = v }
    val oneLeft = days.count { it != null } == 1   // the last day with a limit stays: "Per day" off removes them all
    Column {
        DAY_NAMES.forEachIndexed { i, name ->
            val v = days[i]
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = v != null, onCheckedChange = { on -> onChange(with(i, if (on) fill else null)) },
                    enabled = v == null || !oneLeft)
                Text(name, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
                if (v != null) SmallStepper(Rules.minutesText(v), { onChange(with(i, (v - 15).coerceAtLeast(15))) }, { onChange(with(i, v + 15)) })
                else Text("No limit", style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant, modifier = Modifier.padding(end = 16.dp))
            }
        }
        Spacer(Modifier.height(6.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Fill", style = MaterialTheme.typography.bodyMedium)
            SmallStepper(Rules.minutesText(fill), { fill = (fill - 15).coerceAtLeast(15) }, { fill += 15 })
            Text("into", style = MaterialTheme.typography.bodyMedium)
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Chip("Mon–Fri", false) { onChange(days.mapIndexed { i, d -> if (i < 5) fill else d }) }
            Chip("Sat–Sun", false) { onChange(days.mapIndexed { i, d -> if (i >= 5) fill else d }) }
            Chip("Every day", false) { onChange(List(7) { fill }) }
        }
        Text("A day counts from your reset time (Settings), so just after midnight is still the day before.",
            style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant, modifier = Modifier.padding(top = 6.dp))
    }
}

@Composable
private fun SwitchLimitFields(rule: Rule, onChange: (Rule) -> Unit) {
    val visit = rule.switchMode == SwitchCount.VISIT
    val last = listOf(rule.dailySwitchLimit, rule.weeklySwitchLimit, rule.monthlySwitchLimit).count { it != null } == 1
    var note by remember { mutableStateOf(false) }
    Column {
        PeriodRow("Per day", rule.dailySwitchLimit != null, last, { note = true }) { on -> note = false; onChange(rule.copy(dailySwitchLimit = if (on) 10 else null)) }
        rule.dailySwitchLimit?.let { Stepper(it, "opens", 1) { v -> onChange(rule.copy(dailySwitchLimit = v)) } }
        PeriodRow("Per week", rule.weeklySwitchLimit != null, last, { note = true }) { on -> note = false; onChange(rule.copy(weeklySwitchLimit = if (on) 50 else null)) }
        rule.weeklySwitchLimit?.let { Stepper(it, "opens", 5) { v -> onChange(rule.copy(weeklySwitchLimit = v)) } }
        PeriodRow("Per month", rule.monthlySwitchLimit != null, last, { note = true }) { on -> note = false; onChange(rule.copy(monthlySwitchLimit = if (on) 200 else null)) }
        rule.monthlySwitchLimit?.let { Stepper(it, "opens", 10) { v -> onChange(rule.copy(monthlySwitchLimit = v)) } }
        LastPeriodNote(note && last, "Opening limit")
        Spacer(Modifier.height(12.dp))
        Segmented(listOf("New visits", "Every open"), if (visit) 0 else 1) {
            onChange(rule.copy(switchMode = if (it == 0) SwitchCount.VISIT else SwitchCount.SWITCH))
        }
        if (visit) {
            Spacer(Modifier.height(12.dp))
            Text("An app counts each time it's opened. A site counts again after this long away:",
                style = MaterialTheme.typography.bodyMedium)
            Stepper(rule.visitGapMin ?: Rules.DEFAULT_VISIT_GAP_MIN, "min away", 1, min = 1) { onChange(rule.copy(visitGapMin = it)) }
        }
    }
}

@Composable
private fun ScheduleFields(rule: Rule, group: Boolean, onChange: (Rule) -> Unit) {
    val cs = MaterialTheme.colorScheme
    val sched = rule.schedule ?: Schedule(SchedMode.BLOCK, listOf(Window((0..6).toList(), "22:00", "07:00")))
    val windows = sched.windows.ifEmpty { listOf(Window((0..6).toList(), "22:00", "07:00")) }
    fun setWin(i: Int, w: Window?) {
        val list = windows.toMutableList().also { if (w == null) it.removeAt(i) else it[i] = w }
        onChange(rule.copy(schedule = sched.copy(windows = list)))
    }
    Column {
        Segmented(listOf("Block during", "Allow only"), if (sched.mode == SchedMode.BLOCK) 0 else 1) {
            onChange(rule.copy(schedule = sched.copy(mode = if (it == 0) SchedMode.BLOCK else SchedMode.ALLOW)))
        }
        windows.forEachIndexed { i, win ->
            Spacer(Modifier.height(12.dp))
            DaysRow(win.days) { setWin(i, win.copy(days = it)) }
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                TimeField("From", win.start, Modifier.weight(1f)) { setWin(i, win.copy(start = it)) }
                TimeField("To", win.end, Modifier.weight(1f)) { setWin(i, win.copy(end = it)) }
                if (windows.size > 1) IconButton(onClick = { setWin(i, null) }) { Icon(Icons.Filled.Close, "remove these hours") }
            }
        }
        TextButton(onClick = { onChange(rule.copy(schedule = sched.copy(windows = windows + Window((0..4).toList(), "09:00", "17:00")))) }) {
            Text("+ Add hours")
        }
        Text("Allowance: ${rule.allowanceMin ?: 0} min of grace", style = MaterialTheme.typography.bodyMedium)
        Stepper(rule.allowanceMin ?: 0, "min", 5) { onChange(rule.copy(allowanceMin = it)) }
        if (group && (rule.allowanceMin ?: 0) > 0) {
            Segmented(listOf("Shared by all", "Each member"), if (rule.allowanceShared) 0 else 1) {
                onChange(rule.copy(allowanceShared = it == 0))
            }
            Text(if (rule.allowanceShared) "One pot: time on any member uses it up for all."
                 else "Every member gets the full allowance for itself.",
                style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant, modifier = Modifier.padding(top = 6.dp))
        }
    }
}

@Composable
private fun TemporaryFields(rule: Rule, onChange: (Rule) -> Unit) {
    val now = com.husarp.lockdown.guard.TrustedTime.local(androidx.compose.ui.platform.LocalContext.current)   // the time the blocking counts in
    val until = runCatching { LocalDateTime.parse(rule.tempUntil) }.getOrNull() ?: now.plusHours(1)
    val hours = java.time.Duration.between(now, until).toHours().toInt().coerceAtLeast(1)
    Column {
        Text("Block for", style = MaterialTheme.typography.bodyMedium)
        Stepper(hours, "hours", 1) { onChange(rule.copy(tempUntil = now.plusHours(it.toLong()).toString())) }
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

/** How a site is blocked: "Cut the connection" (dns), "Go back", "Leave the browser". [locked]: flags that come from
 *  elsewhere (a group's way, for a member's own), shown chosen and greyed. Returns the new set of [selected]. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SiteWayChips(selected: Set<String>, locked: Set<String> = emptySet(), onToggle: (Set<String>) -> Unit) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        listOf("dns" to "Cut the connection", "back" to "Go back", "close" to "Leave the browser").forEach { (key, label) ->
            val fixed = key in locked
            Chip(label, key in selected || fixed, modifier = if (fixed) Modifier.alpha(0.5f) else Modifier) {
                if (!fixed) onToggle(if (key in selected) selected - key else selected + key)
            }
        }
    }
}

@Composable
private fun BlockTypeChips(item: Item, onChange: (String) -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val cs = MaterialTheme.colorScheme
    if (item.type == ItemType.APP) {
        Text("A notice covers it and it goes to the home screen. Videos playing in it are paused.",
            style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
        return
    }
    SiteWayChips(BlockMethod.flags(item.blockType)) { f -> if (f.isNotEmpty()) onChange(f.joinToString(",")) }
    val note = buildString {
        append("Go back: the browser goes back a page. Leave the browser: back to the home screen. ")
        append("Cut the connection: the site can't load at all")
        append(if (cfg.settings.siteFilterOn) "." else " - this needs the site filter (Settings > Site filter), which is off; until then the page is covered instead.")
        // a group that chose how its member sites are blocked uses its own way for its blocks (PC 0.84.11)
        val chose = cfg.groups.filter { item.id in it.memberIds && !it.disabled && it.siteBlock != null }.map { it.name }
        if (chose.isNotEmpty()) append(" When ${chose.joinToString(" or ")} blocks it, that group's way applies.")
    }
    Text(note, style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
}

/** A time limit in words: "1h 00m a day", "3h 00m Mon–Fri, 2h 00m Sat–Sun", "· 10h 00m a week". */
fun timeLimitText(r: Rule): String = listOfNotNull(
    Rules.dayLimits(r)?.let { Rules.dayLimitsText(it) } ?: r.dailyLimitMin?.let { "${Rules.minutesText(it)} a day" },
    r.weeklyLimitMin?.let { "${Rules.minutesText(it)} a week" },
    r.monthlyLimitMin?.let { "${Rules.minutesText(it)} a month" },
).joinToString(" · ").ifEmpty { "no limit set" }

/** An opening limit in words: "10 opens a day · 50 a week". */
fun openingLimitText(r: Rule): String =
    listOfNotNull(r.dailySwitchLimit?.let { it to "a day" }, r.weeklySwitchLimit?.let { it to "a week" }, r.monthlySwitchLimit?.let { it to "a month" })
        .mapIndexed { i, (n, p) -> if (i == 0) "$n opens $p" else "$n $p" }.joinToString(" · ").ifEmpty { "no limit set" }

/** A plain-English summary of the item + its rule, updated live. */
fun sentence(item: Item): String {
    val name = item.name
    val r = item.rules.firstOrNull() ?: return "$name isn't limited yet — pick a rule below."
    return when (r.type) {
        RuleType.PERMANENT -> "Block $name completely."
        RuleType.TEMPORARY -> "Block $name for a while."
        RuleType.TIME_LIMIT -> "Block $name after ${timeLimitText(r)}."
        RuleType.SWITCH_LIMIT -> "Block $name after ${openingLimitText(r)}."
        RuleType.SCHEDULED -> {
            val ws = r.schedule?.windows.orEmpty()
            val verb = if (r.schedule?.mode == SchedMode.BLOCK) "Block" else "Allow"
            "$verb $name ${ws.joinToString(", ") { "${it.start}–${it.end}" }}."
        }
    }
}

/** The phone's launchable apps, read off the main thread (reading every app's name took up to a second and froze
 *  the screen when an editor opened); empty until ready. */
@Composable
fun rememberInstalledApps(): List<com.husarp.lockdown.block.InstalledApp> {
    val ctx = LocalContext.current
    val apps by androidx.compose.runtime.produceState(emptyList<com.husarp.lockdown.block.InstalledApp>()) {
        value = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) { runCatching { Apps.launchable(ctx) }.getOrDefault(emptyList()) }
    }
    return apps
}
