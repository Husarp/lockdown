package com.husarp.lockdown.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.Alerts
import com.husarp.lockdown.engine.AlertsCfg
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.SwitchRowInline

/*
 * Settings > Notifications (PC Notifications page). The block notice is the phone's blocked-visit alert: per reason it
 * explains why (or is a quiet notice that goes by itself), at most once per item within the cooldown, in your own
 * words if you like. Then the warnings before a block starts and the "block started" notices. Nothing here loosens a
 * block, so nothing asks for the challenge. In the config, so the Island copy has it too.
 */

private fun setAlerts(f: (AlertsCfg) -> AlertsCfg) = Store.update { it.copy(alerts = f(it.alerts)) }

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun NotificationsCard() {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val a = cfg.alerts
    var messages by remember { mutableStateOf(false) }
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    Card {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Notifications", style = MaterialTheme.typography.titleSmall)
            Text("The block notice says why when something is:", style = MaterialTheme.typography.bodySmall, color = muted)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Alerts.REASONS.forEach { (key, names) ->
                    val on = Alerts.enabled(a, key)
                    Chip(names.first, on) { setAlerts { it.copy(visits = it.visits + (key to !on)) } }
                }
            }
            Text("Otherwise it's a quiet notice: the block is the same, without the reason. Each app or site can choose for itself in its editor.",
                style = MaterialTheme.typography.bodySmall, color = muted)
            Text("Don't explain the same one again within", style = MaterialTheme.typography.bodyMedium)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Alerts.COOLDOWN_OPTIONS.forEach { m -> Chip("$m min", a.cooldownMin == m) { setAlerts { it.copy(cooldownMin = m) } } }
            }
            TextButton(onClick = { messages = !messages }) { Text(if (messages) "Hide messages" else "Messages: change what each one says") }
            if (messages) {
                Alerts.REASONS.forEach { (key, names) ->
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(
                            value = a.messages[key] ?: Alerts.DEFAULT_MESSAGES[key]!!,
                            onValueChange = { v -> setAlerts { it.copy(messages = it.messages + (key to v)) } },
                            label = { Text(names.first) }, modifier = Modifier.weight(1f),
                        )
                        TextButton(onClick = { setAlerts { it.copy(messages = it.messages - key) } }) { Text("Reset") }
                    }
                }
                Text("Placeholders: {site}  {reason}  {until}. Left as it is, the notice keeps its usual words.",
                    style = MaterialTheme.typography.bodySmall, color = muted)
            }

            SwitchRowInline("Warn me before something gets blocked", a.warn) { on -> setAlerts { it.copy(warn = on) } }
            if (a.warn) {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Alerts.WARN_MINUTE_OPTIONS.forEach { m -> Chip("$m min", a.warnMin == m) { setAlerts { it.copy(warnMin = m) } } }
                }
                Text("While I'm using it, remind me again every", style = MaterialTheme.typography.bodyMedium)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Alerts.REPEAT_OPTIONS.forEach { m ->
                        Chip(if (m == 0) "Never" else "$m min", a.repeatMin == m) { setAlerts { it.copy(repeatMin = m) } }
                    }
                }
            }
            SwitchRowInline("Notify me when a block starts", a.started) { on -> setAlerts { it.copy(started = on) } }
            Text("Blocks from the same group come as one (\"Evenings starts in 5 min: ...\"). Pause my blocks with Silence alerts holds them; a mode that mutes lets through only what's about the app in front.",
                style = MaterialTheme.typography.bodySmall, color = muted)
        }
    }
}

/** The item editor's "Block notice": follow Settings > Notifications, always explain, or always quiet. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun NotifyChips(notify: String?, onChange: (String?) -> Unit) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        listOf(null to "As in Settings", "on" to "Say why", "off" to "Quiet").forEach { (v, label) ->
            Chip(label, notify == v) { onChange(v) }
        }
    }
}
