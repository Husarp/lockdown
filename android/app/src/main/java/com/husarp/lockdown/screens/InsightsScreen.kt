package com.husarp.lockdown.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.wrapContentWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.History
import androidx.compose.ui.text.style.TextOverflow
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.SiteTop
import com.husarp.lockdown.engine.AppList
import com.husarp.lockdown.engine.AppStat
import com.husarp.lockdown.engine.ChartAxis
import com.husarp.lockdown.ui.AppIcon
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.ItemIcon
import com.husarp.lockdown.ui.SectionCard
import com.husarp.lockdown.ui.Numeral
import com.husarp.lockdown.ui.SectionLabel
import com.husarp.lockdown.ui.fmtDuration
import com.husarp.lockdown.ui.Kinds
import com.husarp.lockdown.ui.theme.Hue
import com.husarp.lockdown.ui.theme.tone
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.usage.Usage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

private data class Bar(val label: String, val ms: Long)
private data class InsightData(val total: Long, val days: Int, val perApp: List<AppStat>, val bars: List<Bar>, val hourly: Boolean, val islandNotInBars: Long = 0L)

@Composable
fun InsightsScreen(onOpenActivity: () -> Unit = {}, onOpenApps: (range: Int) -> Unit = {}) {
    val ctx = LocalContext.current
    val cs = MaterialTheme.colorScheme
    var range by remember { mutableIntStateOf(0) }
    var selected by remember { mutableIntStateOf(-1) }
    val ranges = listOf("Today" to (0 to 1), "Yesterday" to (1 to 1), "7 days" to (0 to 7), "30 days" to (0 to 30))
    val cfg by com.husarp.lockdown.data.Store.state.collectAsStateWithLifecycle()
    val teal = tone(Hue.TEAL)

    val showSites = cfg.settings.sitesInsteadOfBrowsers
    val data by produceState<InsightData?>(null, range, showSites) {
        selected = -1
        // Each piece is guarded on its own so one failing query can't blank the whole screen (which showed a
        // permanent "Loading…" and was the "7/30 days doesn't work" bug). Always returns a non-null result.
        value = withContext(Dispatchers.IO) {
            val (back, len) = ranges[range].second
            val du = runCatching { Usage.range(ctx, Usage.dayBounds(ctx, back + len - 1).first, Usage.dayBounds(ctx, back).second) }.getOrNull()
            // Island apps count too: the helper's per-app time (over the link) is merged into main's by package
            val today = com.husarp.lockdown.guard.TrustedTime.local(ctx).toLocalDate()    // trusted time's day (as the limits)
            val island = runCatching { com.husarp.lockdown.link.IslandLink.islandUse(ctx, today.minusDays(back + len - 1L), today.minusDays(back.toLong())) }
                .getOrDefault(emptyMap())
            val islandDays = com.husarp.lockdown.engine.DayBounds.days(today, back, len)
            val islandPerDay = runCatching { com.husarp.lockdown.engine.IslandUse.perDay(com.husarp.lockdown.link.IslandLink.islandRows(ctx), islandDays) }
                .getOrDefault(List(len) { 0L })
            val mine = du?.apps?.map { com.husarp.lockdown.engine.AppTime(it.pkg, it.label.ifEmpty { it.pkg }, it.totalMs) } ?: emptyList()
            val allApps = com.husarp.lockdown.engine.IslandUse.topApps(mine, island)
            // Sites instead of browsers: the time per site Lockdown's history has for the range (the browser's address
            // bar), each browser keeping only what no site was read for.
            val sites = if (!showSites) emptyMap() else runCatching {
                val last = com.husarp.lockdown.guard.TrustedTime.local(ctx).toLocalDate().minusDays(back.toLong())
                SiteTop.msByBrowser(com.husarp.lockdown.data.History.detail(last.minusDays(len - 1L), last).values.flatMap { it.rows })
            }.getOrDefault(emptyMap())
            val pm = ctx.packageManager
            val rows = SiteTop.rows(allApps, sites, showSites) { pkg ->
                runCatching { pm.getApplicationInfo(pkg, 0).loadLabel(pm).toString() }.getOrDefault(pkg)
            }
            // every row, with its kind: the list shows the top 6 of the kind chosen on "All apps"
            val apps = AppList.stats(rows, mine.map { it.pkg }.toSet(), island.keys, systemPkgs(ctx, rows.map { it.pkg }.toSet()))
            val hourly = len == 1
            // Today/Yesterday: the helper's hourly rows (when it sent them for that day) fill the bars and the total
            val islandHours = if (hourly) runCatching { com.husarp.lockdown.link.IslandLink.islandHours(ctx, today.minusDays(back.toLong())) }.getOrNull() else null
            val islandTotal = islandHours?.sum() ?: islandPerDay.sum()
            val bars: List<Bar> = if (hourly) {
                val labels = ChartAxis.hourLabels()
                (0..23).map { h -> Bar(labels[h], (du?.hourly?.getOrElse(h) { 0L } ?: 0L) + (islandHours?.get(h) ?: 0L)) }
            } else {
                // dailyTotals counts back from today; Island's days for the shown range are the same days when back == 0
                val totals = runCatching { Usage.dailyTotals(ctx, len) }.getOrDefault(List(len) { 0L })
                    .mapIndexed { i, ms -> ms + islandPerDay[i] }
                val labels = ChartAxis.dayLabels((0 until len).map { i -> today.minusDays((len - 1 - i).toLong()) })
                totals.mapIndexed { i, ms -> Bar(labels[i], ms) }
            }
            InsightData((du?.totalMs ?: 0L) + islandTotal, len, apps, bars, hourly, if (hourly && islandHours == null) islandTotal else 0L)
        }
    }

    // A tap anywhere but on a bar (those take their own taps; a scroll is no tap) brings the total back.
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).pointerInput(Unit) { detectTapGestures { selected = -1 } }
        .padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            ranges.forEachIndexed { i, r -> Chip(r.first, i == range, hue = Hue.TEAL) { range = i } }
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
                // 30 bars at phone width are narrow: less gap between them
                val gap = if (d.hourly || d.bars.size > 7) 2.dp else 6.dp
                Row(Modifier.fillMaxWidth().height(110.dp), verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(gap)) {
                    d.bars.forEachIndexed { i, b ->
                        Column(Modifier.weight(1f).fillMaxHeight().clickable { selected = if (selected == i) -1 else i }, horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Bottom) {
                            // an empty day or hour has no mark (only the baseline under the bars)
                            if (b.ms > 0) {
                                val h = (106 * b.ms / max).toInt().dp.coerceAtLeast(3.dp)
                                // a day's hours inside bedtime -> wake are indigo
                                val night = d.hourly && cfg.sleep.on && inBedtime(i * 60, cfg.sleep.bedtime, cfg.sleep.wake)
                                Box(Modifier.fillMaxWidth().height(h).clip(RoundedCornerShape(topStart = 4.dp, topEnd = 4.dp)).background(when {
                                    i == selected -> teal.onContainer
                                    night -> tone(Hue.INDIGO).accent
                                    else -> teal.accent
                                }))
                            }
                        }
                    }
                }
                Box(Modifier.fillMaxWidth().height(1.dp).background(cs.outlineVariant))
                // the labels on their own row, each centred under its bar on one line (it may be wider than the bar)
                Row(Modifier.fillMaxWidth().padding(top = 4.dp), horizontalArrangement = Arrangement.spacedBy(gap)) {
                    d.bars.forEach { b ->
                        Box(Modifier.weight(1f), contentAlignment = Alignment.Center) {
                            if (b.label.isNotEmpty()) Text(b.label, Modifier.wrapContentWidth(unbounded = true), style = MaterialTheme.typography.labelSmall,
                                color = cs.onSurfaceVariant, maxLines = 1, softWrap = false)
                        }
                    }
                }
                if (d.islandNotInBars >= 60_000L) {
                    Spacer(Modifier.height(8.dp))
                    Text("Includes ${d.islandNotInBars / 60_000} min in Island not shown by hour.", style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant)
                }
            }
            Card(color = cs.surfaceContainerLow, padding = androidx.compose.foundation.layout.PaddingValues(vertical = 8.dp)) {
                val kinds = remember { AppList.kinds(islandLinked(ctx)) }
                var kind by remember { mutableStateOf(AppList.effective(AppListPrefs.kind(ctx), kinds)) }
                Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    SectionLabel("Top apps", Modifier.weight(1f).padding(vertical = 4.dp))
                    AppKindMenu(kind, kinds) { kind = it }
                }
                val ofKind = remember(d, kind) { AppList.filter(d.perApp, kind) }
                val top = ofKind.take(6)
                val topMax = (top.maxOfOrNull { it.ms } ?: 1L).coerceAtLeast(1L)
                top.forEach { a ->
                    val site = a.site
                    val barColor = tone(Kinds.category(if (site != null) siteCategory(cfg, site) else appCategory(cfg, a.pkg)).hue).accent
                    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        if (site != null) ItemIcon(ItemType.SITE, site, site) else AppIcon(a.pkg, a.label)
                        Column(Modifier.weight(1f)) {
                            // the category menu on the name's line, all three on one baseline
                            Row(Modifier.fillMaxWidth()) {
                                Column(Modifier.weight(1f).padding(end = 8.dp).alignByBaseline()) {
                                    Text(a.label, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                    a.via?.let { Text("in $it", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis) }
                                }
                                Text(fmtDuration(a.ms), Modifier.alignByBaseline(), style = MaterialTheme.typography.titleSmall)
                                CategoryMenu(a.pkg, site, Modifier.alignByBaseline())
                            }
                            Spacer(Modifier.height(5.dp))
                            Box(Modifier.fillMaxWidth().height(4.dp).clip(CircleShape).background(cs.surfaceContainerHighest)) {
                                Box(Modifier.fillMaxWidth(AppList.bar(a.ms, topMax)).height(4.dp).clip(CircleShape).background(barColor))
                            }
                        }
                    }
                }
                if (d.perApp.isEmpty()) Text("No usage yet. Grant usage access in Settings.", Modifier.padding(16.dp), color = cs.onSurfaceVariant)
                else if (top.isEmpty()) Text("No apps of this kind used in this range.", Modifier.padding(16.dp), color = cs.onSurfaceVariant)
                if (d.perApp.isNotEmpty()) TextButton(onClick = { onOpenApps(range) }, Modifier.padding(horizontal = 8.dp)) { Text("Show all (${ofKind.size})") }
                if (d.perApp.isNotEmpty()) Text("Mark an app Distracting and modes like Work and Focus block it.", Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
            SectionCard(Icons.Filled.History, Hue.TEAL, "Activity log", "Apps opened, sites visited and blocks, last 7 days", onClick = onOpenActivity)
            val (back, len) = ranges[range].second
            InsightsHistory(back, len)
        }
    }
}

/** [minute] of the day (0-1439) is inside bedtime -> wake ("23:00" -> "07:00", past midnight or not). */
internal fun inBedtime(minute: Int, bedtime: String, wake: String): Boolean {
    fun min(t: String): Int? {
        val p = t.trim().split(":")
        val h = p.getOrNull(0)?.toIntOrNull() ?: return null
        val m = p.getOrNull(1)?.toIntOrNull() ?: return null
        return h * 60 + m
    }
    val b = min(bedtime) ?: return false
    val w = min(wake) ?: return false
    return if (b <= w) minute in b until w else minute >= b || minute < w
}
