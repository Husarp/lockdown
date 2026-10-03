package com.husarp.lockdown.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.block.Protection
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Keywords
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.guard.rememberGuard
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.EditorHeader
import com.husarp.lockdown.ui.ListRow
import com.husarp.lockdown.ui.SectionLabel
import com.husarp.lockdown.ui.SwitchRowInline

@Composable
fun GroupEditor(group: Group, onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val guard = rememberGuard()
    val ctx = LocalContext.current
    var draft by remember { mutableStateOf(cfg.groups.firstOrNull { it.id == group.id } ?: group) }
    val cs = MaterialTheme.colorScheme
    var pick by remember { mutableStateOf("") }
    var newSite by remember { mutableStateOf("") }
    // Installed apps that aren't on the blocklist yet can be members too; picked here, they're added on Save.
    val installed = rememberInstalledApps()
    var newApps by remember { mutableStateOf(listOf<Item>()) }
    var extrasFor by remember { mutableStateOf<String?>(null) }
    val byId = remember(cfg.items) { cfg.items.associateBy { it.id } }
    val listedPkgs = remember(cfg.items) { cfg.items.filter { it.type == ItemType.APP }.mapTo(HashSet()) { it.target.lowercase() } }

    fun setMember(id: String, on: Boolean) {
        draft = if (on) draft.copy(memberIds = (draft.memberIds + id).distinct())
                else draft.copy(memberIds = draft.memberIds - id, overrides = draft.overrides - id)
    }

    Column(Modifier.fillMaxSize()) {
        EditorHeader(draft.name.ifBlank { "New group" }, onBack = onClose, action = "Save") {
            val original = cfg.groups.firstOrNull { it.id == draft.id }
            val loosening = original != null && AntiBypass.groupLooser(original, draft, TrustedTime.local(ctx))
            guard(loosening) {
                val added = newApps.filter { it.id in draft.memberIds }
                Store.update { c ->
                    val items = c.items + added
                    if (c.groups.any { it.id == draft.id }) c.copy(items = items, groups = c.groups.map { if (it.id == draft.id) draft else it })
                    else c.copy(items = items, groups = c.groups + draft)
                }
                onClose()
            }
        }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            OutlinedTextField(draft.name, { draft = draft.copy(name = it) }, label = { Text("Group name") }, singleLine = true, modifier = Modifier.fillMaxWidth())

            SectionLabel("Shared rules (apply to every member)")
            Text("A group can carry several rules at once — e.g. scheduled hours AND a shared daily limit. Time on any member fills the group's limit.",
                style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            RuleFields(draft.rules) { draft = draft.copy(rules = it) }

            SectionLabel("Members")
            OutlinedTextField(pick, { pick = it }, placeholder = { Text("Search apps & items") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            // Candidates: apps + items not already blocked-forever + current members. The thousands of permanently
            // blocked imported sites are hidden here — grouping something already blocked forever does nothing.
            // Then the installed apps not on the list yet (the search used to find only items, so nothing came up).
            val candidates = (cfg.items + newApps).filter {
                (it.type == ItemType.APP || it.rules.none { r -> r.type == RuleType.PERMANENT } || it.id in draft.memberIds) &&
                    (it.name.contains(pick, true) || it.target.contains(pick, true))
            }
            val pending = newApps.mapTo(HashSet()) { it.target.lowercase() }
            val apps = if (pick.isBlank()) emptyList() else installed.filter { a ->   // once something is typed
                a.pkg.lowercase() !in listedPkgs && a.pkg.lowercase() !in pending &&
                    (a.label.contains(pick, true) || a.pkg.contains(pick, true))
            }
            Card(color = cs.surfaceContainerLow, padding = androidx.compose.foundation.layout.PaddingValues(vertical = 4.dp)) {
                if (candidates.isEmpty() && apps.isEmpty()) Text("Nothing matches. Add a site below.", Modifier.padding(16.dp), color = cs.onSurfaceVariant)
                LazyColumn(Modifier.fillMaxWidth().heightIn(max = 280.dp)) {
                    items(candidates, key = { it.id }) { it2 ->
                        ListRow(title = it2.name, subtitle = if (it2.type == ItemType.APP) "App" else it2.target,
                            trailing = { androidx.compose.material3.Checkbox(checked = it2.id in draft.memberIds, onCheckedChange = { on ->
                                setMember(it2.id, on)
                                if (!on && newApps.any { n -> n.id == it2.id }) newApps = newApps.filterNot { n -> n.id == it2.id }
                            }) })
                    }
                    items(apps, key = { "pkg:" + it.pkg }) { a ->
                        ListRow(title = a.label, subtitle = "App · not on your list yet",
                            trailing = { androidx.compose.material3.Checkbox(checked = false, onCheckedChange = { on ->
                                if (on) {
                                    // no own rule: the group's rules govern it
                                    val item = Item(java.util.UUID.randomUUID().toString().take(8), a.label, a.pkg, ItemType.APP)
                                    newApps = newApps + item
                                    setMember(item.id, true)
                                }
                            }) })
                    }
                }
            }
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(newSite, { newSite = it }, placeholder = { Text("Add a site to this group (e.g. reddit.com)") }, singleLine = true, modifier = Modifier.weight(1f))
                TextButton(onClick = {
                    val h = newSite.trim().lowercase().removePrefix("https://").removePrefix("http://").removePrefix("www.").substringBefore("/")
                    if (h.isNotEmpty()) {
                        val item = Item(java.util.UUID.randomUUID().toString().take(8), h, h, ItemType.SITE)   // no own rule: the group's shared rule governs it
                        Store.update { c -> c.copy(items = c.items + item) }
                        draft = draft.copy(memberIds = draft.memberIds + item.id)
                        newSite = ""
                    }
                }) { Text("Add") }
            }

            // A member's own limits come on top of the group's, never instead (like the PC since 0.84.3).
            val members = draft.memberIds.mapNotNull { id -> byId[id] ?: newApps.firstOrNull { it.id == id } }
            if (members.isNotEmpty()) {
                SectionLabel("Extra limits on top of the group's")
                Text("The group's rules still apply to every member, and a member's time still fills them. An extra limit " +
                    "only makes one member stricter — e.g. the group 2 h a day, and YouTube only 1 h of it.",
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                Card(color = cs.surfaceContainerLow, padding = androidx.compose.foundation.layout.PaddingValues(vertical = 4.dp)) {
                    members.forEach { m ->
                        val extras = draft.overrides[m.id] ?: emptyMap()
                        val open = extrasFor == m.id
                        ListRow(title = m.name, subtitle = if (extras.isEmpty()) "Only the group's rules" else "+ ${extras.size} extra limit${if (extras.size > 1) "s" else ""}",
                            trailing = { TextButton(onClick = { extrasFor = if (open) null else m.id }) { Text(if (open) "Done" else "Extra limits") } })
                        if (open) Column(Modifier.padding(horizontal = 12.dp, vertical = 4.dp)) {
                            Text("Extra limits for ${m.name}, on top of the group's", style = MaterialTheme.typography.titleSmall)
                            Spacer(Modifier.height(8.dp))
                            RuleFields(extras.values.toList()) { rules ->
                                val map = rules.associateBy { it.type.name }
                                draft = draft.copy(overrides = if (map.isEmpty()) draft.overrides - m.id else draft.overrides + (m.id to map))
                            }
                        }
                    }
                }
            }

            TextButton(onClick = { guard(true) { Store.update { c -> c.copy(groups = c.groups.filterNot { it.id == draft.id }) }; onClose() } }) { Text("Delete group") }
        }
    }
}

@Composable
fun ProtectionEditor(onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val guard = rememberGuard()
    val ctx = LocalContext.current
    val cs = MaterialTheme.colorScheme

    Column(Modifier.fillMaxSize()) {
        EditorHeader("Protection lists", onBack = onClose)
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("Bundled blocklists — flip one on to block its whole category of sites.", style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
            Protection.LISTS.forEach { def ->
                val on = def.key in cfg.protection.enabled
                val info = remember(def.key, on) { runCatching { Protection.info(ctx, def.key) }.getOrDefault(0 to false) }
                Card(color = cs.surfaceContainerLow) {
                    SwitchRowInline(def.name, on) { want ->
                        val apply = {
                            Store.update { c ->
                                val e = if (want) (c.protection.enabled + def.key).distinct() else c.protection.enabled - def.key
                                c.copy(protection = c.protection.copy(enabled = e))
                            }
                            Protection.load(ctx)
                        }
                        if (want) apply() else guard(true) { apply() }
                    }
                    Text("${def.desc} · ${info.first} domains", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                }
            }
            SectionLabel("Allowed exceptions")
            ExceptionList(cfg.protection.allowed,
                onAdd = { host -> Store.update { c -> c.copy(protection = c.protection.copy(allowed = (c.protection.allowed + host).distinct())) } },
                onRemove = { host -> guard(true) { Store.update { c -> c.copy(protection = c.protection.copy(allowed = c.protection.allowed - host)) } } })
        }
    }
}

@Composable
fun KeywordsEditor(onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val guard = rememberGuard()
    val cs = MaterialTheme.colorScheme
    val kw = cfg.keywords
    fun setKw(block: (com.husarp.lockdown.engine.KeywordsCfg) -> com.husarp.lockdown.engine.KeywordsCfg) = Store.update { it.copy(keywords = block(it.keywords)) }

    var addWord by remember { mutableStateOf("") }

    Column(Modifier.fillMaxSize()) {
        EditorHeader("Blocked words", onBack = onClose)
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Card(color = cs.surfaceContainerLow) {
                SwitchRowInline("Block words in browsers", kw.enabled) { on -> if (on) setKw { it.copy(enabled = true) } else guard(true) { setKw { it.copy(enabled = false) } } }
                SwitchRowInline("Force SafeSearch", kw.safeSearch) { on -> if (on) setKw { it.copy(safeSearch = true) } else guard(true) { setKw { it.copy(safeSearch = false) } } }
                SwitchRowInline("YouTube Restricted Mode", kw.youtube) { on -> if (on) setKw { it.copy(youtube = true) } else guard(true) { setKw { it.copy(youtube = false) } } }
            }
            // Add a word — at the TOP, so you don't scroll past hundreds of words to reach it.
            Card(color = cs.surfaceContainerLow) {
                Text("Add a word", style = MaterialTheme.typography.titleSmall)
                Text("A trailing * matches longer words (porn*); a few words make a phrase; accents are ignored.",
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                Spacer(Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(addWord, { addWord = it }, placeholder = { Text("e.g. gambling") }, singleLine = true, modifier = Modifier.weight(1f))
                    TextButton(onClick = { val w = addWord.trim(); if (w.isNotEmpty()) { setKw { it.copy(words = (it.words + w).distinct()) }; addWord = "" } }) { Text("Add") }
                }
            }
            SectionLabel("Word lists — tap to open, search, turn words on/off")
            // Built-in lists: toggle the whole list, or open it to turn single words off/on. Built-in words can't be
            // deleted (they ship with the app), only switched off. Turning a word OFF loosens, so it's challenge-gated.
            Keywords.READY.forEach { (key, pair) ->
                CollapsibleWordList(
                    title = pair.first, count = pair.second.size,
                    on = kw.lists[key] == true,
                    onToggle = { want -> if (want) setKw { it.copy(lists = it.lists + (key to true)) } else guard(true) { setKw { it.copy(lists = it.lists + (key to false)) } } },
                    words = pair.second, isActive = { w -> w !in kw.off },
                    onWord = { w, active -> if (active) guard(true) { setKw { it.copy(off = (it.off + w).distinct()) } } else setKw { it.copy(off = it.off - w) } },
                    canRemove = false, onRemove = {},
                )
            }
            // Your words: same collapsible shape, but editable (remove your own).
            CollapsibleWordList(
                title = "Your words", count = kw.words.size,
                on = kw.wordsOn,
                onToggle = { want -> if (want) setKw { it.copy(wordsOn = true) } else guard(true) { setKw { it.copy(wordsOn = false) } } },
                words = kw.words, isActive = { true }, onWord = { _, _ -> },
                canRemove = true, onRemove = { w -> guard(true) { setKw { it.copy(words = it.words - w) } } },
            )
            SectionLabel("Exceptions (a site or a word that's never blocked)")
            ExceptionList(kw.exceptions,
                onAdd = { w -> guard(true) { setKw { it.copy(exceptions = (it.exceptions + w).distinct()) } } },
                onRemove = { w -> setKw { it.copy(exceptions = it.exceptions - w) } })
        }
    }
}

/** A collapsible word list: header (name · count · on/off · chevron); opened, a search + each word with a
 *  per-word switch (built-in: on/off) or a remove (your words). */
@Composable
private fun CollapsibleWordList(
    title: String, count: Int, on: Boolean, onToggle: (Boolean) -> Unit,
    words: List<String>, isActive: (String) -> Boolean, onWord: (String, Boolean) -> Unit,
    canRemove: Boolean, onRemove: (String) -> Unit,
) {
    val cs = MaterialTheme.colorScheme
    var expanded by remember { mutableStateOf(false) }
    var q by remember { mutableStateOf("") }
    Card(color = cs.surfaceContainerLow) {
        Row(Modifier.fillMaxWidth().clickable { expanded = !expanded }, verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleSmall)
                Text("$count words", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
            Switch(checked = on, onCheckedChange = onToggle)
            Icon(if (expanded) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore, null, tint = cs.onSurfaceVariant)
        }
        if (expanded) {
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(q, { q = it }, placeholder = { Text("Search words") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            val shown = words.filter { it.contains(q, true) }
            LazyColumn(Modifier.fillMaxWidth().heightIn(max = 300.dp)) {
                items(shown, key = { it }) { w ->
                    val active = isActive(w)
                    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(w, modifier = Modifier.weight(1f), color = if (active) cs.onSurface else cs.onSurfaceVariant,
                            textDecoration = if (active) null else TextDecoration.LineThrough)
                        if (canRemove) IconButton(onClick = { onRemove(w) }) { Icon(Icons.Filled.Close, "remove") }
                        else Switch(checked = active, onCheckedChange = { onWord(w, active) })
                    }
                }
            }
        }
    }
}

/** A simple editable list of strings with an add field and per-row remove. */
@Composable
private fun ExceptionList(values: List<String>, onAdd: (String) -> Unit, onRemove: (String) -> Unit) {
    val cs = MaterialTheme.colorScheme
    var field by remember { mutableStateOf("") }
    Card(color = cs.surfaceContainerLow, padding = androidx.compose.foundation.layout.PaddingValues(vertical = 4.dp)) {
        values.forEach { v ->
            ListRow(title = v, trailing = { IconButton(onClick = { onRemove(v) }) { Icon(Icons.Filled.Close, "remove") } })
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(field, { field = it }, placeholder = { Text("Add…") }, singleLine = true, modifier = Modifier.weight(1f))
            TextButton(onClick = { val t = field.trim(); if (t.isNotEmpty()) { onAdd(t); field = "" } }) { Text("Add") }
        }
    }
}
