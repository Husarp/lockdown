package com.husarp.lockdown.screens

import android.content.Context
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.History
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.HistoryLog
import com.husarp.lockdown.engine.Stats
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.SectionLabel
import com.husarp.lockdown.ui.theme.LockdownTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.LocalDate
import java.time.LocalDateTime

/** What Lockdown's own history (data/History.kt) says about the range picked in Insights, worked out off the main thread. */
private data class HistData(
    val active: Int,
    val kinds: Map<String, Int>,                 // productive / neutral / distracting -> seconds
    val sessions: Int,
    val longestSession: Int,
    val focus: Pair<Int, String>?,               // (seconds, app label)
    val switches: Int,
    val shortVisits: Int,
    val sites: List<Pair<String, Int>>,
    val blocked: Int,
    val savedSec: Int,
    val goalStreak: Int?,                        // null: no daily goal set
    val noUnlock: Int,
    val heat: List<List<Int>>?,                  // rows x 24 hours, levels 0-4 (7 and 30 days)
    val heatRows: List<String>,
    val hasAny: Boolean,                         // there's any history at all (not just in this range)
)

private fun label(ctx: Context, pkg: String) =
    runCatching { ctx.packageManager.let { pm -> pm.getApplicationInfo(pkg, 0).loadLabel(pm).toString() } }.getOrDefault(pkg)

private fun load(ctx: Context, cfg: Config, back: Int, len: Int): HistData {
    val now = TrustedTime.local(ctx)
    val today = now.toLocalDate()
    val to = today.minusDays(back.toLong()); val from = to.minusDays(len - 1L)
    val detail = History.detail(from, to)
    val rows = detail.values.flatMap { it.rows }
    val switches = detail.values.flatMap { it.switches }
    val blocked = detail.values.flatMap { it.blocked }
    val kind = { app: String, site: String ->
        if (site.isNotEmpty()) Stats.categoryOf("site", site, cfg.categories, cfg.items) else Stats.categoryOf("app", app, cfg.categories, cfg.items)
    }
    val sessions = Stats.sessions(rows)
    val sw = Stats.switchSummary(switches, now)
    // the usual visit length comes from the 30 days before the range's end
    val past = History.detail(to.minusDays(30), to).values.flatMap { it.switches }
    val totals = History.allTotals()
    val first = totals.filterValues { it.total > 0 }.keys.minOrNull()
    val streakDays = if (first == null) emptyList() else
        generateSequence(today) { it.minusDays(1) }.takeWhile { !it.isBefore(first) && !it.isBefore(today.minusDays(366)) }.toList()
    val goal = cfg.settings.dailyGoalMin.takeIf { it > 0 }
    val unlockDays = cfg.unlocks.mapNotNull { runCatching { LocalDateTime.parse(it).toLocalDate() }.getOrNull() }.toSet()
    val days = (0 until len).map { from.plusDays(it.toLong()) }
    val names = listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    return HistData(
        active = Stats.totals(rows).first,
        kinds = rows.groupBy { kind(it.app, it.site) }.mapValues { e -> e.value.sumOf { it.active } },
        sessions = sessions.size,
        longestSession = sessions.maxOfOrNull { java.time.Duration.between(it.first, it.second).seconds.toInt() } ?: 0,
        focus = Stats.longestFocus(rows)?.let { it.first to label(ctx, it.second) },
        switches = sw.count, shortVisits = sw.short,
        sites = Stats.perSite(rows).entries.sortedByDescending { it.value }.take(5).map { it.key to it.value },
        blocked = blocked.size,
        savedSec = Stats.timeSaved(blocked, cfg.items, past, now).toInt(),
        goalStreak = goal?.let { g -> if (streakDays.isEmpty()) 0 else Stats.goalStreak(totals.mapValues { it.value.total }, streakDays, g * 60) },
        noUnlock = Stats.noUnlockStreak(streakDays, unlockDays),
        heat = when { len == 7 -> Stats.heatmap(rows, days); len > 7 -> Stats.weekdayHeatmap(rows, days); else -> null },
        heatRows = if (len == 7) days.map { names[it.dayOfWeek.value - 1] } else names,
        hasAny = first != null,
    )
}

/** Screen time per day and app / site, the last 365 days, as CSV (PC: Screen Time > Export). */
private fun csv(cfg: Config): String {
    val since = LocalDate.now().minusDays(365)
    val days = History.allTotals().filterKeys { !it.isBefore(since) }.mapValues { it.value.seconds }
    return HistoryLog.csv(days) { app, site -> if (site.isNotEmpty()) cfg.categories["site:$site"] else cfg.categories["app:$app"] }
}

/** The Insights cards fed by Lockdown's own history: kinds of time, focus, top sites, streaks, heatmap, CSV export. */
@Composable
fun InsightsHistory(back: Int, len: Int) {
    val ctx = LocalContext.current
    val cs = MaterialTheme.colorScheme
    val scope = rememberCoroutineScope()
    var exportMsg by remember { mutableStateOf<String?>(null) }
    val export = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/csv")) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            exportMsg = withContext(Dispatchers.IO) {
                runCatching { ctx.contentResolver.openOutputStream(uri)!!.use { it.write(csv(Store.config).toByteArray()) } }
                    .fold({ "Saved." }, { "Couldn't save the file." })
            }
        }
    }
    val d by produceState<HistData?>(null, back, len) {
        value = withContext(Dispatchers.IO) { runCatching { load(ctx, Store.config, back, len) }.getOrNull() }
    }
    val h = d ?: return
    val muted = cs.onSurfaceVariant

    Card(color = cs.surfaceContainerLow) {
        SectionLabel("Your patterns")
        if (h.active == 0) {
            Text(if (h.hasAny) "Nothing recorded in this range." else "Lockdown keeps its own history from now on. It fills in as you use the phone.",
                Modifier.padding(top = 6.dp), color = muted)
        } else {
            Spacer(Modifier.height(10.dp))
            val ex = LockdownTheme.extra
            val kinds = listOf(Triple("productive", "Productive", ex.success), Triple("neutral", "Neutral", cs.outline), Triple("distracting", "Distracting", cs.error))
            Row(Modifier.fillMaxWidth().height(10.dp), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                kinds.forEach { (k, _, c) ->
                    val s = h.kinds[k] ?: 0
                    if (s > 0) Box(Modifier.weight(s.toFloat()).height(10.dp).clip(RoundedCornerShape(4.dp)).background(c))
                }
            }
            Spacer(Modifier.height(8.dp))
            kinds.forEach { (k, name, c) ->
                Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.width(10.dp).height(10.dp).clip(CircleShape).background(c))
                    Text(name, Modifier.padding(start = 8.dp).weight(1f), style = MaterialTheme.typography.bodyMedium)
                    Text(Stats.hm(h.kinds[k] ?: 0), style = MaterialTheme.typography.titleSmall)
                }
            }
            Spacer(Modifier.height(12.dp))
            Row(Modifier.fillMaxWidth()) {
                Stat("Sessions", "${h.sessions}", "longest ${Stats.hm(h.longestSession)}", Modifier.weight(1f))
                Stat("Longest focus", h.focus?.let { Stats.hm(it.first) } ?: "-", h.focus?.second ?: "", Modifier.weight(1f))
            }
            Spacer(Modifier.height(10.dp))
            Row(Modifier.fillMaxWidth()) {
                Stat("Switches", "${h.switches}", "${h.shortVisits} under 30 s", Modifier.weight(1f))
                Stat("Time saved", Stats.hm(h.savedSec), "${h.blocked} blocked tr${if (h.blocked == 1) "y" else "ies"}", Modifier.weight(1f))
            }
        }
    }

    if (h.sites.isNotEmpty()) Card(color = cs.surfaceContainerLow) {
        SectionLabel("Top sites")
        val max = h.sites.first().second.coerceAtLeast(1)
        h.sites.forEach { (site, sec) ->
            Column(Modifier.padding(top = 8.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(site, style = MaterialTheme.typography.bodyMedium)
                    Text(Stats.hm(sec), style = MaterialTheme.typography.titleSmall)
                }
                Spacer(Modifier.height(5.dp))
                Box(Modifier.fillMaxWidth().height(4.dp).clip(CircleShape).background(cs.surfaceContainerHighest)) {
                    Box(Modifier.fillMaxWidth((sec.toFloat() / max).coerceIn(0.02f, 1f)).height(4.dp).clip(CircleShape).background(cs.primary))
                }
            }
        }
        Text("Sites read from the browser's address bar.", Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodySmall, color = muted)
    }

    Card(color = cs.surfaceContainerLow) {
        SectionLabel("Streaks")
        Row(Modifier.fillMaxWidth().padding(top = 8.dp)) {
            Stat("Within your goal", h.goalStreak?.let { "$it day${if (it == 1) "" else "s"}" } ?: "-",
                if (h.goalStreak == null) "Set a daily goal in Settings" else "in a row", Modifier.weight(1f))
            Stat("No emergency unlock", "${h.noUnlock} day${if (h.noUnlock == 1) "" else "s"}", "in a row", Modifier.weight(1f))
        }
    }

    h.heat?.let { heat ->
        Card(color = cs.surfaceContainerLow) {
            SectionLabel(if (len == 7) "When you use it" else "When you use it · average day")
            Spacer(Modifier.height(8.dp))
            heat.forEachIndexed { i, row ->
                Row(Modifier.fillMaxWidth().padding(vertical = 1.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(h.heatRows[i], Modifier.width(36.dp), style = MaterialTheme.typography.labelSmall, color = muted)
                    row.forEach { level ->
                        Box(Modifier.weight(1f).height(12.dp).padding(horizontal = 1.dp).clip(RoundedCornerShape(2.dp))
                            .background(if (level == 0) cs.surfaceContainerHighest else cs.primary.copy(alpha = 0.2f + 0.2f * level)))
                    }
                }
            }
            Row(Modifier.fillMaxWidth().padding(start = 36.dp, top = 4.dp)) {
                listOf("0", "6", "12", "18").forEach { Text(it, Modifier.weight(1f), style = MaterialTheme.typography.labelSmall, color = muted) }
            }
            Text("Darker: more minutes in that hour.", Modifier.padding(top = 6.dp), style = MaterialTheme.typography.bodySmall, color = muted)
        }
    }

    Row(verticalAlignment = Alignment.CenterVertically) {
        TextButton(onClick = { exportMsg = null; export.launch("lockdown-screen-time.csv") }) { Text("Export screen time (CSV)") }
        exportMsg?.let { Text(it, style = MaterialTheme.typography.bodyMedium, color = cs.primary) }
    }
}

@Composable
private fun Stat(title: String, value: String, note: String, modifier: Modifier = Modifier) {
    Column(modifier) {
        Text(title, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.titleMedium)
        if (note.isNotEmpty()) Text(note, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
    }
}
