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
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.ui.text.style.TextOverflow
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
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.GroupLook
import com.husarp.lockdown.ui.IconTile
import com.husarp.lockdown.ui.Kinds
import com.husarp.lockdown.ui.MemberStack
import com.husarp.lockdown.ui.PillRow
import com.husarp.lockdown.ui.PillSpec
import com.husarp.lockdown.ui.SectionLabel
import com.husarp.lockdown.ui.theme.Hue
import com.husarp.lockdown.ui.theme.tone
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
fun BlockingScreen(onOpenNetLog: () -> Unit, onOpenCalendar: () -> Unit = {}) {
    var page by remember { mutableStateOf<BlockPage>(BlockPage.List) }
    when (val p = page) {
        BlockPage.List -> BlockList(
            onAdd = { page = BlockPage.Add },
            onEdit = { page = BlockPage.Edit(it) },
            onEditGroup = { page = BlockPage.EditGroup(it) },
            onProtection = { page = BlockPage.Protection },
            onKeywords = { page = BlockPage.Keywords },
            onCalendar = onOpenCalendar,
        )
        BlockPage.Add -> AddItemSheet(onDone = { page = BlockPage.List }, onEdit = { page = BlockPage.Edit(it) }, onEditGroup = { page = BlockPage.EditGroup(it) })
        is BlockPage.Edit -> RuleEditor(p.item, onClose = { page = BlockPage.List })
        is BlockPage.EditGroup -> GroupEditor(p.group, onClose = { page = BlockPage.List })
        BlockPage.Protection -> ProtectionEditor(onClose = { page = BlockPage.List })
        BlockPage.Keywords -> KeywordsEditor(onClose = { page = BlockPage.List })
    }
}

@OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
@Composable
private fun BlockList(
    onAdd: () -> Unit,
    onEdit: (Item) -> Unit,
    onEditGroup: (Group) -> Unit,
    onProtection: () -> Unit,
    onKeywords: () -> Unit,
    onCalendar: () -> Unit,
) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val cs = MaterialTheme.colorScheme
    var query by remember { mutableStateOf("") }
    val now = com.husarp.lockdown.guard.TrustedTime.local(androidx.compose.ui.platform.LocalContext.current)
    val mode = com.husarp.lockdown.data.ModesStore.current(now)

    val byId = remember(cfg.items) { cfg.items.associateBy { it.id } }
    // which groups each item is in, built once per change of the groups (each row's pills read it)
    val memberOf = remember(cfg.groups) {
        HashMap<String, MutableList<Group>>().also { m -> cfg.groups.forEach { g -> g.memberIds.forEach { m.getOrPut(it) { ArrayList() }.add(g) } } }
    }
    val apps = remember(cfg.items) { cfg.items.filter { it.type == ItemType.APP } }
    val sites = remember(cfg.items) { cfg.items.filter { it.type == ItemType.SITE } }
    val matches = if (query.isBlank()) emptyList() else cfg.items.filter { it.name.contains(query, true) || it.target.contains(query, true) }
    // The list can be thousands long (imported sites), so the sites stay collapsed and every list pages: 20 rows at a
    // time, 20 more as you scroll to its end. Status is computed per VISIBLE row only. Searching shows one list.
    val listState = rememberLazyListState()
    var appsOpen by remember { mutableStateOf<Boolean?>(null) }       // null: open when there are 30 or fewer
    var sitesOpen by remember { mutableStateOf(false) }
    var appsShown by remember { mutableIntStateOf(20) }
    var sitesShown by remember { mutableIntStateOf(20) }
    var shown by remember { mutableIntStateOf(20) }
    val showApps = appsOpen ?: (apps.size <= 30)
    LaunchedEffect(query) { shown = 20 }
    LaunchedEffect(listState, matches.size, apps.size, sites.size, showApps, sitesOpen, query.isBlank()) {
        snapshotFlow { val li = listState.layoutInfo; (li.visibleItemsInfo.lastOrNull()?.index ?: 0) to li.totalItemsCount }
            .collect { (last, total) ->
                if (last < total - 3) return@collect
                when {
                    query.isNotBlank() -> if (shown < matches.size) shown = (shown + 20).coerceAtMost(matches.size)
                    sitesOpen && sitesShown < sites.size -> sitesShown = (sitesShown + 20).coerceAtMost(sites.size)
                    !sitesOpen && showApps && appsShown < apps.size -> appsShown = (appsShown + 20).coerceAtMost(apps.size)
                }
            }
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
                androidx.compose.foundation.layout.FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    val prot = cfg.protection.enabled.size
                    Chip(if (prot > 0) "Protection · $prot" else "Protection", selected = prot > 0, icon = Kinds.PROTECTION.icon,
                        hue = Kinds.PROTECTION.hue, iconTint = tone(Kinds.PROTECTION.hue).accent) { onProtection() }
                    Chip("Blocked words", selected = cfg.keywords.enabled, icon = Kinds.WORDS.icon, hue = Kinds.WORDS.hue,
                        iconTint = tone(Kinds.WORDS.hue).accent) { onKeywords() }
                    Chip("Calendar", selected = false, icon = androidx.compose.material.icons.Icons.Filled.CalendarMonth, hue = Hue.INDIGO,
                        iconTint = tone(Hue.INDIGO).accent) { onCalendar() }
                }
            }
            if (query.isNotBlank()) {
                item { SectionLabel("Matches · ${matches.size}") }
                val list = matches.take(shown)
                items(list, key = { it.id }) { item -> ItemRow(item, cfg, memberOf, now, mode) { onEdit(item) } }
                if (shown < matches.size) item { MoreNote(list.size, matches.size) }
                // the imported sites aren't items (data/ImportedSites.kt): say when the text is one of them
                val q = query.trim().lowercase()
                val importedHits = cfg.imported.count { it.contains(q) }
                if (importedHits > 0) item {
                    Text("$importedHits imported site${if (importedHits == 1) "" else "s"} match, blocked for good (Settings > Backup & import).",
                        Modifier.padding(horizontal = 4.dp), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                }
            } else {
                if (cfg.groups.isNotEmpty()) {
                    item { SectionLabel("Groups · ${cfg.groups.size}") }
                    items(cfg.groups, key = { "g:" + it.id }) { g -> GroupRow(g, byId, now) { onEditGroup(g) } }
                }
                if (apps.isNotEmpty()) {
                    item { Header("Apps · ${apps.size}", showApps) { appsOpen = !showApps } }
                    if (showApps) {
                        val list = apps.take(appsShown)
                        items(list, key = { it.id }) { item -> ItemRow(item, cfg, memberOf, now, mode) { onEdit(item) } }
                        if (appsShown < apps.size) item {
                            if (sitesOpen) TextButton(onClick = { appsShown = (appsShown + 20).coerceAtMost(apps.size) }) {
                                Text("Show more apps (${apps.size - appsShown} left)")
                            } else MoreNote(list.size, apps.size)
                        }
                    }
                }
                if (sites.isNotEmpty()) {
                    item { Header("Sites · ${sites.size}", sitesOpen) { sitesOpen = !sitesOpen } }
                    if (sitesOpen) {
                        val list = sites.take(sitesShown)
                        items(list, key = { it.id }) { item -> ItemRow(item, cfg, memberOf, now, mode) { onEdit(item) } }
                        if (sitesShown < sites.size) item { MoreNote(list.size, sites.size) }
                    } else item {
                        Text("Tap to show the blocked sites", Modifier.padding(horizontal = 4.dp),
                            style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                    }
                }
                if (cfg.imported.isNotEmpty()) item {
                    Text("Also ${cfg.imported.size} imported sites, blocked for good (Settings > Backup & import).",
                        Modifier.padding(horizontal = 4.dp), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                }
            }
            if (cfg.items.isEmpty() && cfg.imported.isEmpty()) item {
                Box(Modifier.fillMaxWidth().padding(top = 48.dp), contentAlignment = androidx.compose.ui.Alignment.Center) {
                    Text("Nothing blocked yet. Tap Block to add an app or site.", color = cs.onSurfaceVariant)
                }
            }
            item { Spacer(Modifier.height(72.dp)) }
        }
    }
}

/** A collapsible list's label ("Apps · 12") with its chevron. */
@Composable
private fun Header(text: String, open: Boolean, onToggle: () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable { onToggle() }.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        SectionLabel(text, Modifier.weight(1f))
        Icon(if (open) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore, if (open) "collapse" else "expand",
            tint = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun MoreNote(shown: Int, total: Int) {
    Text("Showing $shown of $total — scroll for more", Modifier.padding(8.dp),
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

/** A group: its icon and colour, its shared rules (on their own line, so they keep the room), the members' icons
 *  and "6 members". */
@Composable
private fun GroupRow(g: Group, byId: Map<String, Item>, now: LocalDateTime, onClick: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    val members = remember(g.memberIds, byId) { g.memberIds.mapNotNull { byId[it] } }
    val pills = remember(g) {
        (if (g.disabled) listOf(PillSpec("Paused", Kinds.PAUSE.hue, Kinds.PAUSE.icon)) else emptyList()) + Kinds.rulePills(g.rules, now)
    }
    Card(color = cs.surfaceContainerLow, padding = androidx.compose.foundation.layout.PaddingValues(0.dp), onClick = onClick) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            GroupTile(g)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(g.name, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                PillRow(pills)
            }
            Column(horizontalAlignment = Alignment.End, verticalArrangement = Arrangement.spacedBy(2.dp)) {
                MemberStack(members)
                Text("${members.size} member${if (members.size == 1) "" else "s"}", style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant)
            }
            Icon(Icons.Filled.ChevronRight, null, tint = cs.onSurfaceVariant)
        }
    }
}

/** An app or a site: its icon, its own rules and its groups as pills, and its status now (computed for this
 *  visible row only). */
@Composable
private fun ItemRow(item: Item, cfg: com.husarp.lockdown.data.Config, memberOf: Map<String, List<Group>>, now: LocalDateTime,
                    mode: com.husarp.lockdown.engine.ModeState?, onClick: () -> Unit) {
    val pills = remember(item, memberOf) { Kinds.itemPills(item, memberOf[item.id].orEmpty(), now) }
    val st = Live.status(cfg, item, now, mode)
    val info = if (st == Status.BLOCKED) Live.blockedInfo(cfg, item, now, mode) else null
    Row(Modifier.fillMaxWidth().clickable { onClick() }.padding(horizontal = 4.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
        com.husarp.lockdown.ui.ItemIcon(item.type, item.target, item.name)
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(item.name, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            PillRow(pills)
        }
        StatusChip(info?.let { Kinds.chipText(it, now) } ?: statusText(st, cfg, item, now, mode), st, grey = info?.grey == true)
    }
}

/** A group's icon in its colour (picked, or from its name and id). */
@Composable
fun GroupTile(g: Group, size: androidx.compose.ui.unit.Dp = 40.dp) = IconTile(GroupLook.icon(g), GroupLook.hue(g), size)

private fun statusText(status: Status, cfg: com.husarp.lockdown.data.Config, item: Item, now: LocalDateTime,
                       mode: com.husarp.lockdown.engine.ModeState?): String = when (status) {
    Status.BLOCKED -> "Blocked"
    Status.PAUSED -> "Paused"
    else -> Live.chipText(cfg, item, now, mode)      // "Allowed", or "5 min left" / "5 min allowed" when running low
}
