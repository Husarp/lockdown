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
import androidx.compose.runtime.LaunchedEffect
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
import com.husarp.lockdown.data.SettingsEdit
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.LimitClock
import com.husarp.lockdown.engine.ResetCarry
import java.time.LocalTime
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Perm
import com.husarp.lockdown.engine.PermCheck
import com.husarp.lockdown.guard.Perms
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.ui.SaveBar
import com.husarp.lockdown.ui.UnsavedChanges
import com.husarp.lockdown.ui.rememberDraft
import com.husarp.lockdown.usage.Usage
import com.husarp.lockdown.vpn.LockdownVpn
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Composable
fun SettingsScreen(onBack: () -> Unit = {}) {
    val ctx = LocalContext.current
    val cfg by Store.state.collectAsStateWithLifecycle()
    val s = cfg.settings
    val scope = rememberCoroutineScope()
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
                val doms = linesOf(uri).flatMap { com.husarp.lockdown.engine.Sites.importLine(it) }
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
        if (text == null || Store.decodeImport(text, Store.config) == null) { importMsg = "Couldn't read that file."; return@rememberLauncherForActivityResult }
        // Replacing everything can drop any limit or block, so an import always asks for the challenge (free when no
        // challenge is set or the unlock window is open), like any other loosening.
        guard(true) {
            importMsg = if (Store.importJson(text)) "All settings imported." else "Couldn't read that file."
            com.husarp.lockdown.remind.Grayscale.sync(ctx)
        }
    }

    // Read again on every return to the app (and a moment after), so a permission just granted in Android shows.
    val fresh = com.husarp.lockdown.guard.rememberFresh()
    val a11y = remember(fresh) { Perms.a11y(ctx) }
    val usage = remember(fresh) { Usage.hasAccess(ctx) }
    val notes = remember(fresh) { Perms.notifications(ctx) }
    // Never Android's VpnService.prepare() here: when allowed before, it takes the VPN over from another app.
    val vpn = remember(fresh, s.siteFilterOn) { PermCheck.vpn(s.siteFilterOn, LockdownVpn.active) }
    val privateDns = remember(fresh, s.siteFilterOn) { if (s.siteFilterOn) runCatching { LockdownVpn.strictPrivateDns(ctx) }.getOrNull() else null }
    fun startVpn() = runCatching { LockdownVpn.prepare(ctx) }.fold(
        { i -> if (i != null) vpnConsent.launch(i) else LockdownVpn.start(ctx) },
        { android.widget.Toast.makeText(ctx, "Couldn't start the site filter: Android keeps the VPN for another app " +
            "(Always-on VPN or \"Block connections without VPN\").", android.widget.Toast.LENGTH_LONG).show() },
    )

    // The site filter, protection lists and extras are a draft (like the PC): nothing is in effect until Save, which
    // asks for the challenge once if anything in it loosens. Appearance, notifications and the actions act at once.
    val d = rememberDraft(SettingsEdit.of(cfg))
    val e = d.value
    fun save(then: () -> Unit) {
        val new = d.value
        val old = SettingsEdit.of(Store.config)
        guard(new.looser(old)) {
            Store.update { new.applyTo(it) }
            if (new.protection != old.protection) com.husarp.lockdown.block.Protection.load(ctx)
            if (new.bedtimeGrayscale != old.bedtimeGrayscale) {
                if (new.bedtimeGrayscale) com.husarp.lockdown.remind.Grayscale.sync(ctx) else com.husarp.lockdown.remind.Grayscale.switchedOff(ctx)
            }
            if (new.siteFilter != old.siteFilter) { if (new.siteFilter) startVpn() else LockdownVpn.stop(ctx) }
            d.clear()
            then()
        }
    }
    UnsavedChanges(d.dirty, onBack, ::save, d::clear)
    // the filter also starts and stops outside the draft (Start, another VPN taking over): a draft being edited follows it
    LaunchedEffect(s.siteFilterOn) { d.update { it.copy(siteFilter = s.siteFilterOn) } }

    Column(Modifier.fillMaxSize()) {
    Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
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

        ResetTimeCard()

        NotificationsCard()

        Card {
            Column(Modifier.padding(16.dp)) {
                Text("Permissions", style = MaterialTheme.typography.titleSmall)
                PermRow("App blocking (accessibility)", a11y == Perm.ON, if (a11y == Perm.NOT_RUNNING) A11Y_NOT_RUNNING else null) {
                    ctx.startActivity(BlockService.settingsIntent().addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                }
                PermRow("Usage access (screen time)", usage) { ctx.startActivity(Usage.accessIntent().addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
                PermRow("Notifications (reminders, block notices)", notes) { ctx.startActivity(Perms.notificationsIntent(ctx).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
                Row(Modifier.fillMaxWidth().padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("Site filter (VPN)", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                    Switch(checked = e.siteFilter, onCheckedChange = { on -> d.update { it.copy(siteFilter = on) } })
                }
                // switched on, but not running: say so, one tap starts it (asks Android's VPN permission again if needed)
                if (vpn == Perm.NOT_RUNNING) Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("On, but not running.",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error, modifier = Modifier.weight(1f))
                    TextButton(onClick = { startVpn() }) { Text("Start") }
                }
                // Private DNS set to a provider: sites don't load at all, or blocked ones do (the filter can't see them)
                if (privateDns != null) Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Private DNS is set to $privateDns. Set it to Automatic or Off, or sites may not load (or blocked ones may).",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error, modifier = Modifier.weight(1f))
                    TextButton(onClick = { runCatching { ctx.startActivity(Intent(android.provider.Settings.ACTION_WIRELESS_SETTINGS)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) } }) { Text("Fix") }
                }
                Text("Optional. Blocks sites everywhere via a local VPN, but can interfere with other apps. Leave it off — Lockdown still blocks sites and protection lists in the browser without it.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        IslandLinkCard()

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
            val onList = def.key in cfg.protection.enabled        // saved (and loaded); the switch shows the draft
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
                        Switch(checked = def.key in e.protection, onCheckedChange = { want ->
                            d.update { it.copy(protection = if (want) it.protection + def.key else it.protection - def.key) }
                        })
                    }
                }
            }
        }

        Text("Extras", style = MaterialTheme.typography.titleMedium)
        // A wait you set yourself: turning it off or shortening it needs the challenge, longer is free.
        Toggle("Pause before opening", "A ${e.pauseSec}-second countdown before a limited app opens.", e.pauseBeforeOpen) { on ->
            d.update { it.copy(pauseBeforeOpen = on) }
        }
        if (e.pauseBeforeOpen) Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("Countdown", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
            TextButton(onClick = { d.update { it.copy(pauseSec = (it.pauseSec - 5).coerceAtLeast(5)) } }, enabled = e.pauseSec > 5) { Text("−5") }
            Text("${e.pauseSec} s", style = MaterialTheme.typography.titleMedium)
            TextButton(onClick = { d.update { it.copy(pauseSec = (it.pauseSec + 5).coerceAtMost(60)) } }, enabled = e.pauseSec < 60) { Text("+5") }
        }
        Toggle("Bedtime grayscale", "Drain the screen's colour at bedtime. Needs a one-time adb permission.", e.bedtimeGrayscale) { on ->
            d.update { it.copy(bedtimeGrayscale = on) }
        }
        GrayscaleNote(e.bedtimeGrayscale)
        Toggle("Force SafeSearch", "Keep SafeSearch and YouTube Restricted Mode on through the filter.", e.forceSafeSearch) { on -> d.update { it.copy(forceSafeSearch = on) } }
        Toggle("Weekly digest", "A weekly notification with your screen time.", e.weeklyDigest) { on -> d.update { it.copy(weeklyDigest = on) } }
        Toggle("Network log", "Record DNS lookups the filter sees.", e.networkLog) { on -> d.update { it.copy(networkLog = on) } }
        Card {
            Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Daily screen-time goal", style = MaterialTheme.typography.titleSmall)
                    Text("A once-a-day nudge when you pass this many minutes (0 = off).",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                OutlinedTextField(
                    value = if (e.dailyGoalMin == 0) "" else e.dailyGoalMin.toString(),
                    onValueChange = { v -> d.update { it.copy(dailyGoalMin = v.filter(Char::isDigit).take(4).toIntOrNull() ?: 0) } },
                    singleLine = true,
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Number),
                    modifier = Modifier.width(90.dp),
                )
            }
        }

        com.husarp.lockdown.update.UpdateSection()
    }
    SaveBar(d.dirty, onDiscard = d::clear) { save {} }
    }
}

/** [note]: why it's not on although Android shows it allowed ("allowed but not running"); then the button is Fix. */
@Composable
private fun PermRow(label: String, ok: Boolean, note: String? = null, onFix: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(label, style = MaterialTheme.typography.bodyMedium)
            if (!ok && note != null) Text(note, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        }
        if (ok) Text("On", color = com.husarp.lockdown.ui.theme.LockdownTheme.extra.success, style = MaterialTheme.typography.labelLarge)
        else TextButton(onClick = onFix) { Text(if (note != null) "Fix" else "Grant") }
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

/**
 * "When limits reset" (PC Settings). A later time is free: it only makes the day you are in longer. An earlier one
 * asks which way: start a fresh limit day now (the challenge first, since today's limits start over) or let the new
 * time take over when the running day ends (free, nothing starts over). Kept in the config, so the Island copy
 * counts by the same day.
 */
@Composable
private fun ResetTimeCard() {
    val ctx = LocalContext.current
    val cfg by Store.state.collectAsStateWithLifecycle()
    val guard = com.husarp.lockdown.guard.rememberGuard()
    val cur = "%02d:%02d".format(cfg.resetHour, cfg.resetMin)
    var text by remember(cur) { mutableStateOf(cur) }
    var error by remember { mutableStateOf<String?>(null) }
    var ask by remember { mutableStateOf<LocalTime?>(null) }
    val now = com.husarp.lockdown.guard.TrustedTime.local(ctx)
    val end = cfg.clock().day(now).second

    fun save(t: LocalTime, carry: (LimitClock, java.time.LocalDateTime) -> ResetCarry) {
        val at = com.husarp.lockdown.guard.TrustedTime.local(ctx)
        Store.update { c -> c.copy(resetHour = t.hour, resetMin = t.minute, resetCarry = carry(c.clock(), at)) }
    }

    Card {
        Column(Modifier.padding(16.dp)) {
            Text("When limits reset", style = MaterialTheme.typography.titleSmall)
            Text("Time limits and opening limits start over at this time every day (weekly ones on Monday, monthly ones on the 1st, at the same time). Screen time keeps normal days.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                TimeField("Reset at", text, Modifier.width(120.dp)) { text = it; error = null }
                TextButton(onClick = {
                    val t = validTime(text)?.let { com.husarp.lockdown.engine.Rules.parseHhmm(it) }
                    when {
                        t == null -> error = "The time must look like 04:00."
                        t == cfg.resetTime() -> error = "That's already the reset time."
                        LimitClock.resetLooser(cfg.resetTime(), t) -> ask = t
                        else -> guard(LimitClock.crossesNoon(cfg.resetTime(), t)) { save(t) { clock, at -> LimitClock.change(clock, t, at) } }
                    }
                }) { Text("Change") }
            }
            error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
            Text("The limit day you are in ends ${whenText(end, now)}.", Modifier.padding(top = 4.dp),
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }

    ask?.let { t ->
        val endText = whenText(end, now)
        val noon = LimitClock.crossesNoon(cfg.resetTime(), t)   // weekdays shift: both ways ask
        androidx.compose.material3.AlertDialog(
            onDismissRequest = { ask = null },
            title = { Text("Move the reset back to %02d:%02d".format(t.hour, t.minute)) },
            text = {
                Text("The limit day you are in runs until $endText.\n\n" +
                    "Start now: it ends at once and today's limits start over. This asks for the challenge first.\n\n" +
                    "From $endText: the new time takes over when it ends, and nothing starts over." +
                    (if (noon) " Moving it across 12:00 asks for the challenge too, as the days shift onto other weekdays." else ""))
            },
            confirmButton = {
                TextButton(onClick = { ask = null; guard(true) { save(t) { clock, at -> LimitClock.startNow(clock, at) } } }) { Text("Start now") }
            },
            dismissButton = {
                Row {
                    TextButton(onClick = { ask = null }) { Text("Cancel") }
                    TextButton(onClick = { ask = null; guard(noon) { save(t) { clock, at -> LimitClock.change(clock, t, at) } } }) { Text("From $endText") }
                }
            },
        )
    }
}

/** "at 03:00", "tomorrow at 03:00" or "on Tue 6 at 03:00". */
private fun whenText(t: java.time.LocalDateTime, now: java.time.LocalDateTime): String {
    val hm = "%02d:%02d".format(t.hour, t.minute)
    return when (t.toLocalDate()) {
        now.toLocalDate() -> "at $hm"
        now.toLocalDate().plusDays(1) -> "tomorrow at $hm"
        else -> "on " + t.format(java.time.format.DateTimeFormatter.ofPattern("EEE d")) + " at $hm"
    }
}
