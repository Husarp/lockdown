package com.husarp.lockdown.screens

import android.content.Intent
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.block.BlockService
import com.husarp.lockdown.data.Settings
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.update.Updates
import com.husarp.lockdown.usage.Usage
import com.husarp.lockdown.vpn.LockdownVpn
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Composable
fun SettingsScreen() {
    val ctx = LocalContext.current
    val cfg by Store.state.collectAsStateWithLifecycle()
    val s = cfg.settings
    val scope = rememberCoroutineScope()
    var updateMsg by remember { mutableStateOf<String?>(null) }
    var importMsg by remember { mutableStateOf<String?>(null) }

    fun set(block: (Settings) -> Settings) = Store.update { it.copy(settings = block(it.settings)) }
    val guard = com.husarp.lockdown.guard.rememberGuard()
    val vpnConsent = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
        if (res.resultCode == android.app.Activity.RESULT_OK) LockdownVpn.start(ctx)
    }

    fun linesOf(uri: Uri): List<String> =
        runCatching {
            ctx.contentResolver.openInputStream(uri)!!.bufferedReader().readLines()
                .map { it.trim().lowercase() }.filter { it.isNotEmpty() && !it.startsWith("#") }
        }.getOrDefault(emptyList())

    // Import parses the file, dedups against thousands of existing items, and re-serialises a multi-MB config -
    // all off the main thread so a big list never stalls the UI.
    val importSites = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        importMsg = "Importing sites…"
        scope.launch {
            val added = withContext(Dispatchers.IO) {
                val doms = linesOf(uri).map { it.removePrefix("https://").removePrefix("http://").removePrefix("www.").substringBefore("/") }
                    .filter { it.contains(".") }
                var n = 0
                Store.update { c ->
                    val have = c.items.filter { it.type == ItemType.SITE }.flatMapTo(HashSet()) { it.target.split(" ") }
                    val new = doms.asSequence().filter { it !in have }.distinct()
                        .map { Item(UUID.randomUUID().toString().take(8), it, it, ItemType.SITE, rules = listOf(Rule(RuleType.PERMANENT))) }.toList()
                    n = new.size
                    c.copy(items = c.items + new)
                }
                n
            }
            importMsg = "Added $added sites."
        }
    }
    val importWords = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        importMsg = "Importing keywords…"
        scope.launch {
            val added = withContext(Dispatchers.IO) {
                val words = linesOf(uri)
                var n = 0
                Store.update { c ->
                    val have = c.keywords.words.toHashSet()
                    val new = words.filter { it !in have }.distinct()
                    n = new.size
                    c.copy(keywords = c.keywords.copy(words = c.keywords.words + new))
                }
                n
            }
            importMsg = "Added $added keywords."
        }
    }
    val exportCfg = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/json")) { uri ->
        if (uri != null) {
            runCatching { ctx.contentResolver.openOutputStream(uri)!!.bufferedWriter().use { it.write(Store.exportJson()) } }
            importMsg = "All settings exported."
        }
    }
    val importCfg = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        val text = runCatching { ctx.contentResolver.openInputStream(uri)!!.bufferedReader().readText() }.getOrNull()
        importMsg = if (text != null && Store.importJson(text)) "All settings imported." else "Couldn't read that file."
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Card {
            Column(Modifier.padding(16.dp)) {
                Text("Appearance", style = MaterialTheme.typography.titleSmall)
                Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf("system" to "System", "light" to "Light", "dark" to "Dark").forEach { (v, label) ->
                        FilterChip(selected = s.theme == v, onClick = { set { it.copy(theme = v) } }, label = { Text(label) })
                    }
                }
            }
        }

        Card {
            Column(Modifier.padding(16.dp)) {
                Text("Permissions", style = MaterialTheme.typography.titleSmall)
                PermRow("App blocking (accessibility)", BlockService.isEnabled(ctx)) { ctx.startActivity(BlockService.settingsIntent().addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
                PermRow("Usage access (screen time)", Usage.hasAccess(ctx)) { ctx.startActivity(Usage.accessIntent().addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
                Row(Modifier.fillMaxWidth().padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("Site filter (VPN)", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                    Switch(checked = s.siteFilterOn, onCheckedChange = { on ->
                        if (on) { val i = LockdownVpn.prepare(ctx); if (i != null) vpnConsent.launch(i) else LockdownVpn.start(ctx) }
                        else guard(true) { LockdownVpn.stop(ctx) }
                    })
                }
                Text("Optional. Blocks sites everywhere via a local VPN, but can interfere with other apps. Leave it off — Lockdown still blocks sites and protection lists in the browser without it.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        Card {
            Column(Modifier.padding(16.dp)) {
                Text("Import from Lockdown", style = MaterialTheme.typography.titleSmall)
                Text(
                    "Load a .txt list (one domain or keyword per line) - e.g. the lists exported from Lockdown on the PC.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Row {
                    TextButton(onClick = { importSites.launch("text/*") }) { Text("Import sites") }
                    TextButton(onClick = { importWords.launch("text/*") }) { Text("Import keywords") }
                }
                Text(
                    "Sync everything to your work profile: Export here, then Import in the other profile's app.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 4.dp),
                )
                Row {
                    TextButton(onClick = { exportCfg.launch("lockdown-config.json") }) { Text("Export all settings") }
                    TextButton(onClick = { importCfg.launch("application/json") }) { Text("Import all settings") }
                }
                importMsg?.let { Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.primary) }
            }
        }

        Text("Protection lists", style = MaterialTheme.typography.titleMedium)
        Text(
            "Bundled blocklists, built into the app - just turn one on (no download). You can fetch the full list later.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        com.husarp.lockdown.block.Protection.LISTS.forEach { def ->
            val onList = def.key in cfg.protection.enabled
            val info = remember(def.key, onList, importMsg) { com.husarp.lockdown.block.Protection.info(ctx, def.key) }
            Card {
                Column(Modifier.fillMaxWidth().padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(def.name, style = MaterialTheme.typography.titleSmall)
                            Text(
                                def.desc + " · ${info.first} domains",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Switch(checked = onList, onCheckedChange = { want ->
                            Store.update {
                                it.copy(protection = it.protection.copy(
                                    enabled = if (want) (it.protection.enabled + def.key).distinct() else it.protection.enabled - def.key))
                            }
                            com.husarp.lockdown.block.Protection.load(ctx)
                        })
                    }
                }
            }
        }

        Text("Extras", style = MaterialTheme.typography.titleMedium)
        Toggle("Pause before opening", "A short countdown before a limited app opens.", s.pauseBeforeOpen) { set { c -> c.copy(pauseBeforeOpen = it) } }
        Toggle("Daily open cap", "Limit how many times a blocked app can be opened (set per app; 0 = off).", s.openCapPerDay > 0) { set { c -> c.copy(openCapPerDay = if (it) 10 else 0) } }
        Toggle("Bedtime grayscale", "Drain the screen's colour at bedtime. Needs a one-time adb permission.", s.bedtimeGrayscale) { set { c -> c.copy(bedtimeGrayscale = it) } }
        Toggle("Force SafeSearch", "Keep SafeSearch and YouTube Restricted Mode on through the filter.", s.forceSafeSearch) { set { c -> c.copy(forceSafeSearch = it) } }
        Toggle("Weekly digest", "A weekly notification with your screen time.", s.weeklyDigest) { set { c -> c.copy(weeklyDigest = it) } }
        Toggle("Network log", "Record DNS lookups the filter sees.", s.networkLog) { set { c -> c.copy(networkLog = it) } }
        Card {
            Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Daily screen-time goal", style = MaterialTheme.typography.titleSmall)
                    Text("A once-a-day nudge when you pass this many minutes (0 = off).",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                OutlinedTextField(
                    value = if (s.dailyGoalMin == 0) "" else s.dailyGoalMin.toString(),
                    onValueChange = { v -> set { c -> c.copy(dailyGoalMin = v.filter(Char::isDigit).take(4).toIntOrNull() ?: 0) } },
                    singleLine = true,
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Number),
                    modifier = Modifier.width(90.dp),
                )
            }
        }

        Card {
            Column(Modifier.padding(16.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Updates", style = MaterialTheme.typography.titleSmall)
                        Text("Version ${Updates.current(ctx)}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Switch(checked = s.checkUpdates, onCheckedChange = { set { c -> c.copy(checkUpdates = it) } })
                }
                updateMsg?.let { Text(it, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 6.dp)) }
                TextButton(onClick = {
                    updateMsg = "Checking…"
                    scope.launch {
                        val rel = withContext(Dispatchers.IO) { Updates.latest() }
                        updateMsg = when {
                            rel == null -> "Couldn't check right now."
                            Updates.isNewer(rel.tag, Updates.current(ctx)) -> {
                                ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(rel.url)))
                                "Update ${rel.tag} available - opening the release page."
                            }
                            else -> "You're up to date."
                        }
                    }
                }) { Text("Check now") }
            }
        }
    }
}

@Composable
private fun PermRow(label: String, ok: Boolean, onFix: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        if (ok) Text("On", color = com.husarp.lockdown.ui.theme.LockdownTheme.extra.success, style = MaterialTheme.typography.labelLarge)
        else TextButton(onClick = onFix) { Text("Grant") }
    }
}

@Composable
private fun Toggle(title: String, blurb: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Card {
        Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleSmall)
                Text(blurb, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Switch(checked = checked, onCheckedChange = onChange)
        }
    }
}
