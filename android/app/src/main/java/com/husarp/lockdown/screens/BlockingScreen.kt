package com.husarp.lockdown.screens

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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.outlined.Key
import androidx.compose.material.icons.outlined.Shield
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.data.Live
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Modes
import com.husarp.lockdown.ui.Avatar
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.ListRow
import com.husarp.lockdown.ui.SectionLabel
import com.husarp.lockdown.ui.Status
import com.husarp.lockdown.ui.StatusChip
import java.time.LocalDateTime

private sealed interface BlockPage {
    data object List : BlockPage
    data object Add : BlockPage
    data class Edit(val item: Item) : BlockPage
    data class EditGroup(val group: Group) : BlockPage
    data object Protection : BlockPage
    data object Keywords : BlockPage
}

@Composable
fun BlockingScreen(onOpenNetLog: () -> Unit) {
    var page by remember { mutableStateOf<BlockPage>(BlockPage.List) }
    when (val p = page) {
        BlockPage.List -> BlockList(
            onAdd = { page = BlockPage.Add },
            onEdit = { page = BlockPage.Edit(it) },
            onEditGroup = { page = BlockPage.EditGroup(it) },
            onProtection = { page = BlockPage.Protection },
            onKeywords = { page = BlockPage.Keywords },
        )
        BlockPage.Add -> AddItemSheet(onDone = { page = BlockPage.List }, onEdit = { page = BlockPage.Edit(it) }, onEditGroup = { page = BlockPage.EditGroup(it) })
        is BlockPage.Edit -> RuleEditor(p.item, onClose = { page = BlockPage.List })
        is BlockPage.EditGroup -> GroupEditor(p.group, onClose = { page = BlockPage.List })
        BlockPage.Protection -> ProtectionEditor(onClose = { page = BlockPage.List })
        BlockPage.Keywords -> KeywordsEditor(onClose = { page = BlockPage.List })
    }
}

@Composable
private fun BlockList(
    onAdd: () -> Unit,
    onEdit: (Item) -> Unit,
    onEditGroup: (Group) -> Unit,
    onProtection: () -> Unit,
    onKeywords: () -> Unit,
) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val cs = MaterialTheme.colorScheme
    var query by remember { mutableStateOf("") }
    val now = LocalDateTime.now()

    val matches = cfg.items.filter { it.name.contains(query, true) || it.target.contains(query, true) }
    // The blocked list can be thousands long, so it stays collapsed and paginates: 20 rows at a time, loading 20
    // more as you scroll. Status is computed per VISIBLE row only. Searching opens it automatically.
    val listState = rememberLazyListState()
    var expanded by remember { mutableStateOf(false) }
    var shown by remember { mutableIntStateOf(20) }
    val open = expanded || query.isNotBlank()
    LaunchedEffect(query) { shown = 20 }
    LaunchedEffect(listState, matches.size, open) {
        snapshotFlow { val li = listState.layoutInfo; (li.visibleItemsInfo.lastOrNull()?.index ?: 0) to li.totalItemsCount }
            .collect { (last, total) -> if (open && shown < matches.size && last >= total - 3) shown = (shown + 20).coerceAtMost(matches.size) }
    }

    Scaffold(
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = onAdd, icon = { Icon(Icons.Filled.Add, null) }, text = { Text("Block") },
                containerColor = cs.primaryContainer, contentColor = cs.onPrimaryContainer,
            )
        },
        containerColor = cs.surface,
    ) { pad ->
        LazyColumn(state = listState, modifier = Modifier.fillMaxSize().padding(pad), contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            item {
                OutlinedTextField(
                    value = query, onValueChange = { query = it },
                    leadingIcon = { Icon(Icons.Filled.Search, null) },
                    placeholder = { Text("Search ${cfg.items.size} apps & sites") },
                    singleLine = true, modifier = Modifier.fillMaxWidth(),
                    shape = MaterialTheme.shapes.extraLarge,
                )
            }
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Chip("Protection ${cfg.protection.enabled.size}", selected = cfg.protection.enabled.isNotEmpty(), icon = Icons.Outlined.Shield) { onProtection() }
                    Chip("Keywords", selected = cfg.keywords.enabled, icon = Icons.Outlined.Key) { onKeywords() }
                }
            }
            if (cfg.groups.isNotEmpty()) {
                item { SectionLabel("Groups") }
                items(cfg.groups) { g ->
                    Card(color = cs.surfaceContainerHigh, padding = androidx.compose.foundation.layout.PaddingValues(0.dp), onClick = { onEditGroup(g) }) {
                        ListRow(
                            title = g.name,
                            subtitle = "${g.memberIds.size} items · ${g.rules.size} shared rule(s)",
                            leading = { Avatar(g.name, bg = cs.primaryContainer, fg = cs.onPrimaryContainer) },
                        )
                    }
                }
            }
            if (matches.isNotEmpty()) {
                item {
                    Row(Modifier.fillMaxWidth().clickable { expanded = !expanded }.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        SectionLabel(if (query.isBlank()) "Blocked · ${matches.size}" else "Matches · ${matches.size}", Modifier.weight(1f))
                        if (query.isBlank()) Icon(if (open) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore, if (open) "collapse" else "expand", tint = cs.onSurfaceVariant)
                    }
                }
                if (open) {
                    val list = matches.take(shown)
                    items(list, key = { it.id }) { item ->
                        val st = Live.status(cfg, item, now)   // computed only for this visible row
                        ListRow(
                            title = item.name,
                            subtitle = ruleSummary(item),
                            leading = { com.husarp.lockdown.ui.ItemIcon(item.type, item.target, item.name) },
                            trailing = { StatusChip(statusText(st, cfg, item, now), st) },
                            onClick = { onEdit(item) },
                        )
                    }
                    if (shown < matches.size) item {
                        Text("Showing ${list.size} of ${matches.size} — scroll for more", Modifier.padding(8.dp),
                            style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                    }
                } else item {
                    Text("Tap to show the blocked list", Modifier.padding(horizontal = 4.dp),
                        style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                }
            }
            if (cfg.items.isEmpty()) item {
                Box(Modifier.fillMaxWidth().padding(top = 48.dp), contentAlignment = androidx.compose.ui.Alignment.Center) {
                    Text("Nothing blocked yet. Tap Block to add an app or site.", color = cs.onSurfaceVariant)
                }
            }
            item { Spacer(Modifier.height(72.dp)) }
        }
    }
}

private fun statusText(status: Status, cfg: com.husarp.lockdown.data.Config, item: Item, now: LocalDateTime): String = when (status) {
    Status.BLOCKED -> "Blocked"
    Status.SOON -> Live.dayLimitFraction(cfg, item, now)?.let { "${it.second} min left" } ?: "Soon"
    Status.ALLOWED -> "Allowed"
    Status.PAUSED -> "Paused"
}

/** A one-line human summary of an item's own rules, for the list. */
fun ruleSummary(item: Item): String {
    if (item.rules.isEmpty()) return if (item.type == ItemType.APP) "App" else "Site"
    return item.rules.joinToString(" · ") { r ->
        when (r.type) {
            com.husarp.lockdown.engine.RuleType.PERMANENT -> "Always blocked"
            com.husarp.lockdown.engine.RuleType.TEMPORARY -> "Temporary"
            com.husarp.lockdown.engine.RuleType.TIME_LIMIT -> "${r.dailyLimitMin ?: r.weeklyLimitMin ?: r.monthlyLimitMin ?: 0} min limit"
            com.husarp.lockdown.engine.RuleType.SWITCH_LIMIT -> "${r.dailySwitchLimit ?: 0} opens"
            com.husarp.lockdown.engine.RuleType.SCHEDULED -> "Scheduled hours"
        }
    }
}
