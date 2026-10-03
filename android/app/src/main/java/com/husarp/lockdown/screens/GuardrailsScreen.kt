package com.husarp.lockdown.screens

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.PowerManager
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
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
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.AntiBypassCfg
import com.husarp.lockdown.engine.Emergency
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.guard.AdminReceiver
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.guard.rememberGuard
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.SectionLabel
import com.husarp.lockdown.ui.SwitchRowInline
import kotlinx.coroutines.delay
import java.time.Duration
import java.time.LocalDateTime

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
        guard(loosening) { Store.update { it.copy(antibypass = newAb) } }
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
                Chip("Only in hours", cfg.antibypass.hours) { setAb(cfg.antibypass.copy(hours = !cfg.antibypass.hours)) }
                Chip("Cool-off", cfg.antibypass.waitMin > 0) { setAb(cfg.antibypass.copy(waitMin = if (cfg.antibypass.waitMin > 0) 0 else 5)) }
            }
            if (cfg.antibypass.phrase) {
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Chip("Table writing", cfg.antibypass.grid) { setAb(cfg.antibypass.copy(grid = !cfg.antibypass.grid)) }
                }
                Text(
                    "Random phrase, ${cfg.antibypass.length} characters" +
                        if (cfg.antibypass.grid) " — typed word-by-word into a 3×3 grid (no paste/macros)" else "",
                    Modifier.padding(top = 10.dp), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant,
                )
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
        }

        SectionLabel("Switches")
        Card(color = cs.surfaceContainerLow) {
            SwitchRowInline("Keep running (restart on kill/boot)", cfg.guardrails.persist) { on ->
                if (on) Store.update { it.copy(guardrails = it.guardrails.copy(persist = true)) }
                else guard(true) { Store.update { it.copy(guardrails = it.guardrails.copy(persist = false)) } }
            }
            SwitchRowInline("Trusted time (ignore clock set-backs)", cfg.guardrails.trustedTime) { on ->
                if (on) Store.update { it.copy(guardrails = it.guardrails.copy(trustedTime = true)) }
                else guard(true) { Store.update { it.copy(guardrails = it.guardrails.copy(trustedTime = false)) } }
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
            // Never a permanently blocked app (its own rule or a group's): the emergency can't free those (PC 0.84.7).
            val apps = remember(cfg.items, cfg.groups) {
                cfg.items.filter { it.type == ItemType.APP && !Rules.permanent(Rules.effectiveRules(it, cfg.groups)) }
            }
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text("Unblock an app for ${cfg.emergency.minutes} minutes (one use). Blocked sites, permanent blocks and protection lists stay on.")
                if (apps.isEmpty()) Text("No apps the emergency can unlock.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                apps.forEach { item ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = item.id in picked.value, onCheckedChange = { on -> picked.value = if (on) picked.value + item.id else picked.value - item.id })
                        Text(item.name)
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
