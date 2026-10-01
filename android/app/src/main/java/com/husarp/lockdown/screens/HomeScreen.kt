package com.husarp.lockdown.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bedtime
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Layers
import androidx.compose.material.icons.outlined.Block
import androidx.compose.material.icons.outlined.Warning
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.data.Live
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.Modes
import com.husarp.lockdown.engine.RuleType
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import com.husarp.lockdown.guard.rememberGuard
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.Numeral
import com.husarp.lockdown.ui.SectionLabel
import com.husarp.lockdown.ui.Status
import com.husarp.lockdown.ui.fmtDuration
import com.husarp.lockdown.usage.Usage
import java.time.LocalDateTime

@Composable
fun HomeScreen(onOpenBlocking: () -> Unit, onOpenModes: () -> Unit, onOpenGuardrails: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val guard = rememberGuard()
    val ctx = LocalContext.current
    val cs = MaterialTheme.colorScheme

    val todayMs by produceState(0L, cfg.enabled) {
        value = withContext(Dispatchers.IO) {
            runCatching { val (s, e) = Usage.dayBounds(0); Usage.range(ctx, s, e).totalMs }.getOrDefault(0L)
        }
    }
    val goalMin = cfg.settings.dailyGoalMin
    val now = LocalDateTime.now()
    val modeState = ModesStore.current(now)
    // Cheap prefilter (rule type only, no engine) before the per-item engine check, so thousands of imported
    // items don't freeze Home. Only items that actually have a limit can be "running low".
    val groupLimited = cfg.groups.filter { g -> g.rules.any { it.type == RuleType.TIME_LIMIT || it.type == RuleType.SWITCH_LIMIT } }.flatMap { it.memberIds }.toSet()
    val lowItems = cfg.items
        .filter { it.id in groupLimited || it.rules.any { r -> r.type == RuleType.TIME_LIMIT || r.type == RuleType.SWITCH_LIMIT } }
        .filter { Live.status(cfg, it, now) == Status.SOON }.take(5)

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        // Hero
        Card(color = cs.primaryContainer, contentColor = cs.onPrimaryContainer, shape = MaterialTheme.shapes.extraLarge) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(if (cfg.enabled) "PROTECTION ON" else "PROTECTION OFF",
                    style = MaterialTheme.typography.labelMedium, modifier = Modifier.weight(1f))
                Switch(checked = cfg.enabled, onCheckedChange = { on ->
                    if (on) Store.update { it.copy(enabled = true) }
                    else guard(true) { Store.update { it.copy(enabled = false) } }
                })
            }
            Spacer(Modifier.height(6.dp))
            Row(verticalAlignment = Alignment.Bottom) {
                Numeral(fmtDuration(todayMs), sizeSp = 52, color = cs.onPrimaryContainer)
                Spacer(Modifier.width(6.dp))
                Text("today", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(bottom = 8.dp))
            }
            if (goalMin > 0) {
                Spacer(Modifier.height(12.dp))
                val frac = (todayMs.toFloat() / (goalMin * 60_000L)).coerceIn(0f, 1f)
                LinearProgressIndicator(progress = { frac }, modifier = Modifier.fillMaxWidth().height(8.dp),
                    color = cs.primary, trackColor = cs.onPrimaryContainer.copy(alpha = 0.15f))
                Spacer(Modifier.height(8.dp))
                val leftMs = (goalMin * 60_000L - todayMs).coerceAtLeast(0)
                Text("${fmtDuration(leftMs)} left of your ${goalMin / 60}h ${goalMin % 60}m goal",
                    style = MaterialTheme.typography.bodyMedium)
            }
        }

        // Active mode
        if (modeState != null) {
            Card(color = cs.surfaceContainerLow, onClick = onOpenModes) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    Icon(Icons.Filled.Layers, null, tint = cs.primary)
                    Column(Modifier.weight(1f)) {
                        Text("${modeState.mode.name} is on", style = MaterialTheme.typography.titleSmall)
                        val bits = buildList {
                            modeState.until?.let { add("Until %02d:%02d".format(it.hour, it.minute)) }
                            if (modeState.locked) add("locked")
                            modeState.phase?.let { add("${it.first} · round ${it.third}") }
                        }
                        Text(bits.joinToString(" · ").ifEmpty { Modes.describe(modeState.mode) },
                            style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                    }
                    if (modeState.locked) Icon(Icons.Filled.Lock, null, tint = cs.onSurfaceVariant)
                }
            }
        }

        // Running low
        if (lowItems.isNotEmpty()) {
            Card(color = cs.surfaceContainerLow, padding = androidx.compose.foundation.layout.PaddingValues(vertical = 8.dp)) {
                SectionLabel("Running low", Modifier.padding(horizontal = 16.dp, vertical = 4.dp))
                lowItems.forEach { item ->
                    val frac = Live.dayLimitFraction(cfg, item, now)
                    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Column(Modifier.weight(1f)) {
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                Text(item.name, style = MaterialTheme.typography.titleSmall)
                                if (frac != null) Text("${frac.second} min left",
                                    style = MaterialTheme.typography.titleSmall, color = com.husarp.lockdown.ui.theme.LockdownTheme.extra.warning)
                            }
                            if (frac != null) {
                                Spacer(Modifier.height(6.dp))
                                LinearProgressIndicator(progress = { frac.first }, modifier = Modifier.fillMaxWidth().height(4.dp),
                                    color = com.husarp.lockdown.ui.theme.LockdownTheme.extra.warning, trackColor = cs.surfaceContainerHighest)
                            }
                        }
                    }
                }
            }
        }

        // Quick actions
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Chip("Focus", selected = false, icon = Icons.Filled.Bolt) { onOpenModes() }
            Chip("Block a site", selected = false, icon = Icons.Outlined.Block) { onOpenBlocking() }
            Chip("Emergency", selected = false, icon = Icons.Outlined.Warning) { onOpenGuardrails() }
        }

        // Next reminder
        if (cfg.sleep.on) {
            Row(Modifier.padding(4.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Icon(Icons.Filled.Bedtime, null, tint = cs.onSurfaceVariant)
                Text("Bedtime at ${cfg.sleep.bedtime}", style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
            }
        }
    }
}
