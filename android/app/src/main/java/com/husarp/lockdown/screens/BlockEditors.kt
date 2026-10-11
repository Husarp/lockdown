package com.husarp.lockdown.screens

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
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
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Edit
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
import com.husarp.lockdown.data.ProtectionCfg
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.data.inSavedOrder
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.BlockMethod
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.Keywords
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.guard.rememberGuard
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.EditorHeader
import com.husarp.lockdown.ui.Kinds
import com.husarp.lockdown.ui.ListRow
import com.husarp.lockdown.ui.PillRow
import com.husarp.lockdown.ui.Status
import com.husarp.lockdown.ui.StatusChip
import com.husarp.lockdown.ui.SaveBar
import com.husarp.lockdown.ui.SectionLabel
import com.husarp.lockdown.ui.SwitchRowInline
import com.husarp.lockdown.ui.Unsaved
import com.husarp.lockdown.ui.UnsavedChanges
import com.husarp.lockdown.ui.rememberDraft

@Composable
fun GroupEditor(group: Group, onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val guard = rememberGuard()
    val ctx = LocalContext.current
    var draft by remember { mutableStateOf(cfg.groups.firstOrNull { it.id == group.id } ?: group) }
    val cs = MaterialTheme.colorScheme
    // Installed apps that aren't on the blocklist yet can be members too; picked here, they're added on Save.
    var newApps by remember { mutableStateOf(listOf<Item>()) }
    var memberOpen by remember { mutableStateOf<String?>(null) }     // a member's own page
    var adding by remember { mutableStateOf(false) }                  // the Add members page
    var looks by remember { mutableStateOf(false) }                   // the icon and colour dialog
    val byId = remember(cfg.items) { cfg.items.associateBy { it.id } }

    fun setMember(id: String, on: Boolean) {
        draft = if (on) draft.copy(memberIds = (draft.memberIds + id).distinct())
                else draft.copy(memberIds = draft.memberIds - id, overrides = draft.overrides - id, memberBlocks = draft.memberBlocks - id)
        if (!on && newApps.any { it.id == id }) newApps = newApps.filterNot { it.id == id }
    }

    fun save(then: () -> Unit) {
        val original = cfg.groups.firstOrNull { it.id == draft.id }
        val loosening = original != null && AntiBypass.groupLooser(original, draft, TrustedTime.local(ctx), byId, byId + newApps.associateBy { it.id })
        guard(loosening) {
            val added = newApps.filter { it.id in draft.memberIds }
            Store.update { c ->
                val items = c.items + added
                if (c.groups.any { it.id == draft.id }) c.copy(items = items, groups = c.groups.map { if (it.id == draft.id) draft else it })
                else c.copy(items = items, groups = c.groups + draft)
            }
            newApps = emptyList()
            then()
        }
    }
    UnsavedChanges(draft != (cfg.groups.firstOrNull { it.id == group.id } ?: group) || newApps.any { it.id in draft.memberIds }, onClose, ::save)
    // Back from a member's page or Add members goes up one page, never asks (composed after UnsavedChanges: it wins).
    BackHandler(enabled = memberOpen != null || adding) { memberOpen = null; adding = false }

    val members = draft.memberIds.mapNotNull { id -> byId[id] ?: newApps.firstOrNull { it.id == id } }
    val open = memberOpen?.let { id -> members.firstOrNull { it.id == id } }
    if (open != null) {
        GroupMemberPage(draft, open, onChange = { draft = it }, onRemove = { setMember(open.id, false); memberOpen = null }, onBack = { memberOpen = null })
        return
    }
    if (adding) {
        AddMembersPage(draft, cfg, newApps, onMember = ::setMember, onNewApp = { item -> newApps = newApps + item; setMember(item.id, true) },
            onNewSite = { item -> Store.update { c -> c.copy(items = c.items + item) }; draft = draft.copy(memberIds = draft.memberIds + item.id) },
            onDone = { adding = false })
        return
    }

    Column(Modifier.fillMaxSize()) {
        EditorHeader(draft.name.ifBlank { "New group" }, onBack = { Unsaved.leave(onClose) }, action = "Save") { save(onClose) }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                Box(Modifier.clickable { looks = true }) {
                    GroupTile(draft, 56.dp)
                    Icon(Icons.Filled.Edit, "Icon and colour", Modifier.align(Alignment.BottomEnd).offset(x = 4.dp, y = 4.dp).size(20.dp)
                        .background(cs.surface, CircleShape).padding(3.dp), tint = cs.onSurfaceVariant)
                }
                OutlinedTextField(draft.name, { draft = draft.copy(name = it) }, label = { Text("Group name") }, singleLine = true, modifier = Modifier.weight(1f))
            }
            Card(color = cs.surfaceContainer, shape = MaterialTheme.shapes.extraLarge) {
                Text(groupSentence(draft, members.size), style = MaterialTheme.typography.bodyLarge)
                if (draft.rules.isNotEmpty()) {
                    Spacer(Modifier.height(10.dp))
                    PillRow(Kinds.rulePills(draft.rules), max = 4)
                }
            }

            SectionLabel("Shared rules (apply to every member)")
            Text("A group can carry several rules at once — e.g. scheduled hours AND a shared daily limit. Time on any member fills the group's limit.",
                style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            RuleFields(draft.rules, group = true) { draft = draft.copy(rules = it) }

            // How the group blocks its member sites and apps (PC 0.84.11).
            SectionLabel("How member sites are blocked")
            val way = draft.siteBlock
            if (way == null) {
                Text("Each site the way it's set itself.", style = MaterialTheme.typography.bodyMedium)
                TextButton(onClick = { draft = draft.copy(siteBlock = "dns") }) { Text("Choose for this group") }
            } else {
                SiteWayChips(BlockMethod.flags(way)) { f -> if (f.isNotEmpty()) draft = draft.copy(siteBlock = f.joinToString(",")) }
                Text("When this group blocks a site, this way is used, whatever the site's own setting. A site's own rules keep its own way.",
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                TextButton(onClick = { draft = draft.copy(siteBlock = null) }) { Text("Each site its own way") }
            }
            SectionLabel("How member apps are blocked")
            val appWay = draft.appBlock
            if (appWay == null) {
                Text("Each app the way it's set itself.", style = MaterialTheme.typography.bodyMedium)
                TextButton(onClick = { draft = draft.copy(appBlock = "close") }) { Text("Choose for this group") }
            } else {
                val grey = BlockMethod.greyOnly(appWay)
                AppWayChips(grey) { draft = draft.copy(appBlock = if (it) BlockMethod.GREY else "close") }
                Text(appWayText(grey) + " When this group blocks an app, this way is used. An app's own rules keep its own way.",
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                GrayscaleNote(grey)
                TextButton(onClick = { draft = draft.copy(appBlock = null) }) { Text("Each app its own way") }
            }

            // Members: each with its icon, what applies to it here (the group's rules, its extra limits, its own way,
            // its own rules) and its status now; tap one for its extra limits (on top of the group's, never instead).
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                SectionLabel("Members · ${members.size}", Modifier.weight(1f))
                TextButton(onClick = { adding = true }) { Icon(Icons.Filled.Add, null, Modifier.size(18.dp)); Spacer(Modifier.size(4.dp)); Text("Add") }
            }
            val now = TrustedTime.local(ctx)
            val mode = com.husarp.lockdown.data.ModesStore.current(now)
            Card(color = cs.surfaceContainerLow, padding = androidx.compose.foundation.layout.PaddingValues(vertical = 4.dp)) {
                if (members.isEmpty()) Text("No members yet. Tap Add to pick apps or sites.", Modifier.padding(16.dp), color = cs.onSurfaceVariant)
                members.forEach { m ->
                    val pills = remember(draft, m) { Kinds.memberPills(draft, m, now) }
                    val saved = byId[m.id]                       // status: what's in effect now (the saved config)
                    val st = if (saved == null) Status.ALLOWED else com.husarp.lockdown.data.Live.status(cfg, saved, now, mode)
                    val info = if (saved != null && st == Status.BLOCKED) com.husarp.lockdown.data.Live.blockedInfo(cfg, saved, now, mode) else null
                    Row(Modifier.fillMaxWidth().clickable { memberOpen = m.id }.padding(start = 16.dp, end = 8.dp, top = 10.dp, bottom = 10.dp),
                        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        com.husarp.lockdown.ui.ItemIcon(m.type, m.target, m.name)
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Text(m.name, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                            PillRow(pills)
                        }
                        // (no chevron: the whole row opens the member, and the name keeps its room)
                        StatusChip(info?.let { Kinds.chipText(it, now) } ?: if (saved == null) "Allowed" else when (st) { Status.BLOCKED -> "Blocked"; Status.PAUSED -> "Paused"; else -> com.husarp.lockdown.data.Live.chipText(cfg, saved, now, mode) },
                            st, grey = info?.grey == true)
                    }
                }
            }

            TextButton(onClick = { guard(true) { Store.update { c -> c.copy(groups = c.groups.filterNot { it.id == draft.id }) }; onClose() } },
                colors = androidx.compose.material3.ButtonDefaults.textButtonColors(contentColor = cs.error)) { Text("Delete group") }
        }
    }

    if (looks) IconColourDialog(draft, onPick = { icon, color -> draft = draft.copy(icon = icon, color = color) }, onDismiss = { looks = false })
}

@Composable
fun ProtectionEditor(onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val guard = rememberGuard()
    val ctx = LocalContext.current
    val cs = MaterialTheme.colorScheme

    // A draft, saved with Save: one challenge when a list goes off or an exception is added.
    val d = rememberDraft(cfg.protection)
    val p = d.value
    fun set(f: (ProtectionCfg) -> ProtectionCfg) = d.update { old ->
        f(old).let { it.copy(enabled = inSavedOrder(d.saved.enabled, it.enabled), allowed = inSavedOrder(d.saved.allowed, it.allowed)) }
    }
    fun save(then: () -> Unit) {
        val new = d.value
        val old = Store.config.protection
        guard(AntiBypass.protectionLooser(old.enabled, old.allowed, new.enabled, new.allowed)) {
            Store.update { c -> c.copy(protection = new) }
            if (new.enabled != old.enabled) Protection.load(ctx)
            d.clear()
            then()
        }
    }
    UnsavedChanges(d.dirty, onClose, ::save, d::clear)

    Column(Modifier.fillMaxSize()) {
        EditorHeader("Protection lists", onBack = { Unsaved.leave(onClose) })
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("Bundled blocklists — flip one on to block its whole category of sites.", style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
            Protection.LISTS.forEach { def ->
                val loaded = def.key in cfg.protection.enabled
                val info = remember(def.key, loaded) { runCatching { Protection.info(ctx, def.key) }.getOrDefault(0 to false) }
                Card(color = cs.surfaceContainerLow) {
                    SwitchRowInline(def.name, def.key in p.enabled) { want ->
                        set { it.copy(enabled = if (want) (it.enabled + def.key).distinct() else it.enabled - def.key) }
                    }
                    Text("${def.desc} · ${info.first} domains", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                }
            }
            SectionLabel("Allowed exceptions")
            ExceptionList(p.allowed, clean = { it.filterNot(Char::isWhitespace) },
                onAdd = { host -> set { it.copy(allowed = (it.allowed + host).distinct()) } },
                onRemove = { host -> set { it.copy(allowed = it.allowed - host) } })
        }
        SaveBar(d.dirty, onDiscard = d::clear) { save {} }
    }
}

@Composable
fun KeywordsEditor(onClose: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val guard = rememberGuard()
    val cs = MaterialTheme.colorScheme
    // A draft, saved with Save: one challenge when anything goes off, a word is removed or an exception added.
    val d = rememberDraft(cfg.keywords)
    val kw = d.value
    fun setKw(block: (com.husarp.lockdown.engine.KeywordsCfg) -> com.husarp.lockdown.engine.KeywordsCfg) = d.update { old ->
        block(old).let { it.copy(off = inSavedOrder(d.saved.off, it.off), words = inSavedOrder(d.saved.words, it.words),
            exceptions = inSavedOrder(d.saved.exceptions, it.exceptions)) }
    }
    fun save(then: () -> Unit) {
        val new = d.value
        guard(AntiBypass.keywordsLooser(Store.config.keywords, new)) {
            Store.update { it.copy(keywords = new) }
            d.clear()
            then()
        }
    }
    UnsavedChanges(d.dirty, onClose, ::save, d::clear)

    var addWord by remember { mutableStateOf("") }

    Column(Modifier.fillMaxSize()) {
        EditorHeader("Blocked words", onBack = { Unsaved.leave(onClose) })
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Card(color = cs.surfaceContainerLow) {
                SwitchRowInline("Block words in browsers", kw.enabled) { on -> setKw { it.copy(enabled = on) } }
                SwitchRowInline("Force SafeSearch", kw.safeSearch) { on -> setKw { it.copy(safeSearch = on) } }
                SwitchRowInline("YouTube Restricted Mode", kw.youtube) { on -> setKw { it.copy(youtube = on) } }
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
                    onToggle = { want -> setKw { it.copy(lists = it.lists + (key to want)) } },
                    words = pair.second, isActive = { w -> w !in kw.off },
                    onWord = { w, active -> setKw { if (active) it.copy(off = (it.off + w).distinct()) else it.copy(off = it.off - w) } },
                    canRemove = false, onRemove = {},
                )
            }
            // Your words: same collapsible shape, but editable (remove your own).
            CollapsibleWordList(
                title = "Your words", count = kw.words.size,
                on = kw.wordsOn,
                onToggle = { want -> setKw { it.copy(wordsOn = want) } },
                words = kw.words, isActive = { true }, onWord = { _, _ -> },
                canRemove = true, onRemove = { w -> setKw { it.copy(words = it.words - w) } },
            )
            SectionLabel("Exceptions (a site or a word that's never blocked)")
            ExceptionList(kw.exceptions, clean = com.husarp.lockdown.engine.Sites::cleanIfSite,
                onAdd = { w -> setKw { it.copy(exceptions = (it.exceptions + w).distinct()) } },
                onRemove = { w -> setKw { it.copy(exceptions = it.exceptions - w) } })
        }
        SaveBar(d.dirty, onDiscard = d::clear) { save {} }
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
private fun ExceptionList(values: List<String>, clean: (String) -> String, onAdd: (String) -> Unit, onRemove: (String) -> Unit) {
    val cs = MaterialTheme.colorScheme
    var field by remember { mutableStateOf("") }
    Card(color = cs.surfaceContainerLow, padding = androidx.compose.foundation.layout.PaddingValues(vertical = 4.dp)) {
        values.forEach { v ->
            ListRow(title = v, trailing = { IconButton(onClick = { onRemove(v) }) { Icon(Icons.Filled.Close, "remove") } })
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(field, { field = clean(it) }, placeholder = { Text("Add…") }, singleLine = true, modifier = Modifier.weight(1f))
            TextButton(onClick = { val t = clean(field).trim(); if (t.isNotEmpty()) { onAdd(t); field = "" } }) { Text("Add") }
        }
    }
}
