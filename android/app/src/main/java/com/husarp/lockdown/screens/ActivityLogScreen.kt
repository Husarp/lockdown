package com.husarp.lockdown.screens

import android.content.Context
import android.content.Intent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.husarp.lockdown.data.BypassLog
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.History
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.ActivityLog
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.LogFilter
import com.husarp.lockdown.engine.LogItem
import com.husarp.lockdown.engine.LogKind
import com.husarp.lockdown.engine.LogRange
import com.husarp.lockdown.engine.LogRow
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.link.IslandLink
import com.husarp.lockdown.ui.AppIcon
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.IconTile
import com.husarp.lockdown.ui.ItemIcon
import com.husarp.lockdown.ui.Kinds
import com.husarp.lockdown.ui.theme.Hue
import com.husarp.lockdown.usage.Usage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.time.LocalDate

/** The log as the page shows it: the rows (newest first, the last 7 days), today for the day headings, and whether
 *  Android's usage data could be read (apps come from it). */
private data class LogData(val rows: List<LogRow>, val today: LocalDate, val usageOk: Boolean)

/** Reads everything off the main thread: usage events for apps, Lockdown's own history for sites and blocks, the
 *  config for emergency uses, the bypass record for pauses, challenges passed and switching off. Each piece is guarded on its own, so one failing leaves the others. */
private fun load(ctx: Context, cfg: Config): LogData {
    val now = TrustedTime.local(ctx)
    val today = now.toLocalDate()
    val from = today.minusDays(ActivityLog.DAYS - 1L)
    val pm = ctx.packageManager
    val labels = HashMap<String, String>()
    // an app installed only in Island (a browser its sites were seen in) is named as the pickers name it: "Label (Island)"
    val islandOnly by lazy { runCatching { IslandLink.islandOnly(ctx, emptySet()) }.getOrDefault(emptyList()).associate { it.pkg to it.label } }
    fun label(pkg: String) = labels.getOrPut(pkg) {
        runCatching { pm.getApplicationInfo(pkg, 0).loadLabel(pm).toString() }.getOrNull() ?: islandOnly[pkg] ?: pkg
    }
    val home = runCatching {
        pm.resolveActivity(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME), 0)?.activityInfo?.packageName
    }.getOrNull()
    val leave = setOfNotNull(ctx.packageName, "com.android.systemui", home)
    val zone = java.time.ZoneId.systemDefault()
    val usageOk = Usage.hasAccess(ctx)
    val spans = if (!usageOk) emptyList() else runCatching {
        Usage.spans(ctx, from.atStartOfDay(zone).toInstant().toEpochMilli(), System.currentTimeMillis())
    }.getOrDefault(emptyList())
    val apps = ActivityLog.appRows(spans, zone, ::label) { it in leave }
    // Island apps opened: Lockdown in Island sends them over the link (this phone's usage data only has this profile's)
    val island = ActivityLog.islandRows(runCatching { IslandLink.islandOpens(ctx) }.getOrDefault(emptyList()), zone)
    val detail = runCatching { History.detail(from, today) }.getOrDefault(emptyMap())
    val sites = ActivityLog.siteRows(detail.values.flatMap { it.switches }, now, ::label)
    val byId = cfg.items.associateBy { it.id }
    val blocks = ActivityLog.blockRows(detail.values.flatMap { it.blocked }, { b ->
        val item = byId[b.itemId]
        // an app item: its package; no item (a mode, a word, a protection list): an installed package is an app, else a site / word
        if (item != null) item.target.takeIf { item.type == ItemType.APP }
        else b.target.takeIf { it.contains('.') && runCatching { pm.getApplicationInfo(it, 0) }.isSuccess }
    }, ::label)
    val uses = ActivityLog.emergencyRows(cfg.allUses(), from)
    val bypasses = ActivityLog.bypassRows(BypassLog.read(ctx), from)
    return LogData(ActivityLog.merge(today, apps, island, sites, blocks, uses, bypasses), today, usageOk)
}

/** "Activity log": what was used in the last 7 days, newest first, grouped by day. Beyond what Lockdown already
 *  keeps, only the bypasses are recorded for it (data/BypassLog.kt); the list is a LazyColumn over a capped set of rows. */
@Composable
fun ActivityLogScreen() {
    val ctx = LocalContext.current
    val cs = MaterialTheme.colorScheme
    var filter by remember { mutableStateOf(LogFilter.ALL) }
    var range by remember { mutableStateOf<LogRange?>(LogRange.WEEK) }                  // null: the days picked
    var picked by remember { mutableStateOf<Pair<LocalDate, LocalDate>?>(null) }
    var picking by remember { mutableStateOf(false) }
    val data by produceState<LogData?>(null) {
        value = withContext(Dispatchers.IO) {
            runCatching { load(ctx, Store.config) }.getOrDefault(LogData(emptyList(), LocalDate.now(), true))
        }
    }
    val d = data
    val days = d?.let { range?.days(it.today) ?: picked }
    val shown = remember(d, filter, days) { d?.let { ActivityLog.flat(ActivityLog.view(it.rows, filter, days?.first ?: LocalDate.MIN, days?.second ?: LocalDate.MAX)) } }

    Column(Modifier.fillMaxSize()) {
        // when: Today / Yesterday / 7 days (all the log keeps), or days picked inside those 7
        Row(Modifier.horizontalScroll(rememberScrollState()).padding(start = 16.dp, end = 16.dp, top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            LogRange.entries.forEach { r -> Chip(r.label, r == range, hue = Hue.TEAL) { range = r } }
            val p = picked
            Chip(if (range == null && p != null && d != null) pickedText(p, d.today) else "Pick days…", range == null, hue = Hue.TEAL) { picking = true }
        }
        Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            LogFilter.entries.forEach { f -> Chip(f.label, f == filter, hue = Hue.TEAL) { filter = f } }
        }
        if (picking && d != null) PickDays(d.today, onDismiss = { picking = false }) { from, to ->
            picking = false; picked = ActivityLog.clampDays(from, to, d.today); range = null
        }
        LazyColumn(Modifier.weight(1f), contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp, vertical = 4.dp)) {
            if (d == null || shown == null) item { Text("Loading…", color = cs.onSurfaceVariant) }
            else {
                if (shown.isEmpty()) item {
                    Text(
                        if (filter == LogFilter.APPS && !d.usageOk) "Grant usage access in Settings to list the apps you opened."
                        else if (filter == LogFilter.BYPASS) "No bypass used in this time: no emergency unlock, pause, challenge passed or switching off."
                        else if (range != LogRange.WEEK) "Nothing in this time."
                        else "Nothing here yet. Apps, sites and blocks from the last 7 days show up as you use the phone.",
                        Modifier.padding(vertical = 12.dp), color = cs.onSurfaceVariant,
                    )
                }
                items(shown) { item ->
                    when (item) {
                        is LogItem.Day -> Text(ActivityLog.dayLabel(item.day, d.today), Modifier.padding(top = 16.dp, bottom = 4.dp),
                            style = MaterialTheme.typography.titleSmall, color = cs.onSurfaceVariant)
                        is LogItem.Entry -> LogLine(item.row)
                    }
                }
                item {
                    Text(
                        "Apps come from Android's usage data (Island's from Lockdown in Island, while linked). Sites are seen from the browser's address bar, so other apps' " +
                            "connections aren't listed, and private or incognito tabs are left out where the browser says so.",
                        Modifier.padding(vertical = 16.dp), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

/** "Mon 5 Oct" or "Mon 5 Oct – Wed 7 Oct" (Today / Yesterday by name). */
private fun pickedText(p: Pair<LocalDate, LocalDate>, today: LocalDate): String =
    if (p.first == p.second) ActivityLog.dayLabel(p.first, today) else ActivityLog.dayLabel(p.first, today) + " – " + ActivityLog.dayLabel(p.second, today)

/** Picks a day or a span of days, only among the [ActivityLog.DAYS] the log keeps (older ones can't be picked). */
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
private fun PickDays(today: LocalDate, onDismiss: () -> Unit, onPick: (LocalDate, LocalDate) -> Unit) {
    fun ms(day: LocalDate) = day.atStartOfDay(java.time.ZoneOffset.UTC).toInstant().toEpochMilli()      // the picker's days are UTC
    fun day(ms: Long) = java.time.Instant.ofEpochMilli(ms).atZone(java.time.ZoneOffset.UTC).toLocalDate()
    val first = today.minusDays(ActivityLog.DAYS - 1L)
    val state = androidx.compose.material3.rememberDateRangePickerState(
        initialDisplayedMonthMillis = ms(today),
        yearRange = first.year..today.year,
        selectableDates = object : androidx.compose.material3.SelectableDates {
            override fun isSelectableDate(utcTimeMillis: Long) = day(utcTimeMillis).let { !it.isBefore(first) && !it.isAfter(today) }
            override fun isSelectableYear(year: Int) = year in first.year..today.year
        },
    )
    androidx.compose.material3.DatePickerDialog(onDismissRequest = onDismiss,
        confirmButton = {
            androidx.compose.material3.TextButton(enabled = state.selectedStartDateMillis != null, onClick = {
                val a = state.selectedStartDateMillis ?: return@TextButton
                onPick(day(a), day(state.selectedEndDateMillis ?: a))
            }) { Text("Show") }
        },
        dismissButton = { androidx.compose.material3.TextButton(onClick = onDismiss) { Text("Cancel") } },
    ) {
        androidx.compose.material3.DateRangePicker(state, Modifier.weight(1f),
            title = { Text("Days to show (the log keeps the last ${ActivityLog.DAYS})", Modifier.padding(start = 24.dp, end = 12.dp, top = 16.dp)) },
            showModeToggle = false)
    }
}

@Composable
private fun LogLine(r: LogRow) {
    val cs = MaterialTheme.colorScheme
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("%02d:%02d".format(r.at.hour, r.at.minute), Modifier.width(44.dp), style = MaterialTheme.typography.labelMedium, color = cs.onSurfaceVariant)
        when {
            r.kind == LogKind.EMERGENCY -> IconTile(Kinds.EMERGENCY.icon, Kinds.EMERGENCY.hue, 36.dp)
            r.kind == LogKind.BYPASS -> IconTile(Kinds.PAUSE.icon, Kinds.PAUSE.hue, 36.dp)
            r.pkg.isNotEmpty() -> AppIcon(r.pkg, r.name, 36.dp)
            else -> ItemIcon(ItemType.SITE, r.name, r.name, 36.dp)
        }
        Column(Modifier.weight(1f)) {
            Text(r.name, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(r.what, style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}
