package com.husarp.lockdown.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.husarp.lockdown.ui.AppIcon
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.Numeral
import com.husarp.lockdown.ui.SectionLabel
import com.husarp.lockdown.ui.fmtDuration
import com.husarp.lockdown.usage.Usage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.time.LocalDate

private data class AppRow(val pkg: String, val label: String, val ms: Long)
private data class Bar(val label: String, val ms: Long)
private data class InsightData(val total: Long, val days: Int, val perApp: List<AppRow>, val bars: List<Bar>, val hourly: Boolean)

@Composable
fun InsightsScreen() {
    val ctx = LocalContext.current
    val cs = MaterialTheme.colorScheme
    var range by remember { mutableIntStateOf(0) }
    var selected by remember { mutableIntStateOf(-1) }
    val ranges = listOf("Today" to (0 to 1), "Yesterday" to (1 to 1), "7 days" to (0 to 7), "30 days" to (0 to 30))

    val data by produceState<InsightData?>(null, range) {
        selected = -1
        // Each piece is guarded on its own so one failing query can't blank the whole screen (which showed a
        // permanent "Loading…" and was the "7/30 days doesn't work" bug). Always returns a non-null result.
        value = withContext(Dispatchers.IO) {
            val (back, len) = ranges[range].second
            val du = runCatching { Usage.range(ctx, Usage.dayBounds(back + len - 1).first, Usage.dayBounds(back).second) }.getOrNull()
            val apps = du?.apps?.take(6)?.map { AppRow(it.pkg, it.label.ifEmpty { it.pkg }, it.totalMs) } ?: emptyList()
            val hourly = len == 1
            val bars: List<Bar> = if (hourly) {
                (0..23).map { h -> Bar("%02d".format(h), du?.hourly?.getOrElse(h) { 0L } ?: 0L) }
            } else {
                val totals = runCatching { Usage.dailyTotals(ctx, len) }.getOrDefault(List(len) { 0L })
                val wd = listOf("M", "T", "W", "T", "F", "S", "S")
                totals.mapIndexed { i, ms ->
                    val date = LocalDate.now().minusDays((len - 1 - i).toLong())
                    val label = when { len <= 7 -> wd[date.dayOfWeek.value - 1]; date.dayOfMonth % 5 == 0 -> "${date.dayOfMonth}"; else -> "" }
                    Bar(label, ms)
                }
            }
            InsightData(du?.totalMs ?: 0L, len, apps, bars, hourly)
        }
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ranges.forEachIndexed { i, r -> Chip(r.first, i == range) { range = i } }
        }
        val d = data
        if (d == null) { Text("Loading…", color = cs.onSurfaceVariant) } else {
            Card(color = cs.surfaceContainerLow) {
                if (selected >= 0 && selected < d.bars.size) {
                    val b = d.bars[selected]
                    Text(if (d.hourly) "${b.label}:00–${b.label}:59" else "One day", style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
                    Numeral(fmtDuration(b.ms), sizeSp = 44)
                } else {
                    Text(if (d.days == 1) "Screen time" else "Daily average · ${d.days} days", style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
                    Numeral(fmtDuration(if (d.days > 0) d.total / d.days else d.total), sizeSp = 44)
                }
                Spacer(Modifier.height(16.dp))
                val max = (d.bars.maxOfOrNull { it.ms } ?: 1L).coerceAtLeast(1L)
                Row(Modifier.fillMaxWidth().height(120.dp), verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(if (d.hourly) 2.dp else 6.dp)) {
                    d.bars.forEachIndexed { i, b ->
                        Column(Modifier.weight(1f).clickable { selected = if (selected == i) -1 else i }, horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Bottom) {
                            val h = (100 * b.ms / max).toInt().dp.coerceAtLeast(if (b.ms > 0) 3.dp else 1.dp)
                            Box(Modifier.fillMaxWidth().height(h).clip(RoundedCornerShape(4.dp)).background(if (i == selected) cs.onPrimaryContainer else cs.primary))
                            if (b.label.isNotEmpty()) { Spacer(Modifier.height(6.dp)); Text(b.label, style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant) }
                        }
                    }
                }
            }
            Card(color = cs.surfaceContainerLow, padding = androidx.compose.foundation.layout.PaddingValues(vertical = 8.dp)) {
                SectionLabel("Top apps", Modifier.padding(horizontal = 16.dp, vertical = 4.dp))
                val topMax = (d.perApp.maxOfOrNull { it.ms } ?: 1L).coerceAtLeast(1L)
                d.perApp.forEach { a ->
                    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        AppIcon(a.pkg, a.label)
                        Column(Modifier.weight(1f)) {
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                Text(a.label, style = MaterialTheme.typography.bodyMedium)
                                Text(fmtDuration(a.ms), style = MaterialTheme.typography.titleSmall)
                            }
                            Spacer(Modifier.height(5.dp))
                            Box(Modifier.fillMaxWidth().height(4.dp).clip(CircleShape).background(cs.surfaceContainerHighest)) {
                                Box(Modifier.fillMaxWidth((a.ms.toFloat() / topMax).coerceIn(0.02f, 1f)).height(4.dp).clip(CircleShape).background(cs.primary))
                            }
                        }
                        CategoryMenu(a.pkg)
                    }
                }
                if (d.perApp.isEmpty()) Text("No usage yet. Grant usage access in Settings.", Modifier.padding(16.dp), color = cs.onSurfaceVariant)
                else Text("Mark an app Distracting and modes like Work and Focus block it.", Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
            val (back, len) = ranges[range].second
            InsightsHistory(back, len)
        }
    }
}
