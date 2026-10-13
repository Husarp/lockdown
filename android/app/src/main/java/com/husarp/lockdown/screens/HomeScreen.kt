package com.husarp.lockdown.screens

import android.content.Context
import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDownward
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.BarChart
import androidx.compose.material.icons.filled.Bedtime
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.BusinessCenter
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.HourglassBottom
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.block.BlockService
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.HomeCard
import com.husarp.lockdown.data.HomeLayout
import com.husarp.lockdown.data.Live
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.AppList
import com.husarp.lockdown.engine.AppStat
import com.husarp.lockdown.engine.AppTime
import com.husarp.lockdown.engine.Emergency
import com.husarp.lockdown.engine.IslandUse
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.ModeState
import com.husarp.lockdown.engine.Modes
import com.husarp.lockdown.engine.Pause
import com.husarp.lockdown.engine.Perm
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.TimeLeft
import com.husarp.lockdown.engine.TopRow
import com.husarp.lockdown.guard.Perms
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.guard.rememberGuard
import com.husarp.lockdown.link.IslandLink
import com.husarp.lockdown.ui.AppIcon
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.HomePanel
import com.husarp.lockdown.ui.IconTile
import com.husarp.lockdown.ui.ItemIcon
import com.husarp.lockdown.ui.Kinds
import com.husarp.lockdown.ui.Numeral
import com.husarp.lockdown.ui.Pill
import com.husarp.lockdown.ui.fmtDuration
import com.husarp.lockdown.ui.theme.Hue
import com.husarp.lockdown.ui.theme.tone
import com.husarp.lockdown.usage.Usage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.time.Duration
import java.time.LocalDateTime

/** A Home card's name, one-line description and look, for the cards and the layout editor. */
internal data class HomeCardInfo(val name: String, val about: String, val icon: ImageVector, val hue: Hue)

internal val HOME_CARDS: Map<String, HomeCardInfo> = mapOf(
    "problems" to HomeCardInfo("Needs attention", "Shows only when something isn't working, with Fix", Icons.Filled.ErrorOutline, Hue.RED),
    "mode" to HomeCardInfo("Mode", "The mode that's on, or start one", Kinds.MODE.icon, Hue.BLUE),
    "blocked" to HomeCardInfo("Blocked now", "What's blocked right now and until when", Icons.Filled.Block, Hue.RED),
    "low" to HomeCardInfo("Running low", "Limits about to run out", Icons.Filled.HourglassBottom, Hue.AMBER),
    "apps" to HomeCardInfo("Time per app today", "Your top apps today, with time left", Icons.Filled.BarChart, Hue.TEAL),
    "bedtime" to HomeCardInfo("Bedtime", "When bedtime starts and wake-up", Icons.Filled.Bedtime, Hue.INDIGO),
    "quick" to HomeCardInfo("Shortcuts", "Focus, Block a site, Emergency", Icons.Filled.Bolt, Hue.ORANGE),
    "pause" to HomeCardInfo("Pause", "Pause your blocks, or resume", Kinds.PAUSE.icon, Kinds.PAUSE.hue),
    "emergency" to HomeCardInfo("Emergency unlocks", "Uses left, and Use", Kinds.EMERGENCY.icon, Kinds.EMERGENCY.hue),
    "island" to HomeCardInfo("Island", "Whether Lockdown in Island checks in", Icons.Filled.BusinessCenter, Hue.BLUE),
)

@Composable
fun HomeScreen(onOpenBlocking: () -> Unit, onOpenModes: () -> Unit, onOpenGuardrails: () -> Unit, onOpenInsights: () -> Unit = {},
               onOpenReminders: () -> Unit = {}, onOpenSettings: (String?) -> Unit = {}, onOpenApps: () -> Unit = {}) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    ModesStore.active.collectAsStateWithLifecycle().value                // a mode started or stopped shows at once
    ModesStore.modes.collectAsStateWithLifecycle().value

    // Home refreshes every minute while it's shown: today's screen time (read off the main thread, shared by the
    // hero and "Time per app"), and what's blocked or running low.
    val tick = com.husarp.lockdown.guard.rememberTick(60_000)      // (only while the app is in front)
    val today by produceState<TodayUse?>(null, tick) {
        value = withContext(Dispatchers.IO) { todayUse(ctx) }
    }
    val now = remember(tick, cfg) { TrustedTime.local(ctx) }
    val modeState = ModesStore.current(now)
    val layout = HomeLayout.resolve(cfg.settings.home)

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        com.husarp.lockdown.update.UpdateBanner()
        GrayscaleStuck()
        StatusHero(cfg, today?.totalMs ?: 0L)
        for (card in layout) if (card.on) when (card.id) {
            "problems" -> ProblemsCard(cfg, onOpenSettings)
            "mode" -> ModeCard(modeState, onOpenModes)
            "blocked" -> BlockedCard(cfg, now, tick, modeState, onOpenBlocking)
            "low" -> LowCard(cfg, now, modeState)
            "apps" -> AppsCard(cfg, now, today, modeState, onOpenInsights, onOpenApps)
            "bedtime" -> BedtimeCard(cfg, now, onOpenReminders)
            "quick" -> QuickCard(onOpenModes, onOpenBlocking, onOpenGuardrails)
            "pause" -> PauseHomeCard(cfg, now, onOpenGuardrails)
            "emergency" -> EmergencyCard(cfg, now)
            "island" -> IslandCard(onOpenSettings)
        }
        TextButton(onClick = { onOpenSettings("look") }, modifier = Modifier.align(Alignment.CenterHorizontally)) {
            Icon(Icons.Filled.Dashboard, null, Modifier.size(18.dp)); Spacer(Modifier.width(8.dp)); Text("Edit Home")
        }
    }
}

/** Today's screen time and per-app time, main's and Island's together (as Insights' "Today"), each app with its kind. */
private data class TodayUse(val totalMs: Long, val apps: List<AppStat>)

private fun todayUse(ctx: Context): TodayUse {
    val du = runCatching { val (s, e) = Usage.dayBounds(ctx, 0); Usage.range(ctx, s, e) }.getOrNull()
    val day = com.husarp.lockdown.guard.TrustedTime.local(ctx).toLocalDate()      // trusted time's day (as the limits)
    val island = runCatching { IslandLink.islandUse(ctx, day, day) }.getOrDefault(emptyMap())
    val mine = du?.apps?.map { AppTime(it.pkg, it.label.ifEmpty { it.pkg }, it.totalMs) } ?: emptyList()
    val apps = IslandUse.topApps(mine, island).map { TopRow(it.pkg, it.label, it.ms) }
    val stats = AppList.stats(apps, mine.map { it.pkg }.toSet(), island.keys, systemPkgs(ctx, apps.map { it.pkg }))
    val main = du?.totalMs ?: 0L
    return TodayUse(runCatching { IslandLink.withIsland(ctx, main, listOf(day)) }.getOrDefault(main), stats)
}

/** Always first: the master switch, today's screen time and the goal bar. */
@Composable
private fun StatusHero(cfg: Config, todayMs: Long) {
    val cs = MaterialTheme.colorScheme
    val guard = rememberGuard()
    val ctx = LocalContext.current
    val goalMin = cfg.settings.dailyGoalMin
    Card(color = cs.primaryContainer, contentColor = cs.onPrimaryContainer, shape = MaterialTheme.shapes.extraLarge) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(if (cfg.enabled) "PROTECTION ON" else "PROTECTION OFF",
                style = MaterialTheme.typography.labelMedium, modifier = Modifier.weight(1f))
            Switch(checked = cfg.enabled, onCheckedChange = { on ->
                if (on) Store.update { it.copy(enabled = true) }
                else guard(true) {
                    Store.update { it.copy(enabled = false) }
                    com.husarp.lockdown.data.BypassLog.record(ctx, com.husarp.lockdown.engine.Bypass.OFF)
                }
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
}

/** A card row: a small leading slot, the text (and a second line), and a trailing slot. */
@Composable
private fun CardRow(title: String, sub: String? = null, leading: (@Composable () -> Unit)? = null, trailing: (@Composable () -> Unit)? = null,
                    onClick: (() -> Unit)? = null) {
    val cs = MaterialTheme.colorScheme
    Row(Modifier.fillMaxWidth().let { if (onClick != null) it.clickable { onClick() } else it }.padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        leading?.invoke()
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (sub != null) Text(sub, style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        trailing?.invoke()
    }
}

private fun hhmm(t: LocalDateTime) = "%02d:%02d".format(t.hour, t.minute)

/** Shown only when something isn't working, one row each with Fix (the same screens as Settings and Guardrails). */
@Composable
private fun ProblemsCard(cfg: Config, onOpenSettings: (String?) -> Unit) {
    val ctx = LocalContext.current
    val fresh = com.husarp.lockdown.guard.rememberFresh()
    val a11y = remember(fresh) { Perms.a11y(ctx) }
    val usage = remember(fresh) { Usage.hasAccess(ctx) }
    val notes = remember(fresh) { Perms.notifications(ctx) }
    val vpn = remember(fresh, cfg.settings.siteFilterOn) {
        com.husarp.lockdown.engine.PermCheck.vpn(cfg.settings.siteFilterOn, com.husarp.lockdown.vpn.LockdownVpn.active)
    }
    val link by IslandLink.mainState.collectAsStateWithLifecycle()
    val island = link.state == "linked" && !remember(fresh, link) { IslandLink.healthy(ctx) }
    fun open(i: Intent) = runCatching { ctx.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
    val rows = buildList<Pair<String, () -> Unit>> {
        if (a11y != Perm.ON) add((if (a11y == Perm.NOT_RUNNING) "App blocking is on in Android, but not running" else "App blocking is off") to { open(BlockService.settingsIntent()) })
        if (!usage) add("Usage access is missing (screen time, limits)" to { open(Usage.accessIntent()) })
        if (!notes) add("Notifications are off (reminders, block notices)" to { open(Perms.notificationsIntent(ctx)) })
        if (vpn == Perm.NOT_RUNNING) add("The site filter is on, but not running" to { onOpenSettings("filter") })
        if (island) add("Lockdown in Island isn't checking in" to { IslandLink.openOtherProfile(ctx) })
    }
    if (rows.isEmpty()) return
    val info = HOME_CARDS.getValue("problems")
    HomePanel(info.name, info.icon, info.hue) {
        rows.forEach { (text, fix) ->
            CardRow(text, trailing = { TextButton(onClick = fix) { Text("Fix") } })
        }
    }
}

/** The mode that's on (blue), or a line to start one. */
@Composable
private fun ModeCard(modeState: ModeState?, onOpenModes: () -> Unit) {
    val blue = tone(Kinds.MODE.hue)
    val cs = MaterialTheme.colorScheme
    if (modeState == null) {
        Card(color = cs.surfaceContainerLow, onClick = onOpenModes, padding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp, vertical = 12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                IconTile(Kinds.MODE.icon, Kinds.MODE.hue, 28.dp)
                Text("No mode on", style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                Text("Start one ›", style = MaterialTheme.typography.labelLarge, color = blue.accent)
            }
        }
        return
    }
    Card(color = blue.container, contentColor = blue.onContainer, onClick = onOpenModes) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            Box(Modifier.size(40.dp).background(cs.surface.copy(alpha = 0.6f), androidx.compose.foundation.shape.RoundedCornerShape(12.dp)),
                contentAlignment = Alignment.Center) {
                Icon(Kinds.modeIcon(modeState.mode.id), null, Modifier.size(22.dp), tint = blue.accent)
            }
            Column(Modifier.weight(1f)) {
                Text("${modeState.mode.name} is on", style = MaterialTheme.typography.titleSmall)
                val bits = buildList {
                    modeState.until?.let { add("Until ${hhmm(it)}") }
                    if (modeState.locked) add("locked")
                    modeState.phase?.let { add("${it.first} · round ${it.third}") }
                }
                Text(bits.joinToString(" · ").ifEmpty { Modes.describe(modeState.mode) }, style = MaterialTheme.typography.bodySmall)
            }
            if (modeState.locked) Icon(Icons.Filled.Lock, null)
        }
    }
}

/** What's blocked now: the always-blocked counted in one line, a running mode in one line, then up to 5 blocked by
 *  another rule with their reason and end. Worked out on the main thread (the usage counters live there), on the
 *  minute tick or a change of the config. */
@Composable
private fun BlockedCard(cfg: Config, now: LocalDateTime, tick: Int, modeState: ModeState?, onOpenBlocking: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    val info = HOME_CARDS.getValue("blocked")
    val paused = remember(cfg.pause, tick) { Pause.until(cfg.pause, now) }
    val b = remember(cfg, tick) { Live.blockedNow(cfg, now) }
    HomePanel(info.name, info.icon, info.hue, action = "See all", onAction = onOpenBlocking) {
        when {
            !cfg.enabled -> CardRow("Protection is off", "Nothing is blocked until you turn it on.")
            paused != null -> CardRow("Your blocks are paused until ${hhmm(paused)}", "The protection lists and blocked words still work.",
                leading = { IconTile(Kinds.PAUSE.icon, Kinds.PAUSE.hue, 32.dp) })
            else -> {
                val byMode = Modes.blocking(modeState)
                if (b.alwaysApps == 0 && b.alwaysSites == 0 && b.alwaysGrey == 0 && b.rows.isEmpty() && !byMode) {
                    CardRow("Nothing blocked right now", leading = { IconTile(Icons.Filled.CheckCircle, Hue.GREEN, 32.dp) })
                }
                if (b.alwaysApps > 0 || b.alwaysSites > 0) {
                    val parts = listOfNotNull(
                        b.alwaysApps.takeIf { it > 0 }?.let { "%,d app%s".format(it, if (it == 1) "" else "s") },
                        b.alwaysSites.takeIf { it > 0 }?.let { "%,d site%s".format(it, if (it == 1) "" else "s") })
                    CardRow("Always blocked: " + parts.joinToString(", "), leading = { IconTile(Icons.Filled.Block, Hue.RED, 32.dp) })
                }
                if (b.alwaysGrey > 0) CardRow("Always grey: %,d app%s".format(b.alwaysGrey, if (b.alwaysGrey == 1) "" else "s"),
                    leading = { IconTile(Kinds.GREY.icon, Kinds.GREY.hue, 32.dp) })
                if (byMode && modeState != null) {
                    val what = if ("distracting" in modeState.mode.categories) "your distracting apps and sites" else "what it's set to block"
                    CardRow("${modeState.mode.name} blocks $what" + (modeState.until?.let { " until ${hhmm(it)}" } ?: ""),
                        leading = { IconTile(Kinds.modeIcon(modeState.mode.id), Kinds.MODE.hue, 32.dp) })
                }
                b.rows.forEach { r ->
                    val look = if (r.grey) Kinds.GREY else Kinds.reason(r.reason)
                    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        ItemIcon(r.item.type, r.item.target, r.item.name, 32.dp)
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                            Text(r.item.name, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Pill(if (r.grey) "Grey" else Kinds.reasonText(r.reason), look.hue, look.icon)
                        }
                        Text(r.until?.let { Kinds.untilText(it, now) } ?: "no end", style = MaterialTheme.typography.labelMedium, color = cs.onSurfaceVariant)
                    }
                }
                if (b.total > b.rows.size) Text("and ${b.total - b.rows.size} more", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
        }
    }
}

/** Limits about to run out (only items that have a limit are checked). Hidden while there are none. */
@Composable
private fun LowCard(cfg: Config, now: LocalDateTime, modeState: ModeState?) {
    // Cheap prefilter (rule type only, no engine) before the per-item engine check, so thousands of imported
    // items don't freeze Home. Only items that actually have a limit can be "running low".
    val lowItems = remember(cfg, now, modeState) {
        fun limited(r: com.husarp.lockdown.engine.Rule) = r.type == RuleType.TIME_LIMIT || r.type == RuleType.SWITCH_LIMIT || r.type == RuleType.SCHEDULED
        val groupLimited = cfg.groups.filter { g -> g.rules.any(::limited) || g.overrides.values.any { m -> m.values.any(::limited) } }.flatMap { it.memberIds }.toSet()
        cfg.items.asSequence()
            .filter { it.id in groupLimited || it.rules.any(::limited) }
            .map { it to Live.timeLeft(cfg, it, now, modeState) }
            .filter { (_, l) -> l.low && l.kind != TimeLeft.Kind.BLOCKED }.take(5).toList()   // a blocked one shows no "min left"
    }
    if (lowItems.isEmpty()) return
    val cs = MaterialTheme.colorScheme
    val amber = tone(Hue.AMBER).accent
    val info = HOME_CARDS.getValue("low")
    HomePanel(info.name, info.icon, info.hue) {
        lowItems.forEach { (item, left) ->
            Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                ItemIcon(item.type, item.target, item.name, 32.dp)
                Column(Modifier.weight(1f)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(item.name, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                        Text(TimeLeft.line(left, now), style = MaterialTheme.typography.titleSmall, color = amber)
                    }
                    Spacer(Modifier.height(6.dp))
                    LinearProgressIndicator(progress = { left.fraction }, modifier = Modifier.fillMaxWidth().height(4.dp),
                        color = amber, trackColor = cs.surfaceContainerHighest)
                }
            }
        }
    }
}

/** Today's top 5 apps (main and Island merged, as Insights' Top apps; only the kind chosen on "All apps"): time, and for
 *  an app with a limit the time left (amber, red once blocked). Each bar is the app's OWN time on the card's scale - apps
 *  sharing a group limit share its "min left", not its bar. "Show all" opens the full list. */
@Composable
private fun AppsCard(cfg: Config, now: LocalDateTime, today: TodayUse?, modeState: ModeState?, onOpenInsights: () -> Unit, onOpenApps: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    val ctx = LocalContext.current
    val info = HOME_CARDS.getValue("apps")
    val listed = remember(cfg.items) { cfg.items.filter { it.type == ItemType.APP }.associateBy { it.target.lowercase() } }
    val kind = remember { AppList.effective(AppListPrefs.kind(ctx), AppList.kinds(islandLinked(ctx))) }
    val all = remember(today, kind) { AppList.filter(today?.apps.orEmpty(), kind) }
    HomePanel(info.name, info.icon, info.hue, action = if (today?.apps.orEmpty().isNotEmpty()) "Show all (${all.size})" else null, onAction = onOpenApps, onClick = onOpenInsights) {
        val apps = all.take(5)
        if (today == null) Text("Loading…", color = cs.onSurfaceVariant)
        else if (apps.isEmpty()) Text(if (today.apps.isNotEmpty()) "No apps of this kind used today." else if (Usage.hasAccess(ctx)) "No screen time yet today." else "Allow usage access in Settings to see it.",
            style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
        val top = (apps.maxOfOrNull { it.ms } ?: 1L).coerceAtLeast(1L)
        apps.forEach { a ->
            val item = listed[a.pkg.lowercase()]
            val left = item?.let { Live.timeLeft(cfg, it, now, modeState) }?.takeIf { it.kind != TimeLeft.Kind.FREE }
            val bar = when {
                left == null -> tone(Hue.TEAL).accent
                left.kind == TimeLeft.Kind.BLOCKED -> tone(Hue.RED).accent
                else -> tone(Hue.AMBER).accent
            }
            Row(Modifier.fillMaxWidth().padding(vertical = 5.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                AppIcon(a.pkg, a.label, 32.dp)
                Column(Modifier.weight(1f)) {
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text(a.label, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                        if (left != null) Text(TimeLeft.line(left, now) + " · ", style = MaterialTheme.typography.labelMedium, color = bar,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(fmtDuration(a.ms), style = MaterialTheme.typography.titleSmall)
                    }
                    Spacer(Modifier.height(5.dp))
                    Box(Modifier.fillMaxWidth().height(4.dp).background(cs.surfaceContainerHighest, CircleShape)) {
                        Box(Modifier.fillMaxWidth(AppList.bar(a.ms, top)).height(4.dp).background(bar, CircleShape))
                    }
                }
            }
        }
    }
}

/** When bedtime starts (and how long until), wake-up, and the mode it turns on. */
@Composable
private fun BedtimeCard(cfg: Config, now: LocalDateTime, onOpenReminders: () -> Unit) {
    val info = HOME_CARDS.getValue("bedtime")
    val s = cfg.sleep
    HomePanel(info.name, info.icon, info.hue, onClick = onOpenReminders) {
        if (!s.on) {
            CardRow("Bedtime is off", trailing = { Text("Set up ›", style = MaterialTheme.typography.labelLarge, color = tone(Hue.INDIGO).accent) })
            return@HomePanel
        }
        val inside = inBedtime(now.hour * 60 + now.minute, s.bedtime, s.wake)
        val title = if (inside) "Bedtime now · until ${s.wake}" else {
            val bed = runCatching { com.husarp.lockdown.engine.Rules.parseHhmm(s.bedtime) }.getOrNull()
            val next = bed?.let { b -> now.toLocalDate().atTime(b).let { if (it.isAfter(now)) it else it.plusDays(1) } }
            "Bedtime ${s.bedtime}" + (next?.let { " · in " + fmtDuration(Duration.between(now, it).toMillis()) } ?: "")
        }
        val mode = ModesStore.modes.value.firstOrNull { it.id == s.mode }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleSmall)
                Text("Wake ${s.wake}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (mode != null) Pill(mode.name, Kinds.MODE.hue, Kinds.modeIcon(mode.id))
        }
    }
}

/** Focus, Block a site, Emergency. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun QuickCard(onOpenModes: () -> Unit, onOpenBlocking: () -> Unit, onOpenGuardrails: () -> Unit) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Chip("Focus", selected = false, icon = Icons.Filled.Bolt, iconTint = tone(Hue.BLUE).accent) { onOpenModes() }
        Chip("Block a site", selected = false, icon = Icons.Filled.Block, iconTint = tone(Hue.RED).accent) { onOpenBlocking() }
        Chip("Emergency", selected = false, icon = Kinds.EMERGENCY.icon, iconTint = tone(Kinds.EMERGENCY.hue).accent) { onOpenGuardrails() }
    }
}

/** Pause my blocks: starting it is in Guardrails (with its challenge); resuming is one tap, always free. */
@Composable
private fun PauseHomeCard(cfg: Config, now: LocalDateTime, onOpenGuardrails: () -> Unit) {
    val ctx = LocalContext.current
    val info = HOME_CARDS.getValue("pause")
    val until = Pause.until(cfg.pause, now)
    HomePanel(info.name, info.icon, info.hue) {
        if (until != null) CardRow("Paused until ${hhmm(until)}", trailing = { TextButton(onClick = { resumeBlocks(ctx) }) { Text("Resume") } })
        else CardRow("Your blocks are on", trailing = { TextButton(onClick = onOpenGuardrails) { Text("Pause…") } })
    }
}

/** Emergency unlocks left of each kind, and Use (the same dialog, kinds and checks as Guardrails). */
@Composable
private fun EmergencyCard(cfg: Config, now: LocalDateTime) {
    val info = HOME_CARDS.getValue("emergency")
    var use by remember { mutableStateOf(false) }
    val em = cfg.emergency
    val tiers = em.tierList()
    val left = tiers.associate { it.id to Emergency.usesLeft(cfg.allUses(), it, now, cfg.clock()) }
    HomePanel(info.name, info.icon, info.hue) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Column(Modifier.weight(1f)) {
                if (!em.enabled) Text("Emergency unlocks are off", style = MaterialTheme.typography.bodyMedium)
                else if (tiers.isEmpty()) Text("No emergency kinds set", style = MaterialTheme.typography.bodyMedium)
                tiers.takeIf { em.enabled }?.forEach { t ->
                    val u = left.getValue(t.id)
                    Text("${u.left} of ${u.allowed} left ${Emergency.PERIODS[t.per]} · ${t.minutes} min", style = MaterialTheme.typography.bodyMedium)
                }
            }
            val pink = tone(Kinds.EMERGENCY.hue)
            Button(onClick = { use = true }, enabled = em.enabled && left.values.any { it.left > 0 },
                colors = ButtonDefaults.buttonColors(containerColor = pink.container, contentColor = pink.onContainer)) { Text("Use") }
        }
    }
    if (use) EmergencyDialog(onDismiss = { use = false })
}

/** Lockdown in Island: checking in (green), not (red, with Fix), or Set up. Not shown in Lockdown's Island copy. */
@Composable
private fun IslandCard(onOpenSettings: (String?) -> Unit) {
    val ctx = LocalContext.current
    if (IslandLink.role(ctx) != com.husarp.lockdown.link.Role.MAIN) return
    val info = HOME_CARDS.getValue("island")
    val link by IslandLink.mainState.collectAsStateWithLifecycle()
    val fresh = com.husarp.lockdown.guard.rememberFresh()
    HomePanel(info.name, info.icon, info.hue) {
        when (link.state) {
            "linked" -> {
                val healthy = remember(fresh, link) { IslandLink.healthy(ctx) }
                val ago = agoText((System.currentTimeMillis() - link.lastSeen) / 1000)
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Linked", style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                    if (healthy) Pill("Checking in · $ago", Hue.GREEN, Icons.Filled.CheckCircle)
                    else {
                        Pill("Not checking in", Hue.RED, Icons.Filled.ErrorOutline)
                        TextButton(onClick = { IslandLink.openOtherProfile(ctx) }) { Text("Fix") }
                    }
                }
                remember(fresh, link) { com.husarp.lockdown.link.HelperWatcher.summary(ctx) }?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            "unlinking" -> CardRow("Unlinking", "Finishes when Lockdown in Island next checks in.")
            else -> CardRow("Not linked", trailing = { TextButton(onClick = { onOpenSettings("island") }) { Text("Set up ›") } })
        }
    }
}

/**
 * Settings > Appearance & Home: which cards Home shows and in what order (↑ / ↓ and a switch per card; Status is
 * always first). Looks only, so it saves at once - no Save, no challenge.
 */
@Composable
fun HomeLayoutEditor() {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val cs = MaterialTheme.colorScheme
    val list = HomeLayout.resolve(cfg.settings.home)
    fun save(f: (List<HomeCard>) -> List<HomeCard>?) =
        Store.update { c -> c.copy(settings = c.settings.copy(home = f(HomeLayout.resolve(c.settings.home)))) }
    Card(padding = androidx.compose.foundation.layout.PaddingValues(vertical = 8.dp)) {
        Text("Home", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp))
        Text("Choose the cards on Home and their order. Saved at once.", style = MaterialTheme.typography.bodySmall,
            color = cs.onSurfaceVariant, modifier = Modifier.padding(horizontal = 16.dp))
        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            IconTile(Kinds.PROTECTION.icon, Hue.ORANGE, 32.dp)
            Column(Modifier.weight(1f)) {
                Text("Status", style = MaterialTheme.typography.titleSmall)
                Text("Protection switch and today's time · always first", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
            Icon(Icons.Filled.Lock, "Always shown", tint = cs.onSurfaceVariant, modifier = Modifier.padding(end = 12.dp))
        }
        list.forEachIndexed { i, card ->
            val info = HOME_CARDS[card.id] ?: return@forEachIndexed
            Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 8.dp, top = 4.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                IconTile(info.icon, info.hue, 32.dp, on = card.on)
                Column(Modifier.weight(1f).padding(start = 8.dp)) {
                    Text(info.name, style = MaterialTheme.typography.titleSmall)
                    Text(info.about, style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
                // (small arrows: the name and its line keep room at phone width)
                IconButton(onClick = { save { HomeLayout.move(it, card.id, -1) } }, Modifier.size(36.dp), enabled = i > 0) {
                    Icon(Icons.Filled.ArrowUpward, "Move up", Modifier.size(20.dp))
                }
                IconButton(onClick = { save { HomeLayout.move(it, card.id, 1) } }, Modifier.size(36.dp), enabled = i < list.size - 1) {
                    Icon(Icons.Filled.ArrowDownward, "Move down", Modifier.size(20.dp))
                }
                Switch(checked = card.on, onCheckedChange = { save { l -> HomeLayout.toggle(l, card.id) } })
            }
        }
        TextButton(onClick = { save { null } }, modifier = Modifier.padding(horizontal = 8.dp)) { Text("Reset to default") }
    }
}
