package com.husarp.lockdown.screens

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.PowerManager
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.IconButton
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.block.BlockService
import com.husarp.lockdown.data.EmergencyCfg
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.AntiBypassCfg
import com.husarp.lockdown.engine.Emergency
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Pause
import com.husarp.lockdown.engine.Window
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.guard.AdminReceiver
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.guard.rememberGuard
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.SectionLabel
import com.husarp.lockdown.ui.SwitchRowInline
import kotlinx.coroutines.delay
import com.husarp.lockdown.ui.HairlineSpacer
import java.time.Duration
import java.time.LocalDateTime

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun GuardrailsScreen() {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val guard = rememberGuard()
    val ctx = LocalContext.current
    val cs = MaterialTheme.colorScheme
    val ex = com.husarp.lockdown.ui.theme.LockdownTheme.extra
    var pickUnlock by remember { mutableStateOf(false) }
    var nowMs by remember { mutableStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) { while (true) { nowMs = System.currentTimeMillis(); delay(1000) } }
    val now = remember(nowMs) { TrustedTime.local(ctx) }     // trusted time: the clock set forward or back changes nothing
    val ab = cfg.antibypass

    fun setAb(newAb: AntiBypassCfg) {
        val loosening = AntiBypass.settingsLooser(cfg.antibypass, newAb)
        // keep the unlock window the challenge just opened (newAb was copied from before it)
        guard(loosening) { Store.update { it.copy(antibypass = newAb.copy(unlockedFrom = it.antibypass.unlockedFrom, unlockedUntil = it.antibypass.unlockedUntil)) } }
    }
    // Turning hours / cool-off on picks their settings first and saves both together (free): committing a default
    // first would make the very next pick a loosening, behind the challenge it just switched on.
    var pickHours by remember { mutableStateOf(false) }
    var pickWait by remember { mutableStateOf(false) }

    // Emergency settings: on, longer, more uses or per day instead of per week needs the challenge (PC Settings).
    fun setEm(new: EmergencyCfg) {
        val old = cfg.emergency
        guard(AntiBypass.emergencyLooser(old.enabled, old.minutes, old.uses, old.per, new.enabled, new.minutes, new.uses, new.per)) {
            Store.update { it.copy(emergency = new) }
        }
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        // Unlock box (like the PC): while the challenge is on, pass it once to open a short window where loosening
        // edits save without re-asking each time.
        if (AntiBypass.active(ab)) {
            val until = AntiBypass.unlockedUntil(ab, now)
            if (until != null) {
                val secs = Duration.between(now, until).seconds.coerceAtLeast(0)
                Card(color = ex.successContainer, contentColor = ex.onSuccessContainer) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("Unlocked for editing", style = MaterialTheme.typography.titleSmall)
                            Text("%d:%02d left — changes save without the challenge".format(secs / 60, secs % 60), style = MaterialTheme.typography.bodySmall)
                        }
                        OutlinedButton(onClick = { Store.update { it.copy(antibypass = AntiBypass.lock(it.antibypass)) } }) { Text("Lock now") }
                    }
                }
            } else {
                val status = AntiBypass.status(ab, now)
                Card(color = cs.primaryContainer, contentColor = cs.onPrimaryContainer) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("Locked", style = MaterialTheme.typography.titleSmall)
                            Text(if (status == "closed") "Only inside your allowed hours" else "Unlock to make changes without re-asking each time",
                                style = MaterialTheme.typography.bodySmall)
                        }
                        Button(onClick = { guard(true) {} }, enabled = status != "closed") { Text("Unlock") }
                    }
                }
            }
        }

        SectionLabel("Pause my blocks")
        PauseCard(now, guard)

        SectionLabel("Reliability")
        Card(color = cs.surfaceContainerLow, padding = androidx.compose.foundation.layout.PaddingValues(0.dp)) {
            HealthRow("App blocking active", BlockService.isEnabled(ctx)) { ctx.startActivity(BlockService.settingsIntent().addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
            HealthRow("Battery not restricted", batteryUnrestricted(ctx)) {
                ctx.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
            HealthRow("Uninstall protection", isAdmin(ctx)) { }
            if (cfg.settings.bedtimeGrayscale && !com.husarp.lockdown.remind.Grayscale.otherProfile(ctx))   // only an adb command can fix it - Fix copies it
                HealthRow("Grayscale permission", com.husarp.lockdown.remind.Grayscale.canWrite(ctx)) { copyGrant(ctx) }
            val link by com.husarp.lockdown.link.IslandLink.mainState.collectAsStateWithLifecycle()
            if (link.state.isNotEmpty() && !com.husarp.lockdown.remind.Grayscale.otherProfile(ctx))   // Fix opens Lockdown in Island
                HealthRow("Island helper checking in", com.husarp.lockdown.link.IslandLink.healthy(ctx)) { com.husarp.lockdown.link.IslandLink.openOtherProfile(ctx) }
        }

        SectionLabel("Before loosening anything")
        Card(color = cs.surfaceContainerLow) {
            Text("Tightening a rule never asks. Loosening needs:", style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Chip("Type a phrase", cfg.antibypass.phrase) { setAb(cfg.antibypass.copy(phrase = !cfg.antibypass.phrase)) }
                Chip("Only in hours", ab.hours) { if (ab.hours) setAb(ab.copy(hours = false)) else pickHours = !pickHours }
                Chip("Cool-off", ab.waitMin > 0) { if (ab.waitMin > 0) setAb(ab.copy(waitMin = 0)) else pickWait = !pickWait }
            }
            if (ab.phrase) {
                Spacer(Modifier.height(14.dp))
                if (ab.customPhrase.isEmpty()) {          // a random phrase's settings; your own phrase has none
                    Text("Phrase length", style = MaterialTheme.typography.titleSmall)
                    Spacer(Modifier.height(6.dp))
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        for ((label, n) in PHRASE_LENGTHS) Chip("$label · $n", ab.length == n) { setAb(ab.copy(length = n)) }
                    }
                    Spacer(Modifier.height(8.dp))
                }
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (ab.customPhrase.isEmpty()) Chip("Numbers and capitals", ab.complex) { setAb(ab.copy(complex = !ab.complex)) }
                    Chip("Table writing", ab.grid) { setAb(ab.copy(grid = !ab.grid)) }
                }
                Spacer(Modifier.height(10.dp))
                var custom by remember(ab.customPhrase) { mutableStateOf(ab.customPhrase) }
                val cleaned = custom.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(custom, { custom = it }, label = { Text("Your own phrase") },
                        placeholder = { Text("empty = a random one") }, modifier = Modifier.weight(1f))
                    TextButton(onClick = { setAb(ab.copy(customPhrase = cleaned)) }, enabled = cleaned != ab.customPhrase) { Text("Save") }
                }
                Text(
                    (if (ab.customPhrase.isNotEmpty()) "Your own phrase, typed exactly each time (no pasting). Setting or changing it needs the challenge."
                        else "A random phrase of ${ab.length} characters" + (if (ab.complex) ", with numbers and capitals" else "") + ".") +
                        if (ab.grid) " Typed word by word into a 3×3 grid (no paste/macros)." else "",
                    Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant,
                )
            }
            if (ab.waitMin > 0 || pickWait) {
                Spacer(Modifier.height(14.dp))
                Text(if (ab.waitMin > 0) "Cool-off" else "Cool-off - pick a length to turn it on", style = MaterialTheme.typography.titleSmall)
                Spacer(Modifier.height(6.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    for (m in (WAIT_OPTIONS + ab.waitMin).filter { it > 0 }.distinct().sorted())
                        Chip("$m min", ab.waitMin == m) { pickWait = false; setAb(ab.copy(waitMin = m)) }
                }
                Text("The change goes through this long after the phrase is right, with the app left open.",
                    Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
            if (ab.hours || pickHours) {
                Spacer(Modifier.height(14.dp))
                Text("Loosening allowed only", style = MaterialTheme.typography.titleSmall)
                HoursEditor(ab.windows, turnOn = !ab.hours) { pickHours = false; setAb(ab.copy(hours = true, windows = it)) }
            }
        }

        SectionLabel("Emergency unlock")
        Card(color = cs.surfaceContainer) {
            val uses = Emergency.usesLeft(cfg.unlocks.mapNotNull { runCatching { LocalDateTime.parse(it) }.getOrNull() }, now, cfg.emergency.per, cfg.emergency.uses, cfg.clock())
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.Bottom) {
                Text("Unblock chosen items for ${cfg.emergency.minutes} min", style = MaterialTheme.typography.titleSmall)
                Text("${uses.left} of ${uses.allowed} left", style = MaterialTheme.typography.titleMedium)
            }
            Spacer(Modifier.height(10.dp))
            OutlinedButton(onClick = { pickUnlock = true }, enabled = cfg.emergency.enabled && uses.left > 0) { Text("Use unlock") }
            Spacer(Modifier.height(14.dp))
            HairlineSpacer()
            Spacer(Modifier.height(8.dp))
            val em = cfg.emergency
            SwitchRowInline("Allow emergency unlocks", em.enabled) { setEm(em.copy(enabled = it)) }
            if (em.enabled) {
                Text("Length", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 8.dp, bottom = 6.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    for (m in (Emergency.MINUTE_OPTIONS + em.minutes).distinct().sorted()) Chip("$m min", em.minutes == m) { setEm(em.copy(minutes = m)) }
                }
                Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("Uses", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                    IconButton(onClick = { setEm(em.copy(uses = (em.uses - 1).coerceAtLeast(1))) }, enabled = em.uses > 1) {
                        Icon(Icons.Filled.Remove, "fewer") }
                    Text("${em.uses}", style = MaterialTheme.typography.titleMedium)
                    IconButton(onClick = { setEm(em.copy(uses = em.uses + 1)) }, enabled = em.uses < 10) {
                        Icon(Icons.Filled.Add, "more") }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Chip("per day", em.per == "day") { setEm(em.copy(per = "day")) }
                    Chip("per week", em.per == "week") { setEm(em.copy(per = "week")) }
                }
                Text("Shorter, fewer or per week saves at once. Longer, more, per day or turning it on needs the challenge.",
                    Modifier.padding(top = 6.dp), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
        }

        SectionLabel("Switches")
        Card(color = cs.surfaceContainerLow) {
            SwitchRowInline("Keep running (restart on kill/boot)", cfg.guardrails.persist) { on ->
                if (on) Store.update { it.copy(guardrails = it.guardrails.copy(persist = true)) }
                else guard(true) { Store.update { it.copy(guardrails = it.guardrails.copy(persist = false)) } }
            }
            SwitchRowInline("Trusted time (ignore clock changes)", cfg.guardrails.trustedTime) { on ->
                if (on) Store.update { it.copy(guardrails = it.guardrails.copy(trustedTime = true)) }
                else guard(true) { Store.update { it.copy(guardrails = it.guardrails.copy(trustedTime = false)) } }
            }
            TrustedTime.zonePending()?.let { (new, kept) ->
                Text("Time zone changed to $new - Lockdown keeps $kept for 24 hours.",
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
            SwitchRowInline("Uninstall protection (device admin)", cfg.guardrails.uninstallProtection) { on ->
                if (on) {
                    Store.update { it.copy(guardrails = it.guardrails.copy(uninstallProtection = true)) }
                    ctx.startActivity(adminIntent(ctx).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                } else guard(true) {
                    runCatching { dpm(ctx).removeActiveAdmin(admin(ctx)) }
                    Store.update { it.copy(guardrails = it.guardrails.copy(uninstallProtection = false)) }
                }
            }
        }
    }

    if (pickUnlock) EmergencyDialog(onDismiss = { pickUnlock = false })
}

private val PHRASE_LENGTHS = listOf("Short" to 30, "Medium" to 60, "Long" to 120, "Very long" to 250)   // as the PC
private val WAIT_OPTIONS = listOf(1, 2, 5, 10, 15, 30, 60)

/**
 * Pause my blocks (PC 0.84.8): every block from your own list off for a while, then back by itself. Starting needs
 * the challenge; Resume blocking never does. It lives in the config, so the Island helper pauses with it.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun PauseCard(now: LocalDateTime, guard: (Boolean, () -> Unit) -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    val cs = MaterialTheme.colorScheme
    var length by remember { mutableStateOf("1 h") }
    var silent by remember { mutableStateOf(false) }
    Card(color = cs.surfaceContainer) {
        val running = Pause.state(cfg.pause, now)
        if (running != null) {
            Text("Your blocks are paused until ${hhmm(LocalDateTime.parse(running.until))}" +
                if (running.silent) " - alerts are silenced." else ".", style = MaterialTheme.typography.titleSmall, color = cs.primary)
            Spacer(Modifier.height(10.dp))
            Button(onClick = { resumeBlocks(ctx) }) { Text("Resume blocking") }
        } else {
            Text("Your blocks are on.", style = MaterialTheme.typography.titleSmall)
            Spacer(Modifier.height(8.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                for (label in Pause.DURATIONS.keys) Chip(label, length == label) { length = label }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = silent, onCheckedChange = { silent = it })
                Text("Silence alerts too (bedtime, breaks and reminders)", style = MaterialTheme.typography.bodyMedium)
            }
            OutlinedButton(onClick = {
                val minutes = Pause.DURATIONS[length]
                guard(true) {
                    val at = TrustedTime.local(ctx)        // the time it is once the challenge is done
                    Store.update { it.copy(pause = Pause.start(at, minutes, silent, it.resetTime())) }
                    if (silent) com.husarp.lockdown.remind.Grayscale.sync(ctx)
                }
            }) { Text("Pause my blocks") }
        }
        Text("Unblocks every app and site on your list for a while - hours, time limits, opening limits, modes, temporary " +
            "and permanent blocks. The protection lists, blocked words and SafeSearch keep working, and the time you use " +
            "still counts toward your limits. It ends by itself (Rest of the day: at the next reset time). Starting it " +
            "needs the challenge; resuming early never does. No emergency unlock is used.",
            Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
    }
}

/** The slim "Blocking paused until HH:MM · Resume now" banner over every page while a pause runs (PC 0.84.8). */
@Composable
fun PauseBanner() {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    var tick by remember { mutableStateOf(0) }
    LaunchedEffect(Unit) { while (true) { delay(5000); tick++ } }      // a pause that runs out takes the banner with it
    val until = remember(cfg.pause, tick) { Pause.until(cfg.pause, TrustedTime.local(ctx)) } ?: return
    val cs = MaterialTheme.colorScheme
    androidx.compose.material3.Surface(color = cs.primaryContainer, contentColor = cs.onPrimaryContainer, modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("Blocking paused until ${hhmm(until)}", style = MaterialTheme.typography.labelLarge, modifier = Modifier.weight(1f))
            TextButton(onClick = { resumeBlocks(ctx) }) { Text("Resume now") }
        }
    }
}

/** "Resume now": blocking back on at once - always free. */
fun resumeBlocks(ctx: Context) {
    Store.update { it.copy(pause = null) }
    com.husarp.lockdown.remind.Grayscale.sync(ctx)
}

private fun hhmm(t: LocalDateTime) = "%02d:%02d".format(t.hour, t.minute)

/** The challenge's "only in hours" windows: edited here, saved with Save hours (any change of the hours, or no
 *  longer needing them, is loosening - one challenge for the whole edit rather than one per field). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun HoursEditor(saved: List<Window>, turnOn: Boolean = false, onSave: (List<Window>) -> Unit) {
    var draft by remember(saved) { mutableStateOf(saved.ifEmpty { listOf(Window(listOf(6), "18:00", "20:00")) }) }
    fun set(i: Int, w: Window?) { draft = draft.toMutableList().also { if (w == null) it.removeAt(i) else it[i] = w } }
    val days = listOf("M", "T", "W", "T", "F", "S", "S")
    draft.forEachIndexed { i, w ->
        Spacer(Modifier.height(10.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            days.forEachIndexed { d, l -> Chip(l, d in w.days) { set(i, w.copy(days = if (d in w.days) w.days - d else (w.days + d).sorted())) } }
        }
        Spacer(Modifier.height(6.dp))
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            TimeField("From", w.start, Modifier.weight(1f)) { set(i, w.copy(start = it)) }
            TimeField("To", w.end, Modifier.weight(1f)) { set(i, w.copy(end = it)) }
            if (draft.size > 1) IconButton(onClick = { set(i, null) }) { Icon(Icons.Filled.Close, "remove these hours") }
        }
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        TextButton(onClick = { draft = draft + Window(listOf(6), "18:00", "20:00") }) { Text("+ Add time window") }
        TextButton(onClick = { onSave(draft) }, enabled = (turnOn || draft != saved) && draft.all { it.days.isNotEmpty() }) {
            Text(if (turnOn) "Turn on with these hours" else "Save hours") }
    }
}

@Composable
private fun EmergencyDialog(onDismiss: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    val picked = remember { mutableStateOf(setOf<String>()) }
    var alerts by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Emergency unlock") },
        text = {
            // Never a permanently blocked app or site (its own rule or a group's): the emergency can't free those (PC 0.84.7).
            val items = remember(cfg.items, cfg.groups) {
                cfg.items.filter { !it.disabled && !Rules.permanent(Rules.effectiveRules(it, cfg.groups)) }
                    .sortedWith(compareBy({ it.type != ItemType.APP }, { it.name.lowercase() }))
            }
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text("Unblock apps or sites for ${cfg.emergency.minutes} minutes (one use, however many you tick). " +
                    "Permanent blocks and protection lists stay on. Time you use still counts toward your limits.")
                if (items.isEmpty()) Text("Nothing the emergency can unlock.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                items.forEach { item ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = item.id in picked.value, onCheckedChange = { on -> picked.value = if (on) picked.value + item.id else picked.value - item.id })
                        Text(if (item.type == ItemType.SITE) "${item.name} · site" else item.name)
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = alerts, onCheckedChange = { alerts = it })
                    Text("Pause bedtime and break alerts (and bedtime grayscale)")
                }
            }
        },
        confirmButton = {
            TextButton(enabled = picked.value.isNotEmpty() || alerts, onClick = {
                val now = TrustedTime.local(ctx)                  // trusted time: a clock set forward gives no longer unlock
                if (com.husarp.lockdown.remind.ReminderRunner.emergencyLeft(Store.config, now) <= 0) { onDismiss(); return@TextButton }   // spent meanwhile
                val until = now.plusMinutes(cfg.emergency.minutes.toLong())
                Store.update {
                    var c = it.copy(unlocks = it.unlocks + now.toString())   // items and alerts together: one use
                    if (picked.value.isNotEmpty()) c = c.copy(unlockUntil = until.toString(), unlockItems = picked.value.toList())
                    if (alerts) c = c.copy(alertsPausedUntil = until.toString())
                    c
                }
                if (alerts) com.husarp.lockdown.remind.Grayscale.sync(ctx)
                onDismiss()
            }) { Text("Unlock") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun HealthRow(label: String, ok: Boolean, onFix: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    val ex = com.husarp.lockdown.ui.theme.LockdownTheme.extra
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Icon(if (ok) Icons.Filled.Check else Icons.Filled.Close, null, tint = if (ok) ex.success else cs.error, modifier = Modifier.size(20.dp))
        Text(label, style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
        if (!ok) TextButton(onClick = onFix) { Text("Fix") }
    }
}

private fun batteryUnrestricted(ctx: Context) =
    (ctx.getSystemService(Context.POWER_SERVICE) as PowerManager).isIgnoringBatteryOptimizations(ctx.packageName)

private fun dpm(ctx: Context) = ctx.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
private fun admin(ctx: Context) = ComponentName(ctx, AdminReceiver::class.java)
private fun isAdmin(ctx: Context) = dpm(ctx).isAdminActive(admin(ctx))
private fun adminIntent(ctx: Context) = Intent(DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN)
    .putExtra(DevicePolicyManager.EXTRA_DEVICE_ADMIN, admin(ctx))
    .putExtra(DevicePolicyManager.EXTRA_ADD_EXPLANATION, "Lockdown uses device admin so it can't be uninstalled while a block is active.")
