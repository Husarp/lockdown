package com.husarp.lockdown.update

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.SystemUpdate
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.data.Store

private fun open(ctx: Context, url: String) {
    runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
}

/** Main-screen banner: the new version, UPDATE and ✕ (hides it until the app is next started). */
@Composable
fun UpdateBanner() {
    val ctx = LocalContext.current
    val s by Updates.state.collectAsStateWithLifecycle()
    val latest = s.latest
    if (latest == null || !Updates.bannerVisible(s, Updates.current(ctx))) return
    val cs = MaterialTheme.colorScheme
    com.husarp.lockdown.ui.Card(color = cs.secondaryContainer, contentColor = cs.onSecondaryContainer, padding = androidx.compose.foundation.layout.PaddingValues(start = 16.dp, top = 8.dp, bottom = 8.dp, end = 4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Icon(Icons.Filled.SystemUpdate, null)
            Column(Modifier.weight(1f)) {
                Text("Update available", style = MaterialTheme.typography.titleSmall)
                Text("Lockdown Mobile ${latest.version}", style = MaterialTheme.typography.bodySmall)
            }
            Button(onClick = { Updates.update(ctx) }, enabled = s.phase !is Updates.Phase.Downloading) { Text("UPDATE") }
            IconButton(onClick = { Updates.dismissBanner() }) { Icon(Icons.Filled.Close, "Close") }
        }
        UpdateProgress(s, Modifier.padding(end = 12.dp))
    }
}

/** Settings → Updates: auto-check switch, current version + CHECK NOW, GITHUB, GET UPDATE. */
@Composable
fun UpdateSection() {
    val ctx = LocalContext.current
    val cfg by Store.state.collectAsStateWithLifecycle()
    val s by Updates.state.collectAsStateWithLifecycle()
    val cur = Updates.current(ctx)
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    Card {
        Column(Modifier.padding(16.dp)) {
            Text("Updates", style = MaterialTheme.typography.titleSmall)
            Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Check for updates", style = MaterialTheme.typography.bodyMedium)
                    Text("When the app opens or you come back to it.", style = MaterialTheme.typography.bodySmall, color = muted)
                }
                Switch(checked = cfg.settings.checkUpdates, onCheckedChange = { on ->
                    Store.update { it.copy(settings = it.settings.copy(checkUpdates = on)) }
                    if (on) Updates.autoCheck(ctx)
                })
            }
            Row(Modifier.fillMaxWidth().padding(top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Version $cur", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                TextButton(onClick = { Updates.checkNow(ctx) }, enabled = !s.checking) { Text("CHECK NOW") }
            }
            s.message?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
            Row(Modifier.padding(top = 4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { open(ctx, Updates.RELEASES_PAGE) }) { Text("GITHUB") }
                if (Updates.available(s, cur)) {
                    Button(onClick = { Updates.update(ctx) }, enabled = s.phase !is Updates.Phase.Downloading) { Text("GET UPDATE") }
                }
            }
            UpdateProgress(s)
            Text("Android asks you to confirm every update, and only an update signed with Lockdown's own key installs.",
                style = MaterialTheme.typography.bodySmall, color = muted, modifier = Modifier.padding(top = 8.dp))
        }
    }
}

/** Download progress, the waiting-on-Android steps, and a failed update (TRY AGAIN + GITHUB). */
@Composable
private fun UpdateProgress(s: Updates.State, modifier: Modifier = Modifier) {
    val ctx = LocalContext.current
    Column(modifier.fillMaxWidth()) {
        when (val p = s.phase) {
            Updates.Phase.Idle -> {}
            is Updates.Phase.Downloading -> {
                Text("Downloading… ${p.percent}%", style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 8.dp, bottom = 4.dp))
                LinearProgressIndicator(progress = { p.percent / 100f }, modifier = Modifier.fillMaxWidth().height(6.dp))
            }
            Updates.Phase.NeedsPermission -> Text(
                "Allow \"Install unknown apps\" for Lockdown, then come back - the update carries on by itself.",
                style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 8.dp))
            Updates.Phase.Ready -> Text("Downloaded - Android's installer opens now.",
                style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 8.dp))
            Updates.Phase.Installing -> Text("Confirm the update in Android's window.",
                style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 8.dp))
            is Updates.Phase.Failed -> {
                Text("Update failed. ${p.why}", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(top = 8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = { Updates.update(ctx) }) { Text("TRY AGAIN") }
                    TextButton(onClick = { open(ctx, s.pageUrl) }) { Text("GITHUB") }
                }
            }
        }
    }
}
