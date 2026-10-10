package com.husarp.lockdown.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.unit.dp
import com.husarp.lockdown.block.InstalledApp
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.engine.BlockMethod
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.ui.AppIcon
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.EditorHeader
import com.husarp.lockdown.ui.GroupLook
import com.husarp.lockdown.ui.IconTile
import com.husarp.lockdown.ui.ItemIcon
import com.husarp.lockdown.ui.Kinds
import com.husarp.lockdown.ui.ListRow
import com.husarp.lockdown.ui.Pill
import com.husarp.lockdown.ui.PillSpec
import com.husarp.lockdown.ui.SectionLabel
import com.husarp.lockdown.ui.theme.Hue
import com.husarp.lockdown.ui.theme.tone

/*
 * The group editor's member pages (it keeps the one draft, saved with its Save): a member's own page (what applies
 * to it, its extra limits on top of the group's, how it's blocked here, remove it), Add members, and the group's
 * icon and colour.
 */

/** "Social: 1h a day together, blocked 22:00–07:00. 6 members." */
internal fun groupSentence(g: Group, members: Int): String {
    val bits = g.rules.map { r ->
        when (r.type) {
            RuleType.PERMANENT -> "always blocked"
            RuleType.TIME_LIMIT, RuleType.SWITCH_LIMIT -> Kinds.ruleText(r) + " together"
            RuleType.SCHEDULED -> Kinds.ruleText(r).let { if (it.startsWith("Allowed")) "allowed only " + it.removePrefix("Allowed ") else "blocked $it" }
            RuleType.TEMPORARY -> "blocked " + Kinds.ruleText(r)
        }
    }
    val name = g.name.ifBlank { "This group" }
    val rules = if (bits.isEmpty()) "no shared rules yet" else bits.joinToString(", ")
    return "$name: $rules. $members member${if (members == 1) "" else "s"}."
}

/** One member in its group: what applies to it, its extra limits on top of the group's, how it's blocked here, and
 *  Remove (removing loosens: the group's Save asks for the challenge). Edits go into the group's draft. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun GroupMemberPage(draft: Group, m: Item, onChange: (Group) -> Unit, onRemove: () -> Unit, onBack: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    Column(Modifier.fillMaxSize()) {
        EditorHeader("${m.name} in ${draft.name.ifBlank { "this group" }}", onBack = onBack)
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                ItemIcon(m.type, m.target, m.name, 48.dp)
                Column(Modifier.weight(1f)) {
                    Text(m.name, style = MaterialTheme.typography.titleMedium)
                    Text(if (m.type == ItemType.APP) "App" else m.target, style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                }
            }

            SectionLabel("What applies")
            Card(color = cs.surfaceContainerLow) {
                val group = Kinds.rulePills(draft.rules)
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Pill(Kinds.groupPill(draft, draft.name.ifBlank { "Group" }))
                    Text(if (group.isEmpty()) "no shared rules yet" else "every member", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                }
                if (group.isNotEmpty()) PillFlow(group)
                if (m.rules.isNotEmpty()) {
                    Spacer(Modifier.height(10.dp))
                    Text("Its own rules (outside the group)", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                    PillFlow(m.rules.map { Kinds.rulePill(it, outlined = true) })
                }
            }

            // A member's own limits come on top of the group's, never instead (like the PC since 0.84.3).
            SectionLabel("Extra limits on top of the group's")
            Text("The group's rules still apply, and ${m.name}'s time still fills them. An extra limit only makes ${m.name} " +
                "stricter — e.g. the group 2 h a day, and ${m.name} only 1 h of it.",
                style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            val extras = draft.overrides[m.id] ?: emptyMap()
            RuleFields(extras.values.toList()) { rules ->
                val map = rules.associateBy { it.type.name }
                onChange(draft.copy(overrides = if (map.isEmpty()) draft.overrides - m.id else draft.overrides + (m.id to map)))
            }

            if (m.type == ItemType.SITE) {
                // the group's way shown chosen and greyed: a member can only add to it
                val base = BlockMethod.strength(BlockMethod.inGroup(m, draft, own = false))
                val own = BlockMethod.flags(draft.memberBlocks[m.id]).filterTo(HashSet()) { draft.memberBlocks[m.id] != null && it !in base }
                SectionLabel("How ${m.name} is blocked in this group")
                SiteWayChips(own, locked = base) { f ->
                    val add = f - base
                    onChange(draft.copy(memberBlocks = if (add.isEmpty()) draft.memberBlocks - m.id else draft.memberBlocks + (m.id to add.joinToString(","))))
                }
            } else {
                // an app: the group's way, or its own instead (even a lighter one - e.g. only greyed while the group
                // closes; the group's Save asks for the challenge for that)
                val own = draft.memberBlocks[m.id]
                val groupGrey = BlockMethod.greyOnly(BlockMethod.inGroup(m, draft, own = false))
                val ownGrey = own != null && BlockMethod.greyOnly(own)
                SectionLabel("How ${m.name} is blocked in this group")
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    val look = Kinds.way(if (groupGrey) "grey" else "close", site = false)
                    Chip("Group's way (${if (groupGrey) "grey" else "close"})", own == null, icon = look.icon, hue = look.hue) {
                        onChange(draft.copy(memberBlocks = draft.memberBlocks - m.id))
                    }
                    val close = Kinds.way("close", site = false)
                    Chip("Close it", own != null && !ownGrey, icon = close.icon, hue = close.hue) {
                        onChange(draft.copy(memberBlocks = draft.memberBlocks + (m.id to "close")))
                    }
                    Chip("Grey it", ownGrey, icon = Kinds.GREY.icon, hue = Kinds.GREY.hue) {
                        onChange(draft.copy(memberBlocks = draft.memberBlocks + (m.id to BlockMethod.GREY)))
                    }
                }
                Text(if (own == null) "${m.name} is blocked the way the group does it." else appWayText(ownGrey) + " Only in this group.",
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                if (own != null) Text("A lighter way than it has now asks for the challenge when you save the group.",
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }

            TextButton(onClick = onRemove, colors = ButtonDefaults.textButtonColors(contentColor = cs.error)) {
                Icon(Icons.Filled.Delete, null); Spacer(Modifier.size(6.dp)); Text("Remove from group")
            }
            Text("Removing a member, or easing its extra limits, asks for the challenge when you save the group.",
                style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            Spacer(Modifier.height(24.dp))
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun PillFlow(pills: List<PillSpec>) {
    FlowRow(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        pills.forEach { Pill(it) }
    }
}

/**
 * Add members: search (focused at once) for apps and sites already on your list and installed apps not on it yet
 * (added to the list on the group's Save), each with its icon and a tick; with nothing typed, the current members
 * ticked. A new site is added below, as before.
 */
@Composable
internal fun AddMembersPage(draft: Group, cfg: Config, newApps: List<Item>, onMember: (String, Boolean) -> Unit,
                            onNewApp: (Item) -> Unit, onNewSite: (Item) -> Unit, onDone: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    var pick by remember { mutableStateOf("") }
    var newSite by remember { mutableStateOf("") }
    val installed = rememberInstalledApps()
    val listedPkgs = remember(cfg.items) { cfg.items.filter { it.type == ItemType.APP }.mapTo(HashSet()) { it.target.lowercase() } }
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }

    Column(Modifier.fillMaxSize()) {
        EditorHeader("Add members", onBack = onDone, action = "Done", onAction = onDone)
        Column(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedTextField(pick, { pick = it }, placeholder = { Text("Search apps & sites") }, singleLine = true,
                modifier = Modifier.fillMaxWidth().focusRequester(focus))
            // Candidates: apps + items not already blocked-forever + current members. The thousands of permanently
            // blocked imported sites are hidden here — grouping something already blocked forever does nothing.
            // Then the installed apps not on the list yet. With nothing typed only the current members show.
            val candidates = (cfg.items + newApps).filter {
                if (pick.isBlank()) it.id in draft.memberIds
                else (it.type == ItemType.APP || it.rules.none { r -> r.type == RuleType.PERMANENT } || it.id in draft.memberIds) &&
                    (it.name.contains(pick, true) || it.target.contains(pick, true))
            }
            val pending = newApps.mapTo(HashSet()) { it.target.lowercase() }
            val apps = if (pick.isBlank()) emptyList() else installed.filter { a ->
                a.pkg.lowercase() !in listedPkgs && a.pkg.lowercase() !in pending &&
                    (a.label.contains(pick, true) || a.pkg.contains(pick, true))
            }
            Card(color = cs.surfaceContainerLow, padding = androidx.compose.foundation.layout.PaddingValues(vertical = 4.dp), modifier = Modifier.weight(1f, fill = false)) {
                if (pick.isBlank() && candidates.isEmpty()) Text("Type to find apps or sites.", Modifier.padding(16.dp), color = cs.onSurfaceVariant)
                else if (candidates.isEmpty() && apps.isEmpty()) Text("Nothing matches. Add a site below.", Modifier.padding(16.dp), color = cs.onSurfaceVariant)
                // one list in name order: an app already on your list sits in its place among the others, not apart
                val rows = (candidates.map { it.name to it } + apps.map { it.label to it }).sortedBy { it.first.lowercase() }.map { it.second }
                LazyColumn(Modifier.fillMaxWidth()) {
                    items(rows, key = { if (it is Item) it.id else "pkg:" + (it as InstalledApp).pkg }) { row ->
                        if (row is Item) {
                            val on = row.id in draft.memberIds
                            ListRow(title = row.name,
                                subtitle = if (row.type == ItemType.APP) (if (row.rules.isEmpty()) "App" else "App · has its own rule") else row.target,
                                leading = { ItemIcon(row.type, row.target, row.name) },
                                trailing = { Checkbox(checked = on, onCheckedChange = { onMember(row.id, it) }) },
                                onClick = { onMember(row.id, !on) })
                        } else {
                            val a = row as InstalledApp
                            fun add() = onNewApp(Item(java.util.UUID.randomUUID().toString().take(8), a.label, a.pkg, ItemType.APP))  // no own rule: the group's govern it
                            ListRow(title = a.label, subtitle = "App · not on your list yet", subtitlePill = true,
                                leading = { AppIcon(a.pkg, a.label) },
                                trailing = { Checkbox(checked = false, onCheckedChange = { if (it) add() }) },
                                onClick = { add() })
                        }
                    }
                }
            }
            Row(Modifier.fillMaxWidth().padding(bottom = 16.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(newSite, { newSite = it.filterNot(Char::isWhitespace) }, placeholder = { Text("Add a site (e.g. reddit.com)") }, singleLine = true, modifier = Modifier.weight(1f))
                TextButton(onClick = {
                    val h = com.husarp.lockdown.engine.Sites.clean(newSite)
                    if (h.isNotEmpty()) {
                        onNewSite(Item(java.util.UUID.randomUUID().toString().take(8), h, h, ItemType.SITE))   // no own rule: the group's shared rule governs it
                        newSite = ""
                    }
                }) { Text("Add") }
            }
        }
    }
}

/** The group's icon and colour: 24 icons, 10 colours, or Auto (from its name and id). Only the draft changes - saved
 *  with the group, always free (looks block nothing). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun IconColourDialog(g: Group, onPick: (icon: String?, color: String?) -> Unit, onDismiss: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    var icon by remember { mutableStateOf(g.icon?.takeIf { it in GroupLook.KEYS }) }
    var color by remember { mutableStateOf(g.color?.takeIf { c -> Hue.entries.any { it.name == c } }) }
    val shown = g.copy(icon = icon, color = color)
    val hue = GroupLook.hue(shown)
    val t = tone(hue)
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Icon and colour") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    IconTile(GroupLook.icon(shown), hue, 56.dp)
                    Column(Modifier.weight(1f)) {
                        Text(g.name.ifBlank { "New group" }, style = MaterialTheme.typography.titleMedium)
                        Text(if (icon == null && color == null) "Auto: from the name" else "Picked", style = MaterialTheme.typography.bodySmall,
                            color = cs.onSurfaceVariant)
                    }
                    Chip("Auto", icon == null && color == null) { icon = null; color = null }
                }
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    val current = GroupLook.iconKey(shown)
                    GroupLook.KEYS.forEach { key ->
                        val sel = key == current
                        Box(Modifier.size(44.dp).background(if (sel) t.container else cs.surfaceContainerHigh, RoundedCornerShape(12.dp))
                            .then(if (sel) Modifier.border(2.dp, t.accent, RoundedCornerShape(12.dp)) else Modifier)
                            .clickable { icon = key }, contentAlignment = Alignment.Center) {
                            Icon(GroupLook.vector(key), key, Modifier.size(24.dp), tint = if (sel) t.accent else cs.onSurfaceVariant)
                        }
                    }
                }
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Hue.entries.forEach { h ->
                        val sel = h == hue
                        val ht = tone(h)
                        Box(Modifier.size(32.dp).background(ht.accent, CircleShape).clickable { color = h.name }, contentAlignment = Alignment.Center) {
                            if (sel) Icon(Icons.Filled.Check, h.name, Modifier.size(18.dp), tint = ht.container)
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { onPick(icon, color); onDismiss() }) { Text("Done") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
