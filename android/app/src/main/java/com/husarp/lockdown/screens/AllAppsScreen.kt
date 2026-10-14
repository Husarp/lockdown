package com.husarp.lockdown.screens

import android.content.Context
import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.material.icons.filled.Check
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.data.Live
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.ActivityLog
import com.husarp.lockdown.engine.AppKind
import com.husarp.lockdown.engine.AppList
import com.husarp.lockdown.engine.AppSort
import com.husarp.lockdown.engine.AppStat
import com.husarp.lockdown.engine.AppTime
import com.husarp.lockdown.engine.IslandUse
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.SiteTop
import com.husarp.lockdown.engine.TimeLeft
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.link.IslandLink
import com.husarp.lockdown.ui.AppIcon
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.ItemIcon
import com.husarp.lockdown.ui.Kinds
import com.husarp.lockdown.ui.fmtDuration
import com.husarp.lockdown.ui.theme.Hue
import com.husarp.lockdown.ui.theme.tone
import com.husarp.lockdown.usage.Usage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.time.LocalDate

/** The app lists' chosen sort and kind (looks only: saved at once, free, no challenge). */
internal object AppListPrefs {
    private fun p(ctx: Context) = ctx.applicationContext.getSharedPreferences("app_list", Context.MODE_PRIVATE)
    fun sort(ctx: Context): AppSort = AppSort.entries.firstOrNull { it.name == p(ctx).getString("sort", null) } ?: AppSort.TIME
    fun kind(ctx: Context): AppKind = AppKind.entries.firstOrNull { it.name == p(ctx).getString("kind", null) } ?: AppKind.ALL
    fun save(ctx: Context, sort: AppSort, kind: AppKind) { p(ctx).edit().putString("sort", sort.name).putString("kind", kind.name).apply() }
}

/** The lists' kind picked right on a card (Home's "Time per app", Insights' Top apps): "Only apps I installed ▾" and
 *  the like. Remembered as on "All apps", so every list follows it. */
@Composable
internal fun AppKindMenu(kind: AppKind, kinds: List<AppKind>, modifier: Modifier = Modifier, onPick: (AppKind) -> Unit) {
    val ctx = LocalContext.current
    var open by remember { mutableStateOf(false) }
    Box(modifier) {
        SmallTextButton(onClick = { open = true }) {
            Text(kind.label + " ▾", style = MaterialTheme.typography.labelMedium, maxLines = 1)
        }
        androidx.compose.material3.DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            kinds.forEach { k ->
                androidx.compose.material3.DropdownMenuItem(text = { Text(k.label) }, onClick = {
                    open = false
                    AppListPrefs.save(ctx, AppListPrefs.sort(ctx), k)
                    onPick(k)
                }, trailingIcon = if (k == kind) { { androidx.compose.material3.Icon(androidx.compose.material.icons.Icons.Filled.Check, "Chosen") } } else null)
            }
        }
    }
}

/** Island is linked (this is main), so the Personal / Island kinds mean something. */
internal fun islandLinked(ctx: Context): Boolean =
    IslandLink.role(ctx) == com.husarp.lockdown.link.Role.MAIN && IslandLink.mainState.value.state == "linked"

/** The [pkgs] that came with the phone (Android's flags), plus its home screen and Settings, which always count as system. */
internal fun systemPkgs(ctx: Context, pkgs: Collection<String>): Set<String> {
    val pm = ctx.packageManager
    fun resolve(i: Intent) = runCatching { pm.resolveActivity(i, 0)?.activityInfo?.packageName }.getOrNull()
    val always = setOfNotNull(resolve(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)),
        resolve(Intent(android.provider.Settings.ACTION_SETTINGS)), "com.android.settings")
    return pkgs.filterTo(HashSet()) { pkg ->
        AppList.system(pkg, runCatching { pm.getApplicationInfo(pkg, 0).flags }.getOrNull(), always)
    }
}

/** The page's rows for a range ([back] days back, [len] days), as Insights' Top apps counts them (main and Island merged,
 *  sites instead of browsers when that's on), with opens and the latest open from the same usage events. */
private data class AllApps(val rows: List<AppStat>, val today: LocalDate, val usageOk: Boolean)

private fun loadAll(ctx: Context, back: Int, len: Int, showSites: Boolean): AllApps {
    val start = Usage.dayBounds(ctx, back + len - 1).first
    val end = Usage.dayBounds(ctx, back).second
    val now = TrustedTime.local(ctx)
    val today = now.toLocalDate()
    val pm = ctx.packageManager
    val labels = HashMap<String, String>()
    fun label(pkg: String) = labels.getOrPut(pkg) { runCatching { pm.getApplicationInfo(pkg, 0).loadLabel(pm).toString() }.getOrDefault(pkg) }
    val usageOk = Usage.hasAccess(ctx)
    // one query: the spans give each app's time (as Usage.range adds it) and its visits (as the Activity log groups them)
    val spans = if (!usageOk) emptyList() else runCatching { Usage.spans(ctx, start, end) }.getOrDefault(emptyList())
    val main = spans.groupBy { it.pkg }.map { (pkg, s) -> AppTime(pkg, label(pkg), s.sumOf { it.toMs - it.fromMs }) }.filter { it.ms >= 1000 }
    val island = runCatching { IslandLink.islandUse(ctx, today.minusDays(back + len - 1L), today.minusDays(back.toLong())) }.getOrDefault(emptyMap())
    val all = IslandUse.topApps(main, island)
    val last = today.minusDays(back.toLong())
    val detail = if (!showSites) emptyMap() else runCatching { com.husarp.lockdown.data.History.detail(last.minusDays(len - 1L), last) }.getOrDefault(emptyMap())
    val sites = SiteTop.msByBrowser(detail.values.flatMap { it.rows })
    val zone = TrustedTime.zone()
    val siteOpens = ActivityLog.siteRows(detail.values.flatMap { it.switches }, now, ::label).map { it.name to it.at.atZone(zone).toInstant().toEpochMilli() }
    val rows = SiteTop.rows(all, sites, showSites, ::label)
    val opens = AppList.opens(ActivityLog.visits(spans), runCatching { IslandLink.islandOpens(ctx) }.getOrDefault(emptyList()), siteOpens, start, end)
    val stats = AppList.stats(rows, main.map { it.pkg }.toSet(), island.keys, systemPkgs(ctx, rows.map { it.pkg }.toSet()), opens)
    return AllApps(stats, today, usageOk)
}

/**
 * "All apps": every app used in the range (Home's "Time per app today" and Insights' Top apps show the top few), sorted
 * by time, opens or the latest open, and filtered to a kind (installed by you, personal, Island). The sort and kind are
 * remembered; the kind applies to Home's card and Top apps too. Today, an app with a limit shows its time left.
 */
@Composable
fun AllAppsScreen(startRange: Int = 0) {
    val ctx = LocalContext.current
    val cs = MaterialTheme.colorScheme
    val cfg by Store.state.collectAsStateWithLifecycle()
    val ranges = listOf("Today" to (0 to 1), "Yesterday" to (1 to 1), "7 days" to (0 to 7), "30 days" to (0 to 30))
    var range by remember { mutableIntStateOf(startRange.coerceIn(0, ranges.size - 1)) }
    var sort by remember { mutableStateOf(AppListPrefs.sort(ctx)) }
    var kind by remember { mutableStateOf(AppListPrefs.kind(ctx)) }
    val showSites = cfg.settings.sitesInsteadOfBrowsers
    val data by produceState<AllApps?>(null, range, showSites) {
        value = null
        value = withContext(Dispatchers.IO) {
            val (back, len) = ranges[range].second
            runCatching { loadAll(ctx, back, len, showSites) }.getOrDefault(AllApps(emptyList(), LocalDate.now(), true))
        }
    }
    val kinds = remember { AppList.kinds(islandLinked(ctx)) }
    val shownKind = AppList.effective(kind, kinds)
    val d = data
    val shown = remember(d, sort, shownKind) { d?.let { AppList.sort(AppList.filter(it.rows, shownKind), sort) } }
    val max = (shown?.maxOfOrNull { it.ms } ?: 0L)
    val now = remember(d) { TrustedTime.local(ctx) }
    val modeState = ModesStore.current(now)
    val listed = remember(cfg.items) { cfg.items.filter { it.type == ItemType.APP }.associateBy { it.target.lowercase() } }

    Column(Modifier.fillMaxSize()) {
        Spacer(Modifier.height(4.dp))
        ChipRow { ranges.forEachIndexed { i, r -> Chip(r.first, i == range, hue = Hue.TEAL) { range = i } } }
        ChipRow { AppSort.entries.forEach { s -> Chip(s.label, s == sort, hue = Hue.TEAL) { sort = s; AppListPrefs.save(ctx, sort, kind) } } }
        ChipRow { kinds.forEach { k -> Chip(k.label, k == shownKind, hue = Hue.TEAL) { kind = k; AppListPrefs.save(ctx, sort, kind) } } }
        LazyColumn(Modifier.weight(1f), contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp)) {
            if (d == null || shown == null) item { Text("Loading…", color = cs.onSurfaceVariant) }
            else {
                if (shown.isEmpty()) item {
                    Text(if (!d.usageOk) "Allow usage access in Settings to see your apps." else "No apps of this kind used in this range.",
                        Modifier.padding(vertical = 12.dp), color = cs.onSurfaceVariant)
                }
                items(shown, key = { it.pkg + "|" + it.site }) { a ->
                    val left = if (range != 0 || a.site != null) null
                        else listed[a.pkg.lowercase()]?.let { Live.timeLeft(cfg, it, now, modeState) }?.takeIf { it.kind != TimeLeft.Kind.FREE }
                    AppStatRow(a, AppList.bar(a.ms, max), left, cfg, d.today, now)
                }
                item {
                    Text("Opens count each visit of 5 seconds or more, as the Activity log lists them (Island's from Lockdown in Island, " +
                        "for the last 7 days). System apps are the ones that came with the phone and Settings. Time on the home screen " +
                        "isn't counted as using an app (as in Android's Digital Wellbeing).",
                        Modifier.padding(vertical = 16.dp), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                }
            }
        }
    }
}

@Composable
private fun ChipRow(content: @Composable () -> Unit) =
    Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) { content() }

/** One app (or site): icon, name, time; opens and the latest open; a bar of its own time on the list's scale - amber
 *  when it has a limit ("N min left", the limit's), red once blocked, else its category's colour (as Insights). */
@Composable
private fun AppStatRow(a: AppStat, frac: Float, left: TimeLeft.Left?, cfg: com.husarp.lockdown.data.Config, today: LocalDate, now: java.time.LocalDateTime) {
    val cs = MaterialTheme.colorScheme
    val site = a.site
    val bar = when {
        left == null -> tone(Kinds.category(if (site != null) siteCategory(cfg, site) else appCategory(cfg, a.pkg)).hue).accent
        left.kind == TimeLeft.Kind.BLOCKED -> tone(Hue.RED).accent
        else -> tone(Hue.AMBER).accent
    }
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        if (site != null) ItemIcon(ItemType.SITE, site, site) else AppIcon(a.pkg, a.label)
        Column(Modifier.weight(1f)) {
            // the category menu on the name's line, all three on one baseline
            Row(Modifier.fillMaxWidth()) {
                Column(Modifier.weight(1f).padding(end = 8.dp).alignByBaseline()) {
                    Text(a.label, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    val opened = AppList.lastText(a.lastMs, today, TrustedTime.zone())
                    val sub = listOfNotNull(a.via?.let { "in $it" },
                        when (a.opens) { 0 -> null; 1 -> "1 open"; else -> "${a.opens} opens" },
                        opened.takeIf { it.isNotEmpty() }?.let { "last $it" }).joinToString(" · ")
                    if (sub.isNotEmpty()) Text(sub, style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                if (left != null) Text(TimeLeft.line(left, now) + " · ", Modifier.alignByBaseline(), style = MaterialTheme.typography.labelMedium, color = bar,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(fmtDuration(a.ms), Modifier.alignByBaseline(), style = MaterialTheme.typography.titleSmall)
                CategoryMenu(a.pkg, site, Modifier.alignByBaseline())
            }
            Spacer(Modifier.height(5.dp))
            Box(Modifier.fillMaxWidth().height(4.dp).clip(CircleShape).background(cs.surfaceContainerHighest)) {
                Box(Modifier.fillMaxWidth(frac).height(4.dp).clip(CircleShape).background(bar))
            }
        }
    }
}
