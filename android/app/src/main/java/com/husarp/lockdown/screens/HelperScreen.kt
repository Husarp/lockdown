package com.husarp.lockdown.screens

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.app.NotificationManagerCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.block.BlockService
import com.husarp.lockdown.link.IslandLink
import com.husarp.lockdown.link.LinkService
import com.husarp.lockdown.usage.Usage
import kotlinx.coroutines.delay

/** Lockdown in Island while linked: no editing here (main holds the rules), just status and what it needs. */
@Composable
fun HelperScreen() {
    val ctx = LocalContext.current
    val st by IslandLink.status.collectAsStateWithLifecycle()
    val cs = MaterialTheme.colorScheme
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    var newCode by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        runCatching { LinkService.start(ctx) }
        IslandLink.kick()
        while (true) { now = System.currentTimeMillis(); delay(1000) }
    }
    val mine = remember { IslandLink.versionCode(ctx) }

    Surface(Modifier.fillMaxSize()) {
        Column(Modifier.systemBarsPadding().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Managed by your main Lockdown", style = MaterialTheme.typography.headlineSmall)
            Text("Rules, limits, modes and keywords are set in Lockdown in your main profile. Time used here counts toward the same limits.",
                style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)

            Card {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    val (text, bad) = when {
                        st.problem == "version" -> "The two copies can't talk: update Lockdown in both profiles. Still blocking with the last rules." to true
                        st.problem == "denied" -> "Your main Lockdown doesn't know this link any more (reinstalled or reset there?). Still blocking with the last rules. Enter a new link code from it." to true
                        st.problem != null && st.lastOk > 0 -> "Can't reach your main Lockdown since ${hhmm(st.lastOk)}. Still blocking with the last rules." to true
                        st.problem != null -> "Can't reach your main Lockdown. Is it running? If you use AFWall, allow Lockdown in both profiles." to true
                        st.lastOk == 0L -> "Connecting to your main Lockdown…" to false
                        else -> "Up to date · ${agoText((now - st.lastOk) / 1000)}" to false
                    }
                    Text(text, style = MaterialTheme.typography.bodyMedium, color = if (bad) cs.error else cs.onSurface)
                    if (st.cfgError) Text("Couldn't read the latest rules: update Lockdown here. Still blocking with the earlier ones.",
                        style = MaterialTheme.typography.bodySmall, color = cs.error)
                    if (st.mainApp > mine) Text("Update Lockdown here: your main Lockdown is newer.", style = MaterialTheme.typography.bodySmall, color = cs.error)
                }
            }

            Card {
                Column(Modifier.padding(16.dp)) {
                    Text("Needed here", style = MaterialTheme.typography.titleSmall)
                    // read again each second (the clock above), so a permission granted in Settings shows on return
                    val a11y = remember(now) { BlockService.connected }     // really running, not just ticked
                    val overlay = remember(now) { Settings.canDrawOverlays(ctx) }
                    HelperRow("Usage access (sees the Island app in front)", remember(now) { Usage.hasAccess(ctx) }) { open(ctx, Usage.accessIntent()) }
                    HelperRow("Display over other apps (the block screen)", overlay || a11y) {
                        open(ctx, Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:${ctx.packageName}")))
                    }
                    HelperRow("Battery not restricted", remember(now) { ctx.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(ctx.packageName) }) {
                        open(ctx, Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
                    }
                    HelperRow("Notifications", remember(now) { NotificationManagerCompat.from(ctx).areNotificationsEnabled() }) {
                        open(ctx, Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, ctx.packageName))
                    }
                    HelperRow("App blocking (accessibility): optional, better if your phone allows it", a11y) { open(ctx, BlockService.settingsIntent()) }
                    if (!overlay && !a11y)
                        Text("Can't block in Island yet: allow Display over other apps.", style = MaterialTheme.typography.bodySmall, color = cs.error)
                }
            }

            Text("To unlink, open Lockdown in your main profile: Settings > Island > Unlink.", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            if (!newCode) TextButton(onClick = { newCode = true }) { Text("Enter a new link code") }
            else Card {
                Column(Modifier.padding(16.dp)) {
                    Text("Only needed when your main Lockdown was reinstalled or reset. Get a code there: Settings > Island > Link.",
                        style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                    LinkCodeField(challenge = true)
                }
            }
            com.husarp.lockdown.update.UpdateSection()
        }
    }
}

private fun open(ctx: Context, i: Intent) { runCatching { ctx.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) } }

@Composable
private fun HelperRow(label: String, ok: Boolean, onFix: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        if (ok) Text("On", color = com.husarp.lockdown.ui.theme.LockdownTheme.extra.success, style = MaterialTheme.typography.labelLarge)
        else TextButton(onClick = onFix) { Text("Grant") }
    }
}
