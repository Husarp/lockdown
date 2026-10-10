package com.husarp.lockdown.ui

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.TrendingUp
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Contrast
import androidx.compose.material.icons.filled.Emergency
import androidx.compose.material.icons.filled.EventBusy
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.HourglassBottom
import androidx.compose.material.icons.filled.Layers
import androidx.compose.material.icons.filled.PauseCircle
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.TextFields
import androidx.compose.material.icons.filled.Timer
import androidx.compose.material.icons.filled.TouchApp
import androidx.compose.material.icons.filled.VpnLock
import androidx.compose.material.icons.filled.WifiOff
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.CenterFocusStrong
import androidx.compose.material.icons.filled.DoNotDisturbOn
import androidx.compose.material.icons.filled.School
import androidx.compose.material.icons.filled.SelfImprovement
import androidx.compose.material.icons.filled.Work
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.runtime.Immutable
import androidx.compose.ui.graphics.vector.ImageVector
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Reason
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.SchedMode
import com.husarp.lockdown.ui.theme.Hue
import java.time.LocalDateTime

/*
 * What each colour means (the redesign's one table): a hue and an icon per kind of block, state, way and category.
 * Colour never stands alone - there's always the icon and a word with it. No logic here, only looks.
 */

/** A hue and an icon. */
@Immutable
data class Look(val hue: Hue, val icon: ImageVector)

/** One small coloured label (a rule, a group, a way). [hue] null or [outlined]: the plain outlined style. */
@Immutable
data class PillSpec(val text: String, val hue: Hue?, val icon: ImageVector? = null, val outlined: Boolean = false)

object Kinds {
    val MODE = Look(Hue.BLUE, Icons.Filled.Layers)
    val PROTECTION = Look(Hue.TEAL, Icons.Filled.Shield)
    val WORDS = Look(Hue.TEAL, Icons.Filled.TextFields)
    val FILTER = Look(Hue.TEAL, Icons.Filled.VpnLock)
    val GREY = Look(Hue.SLATE, Icons.Filled.Contrast)
    val EMERGENCY = Look(Hue.PINK, Icons.Filled.Emergency)
    val PAUSE = Look(Hue.SLATE, Icons.Filled.PauseCircle)

    /** A mode's icon: the built-ins their own, a mode you made the layers. */
    fun modeIcon(id: String): ImageVector = when (id) {
        "work" -> Icons.Filled.Work
        "study" -> Icons.Filled.School
        "focus" -> Icons.Filled.CenterFocusStrong
        "dnd" -> Icons.Filled.DoNotDisturbOn
        "relax" -> Icons.Filled.SelfImprovement
        else -> Icons.Filled.Layers
    }

    fun rule(t: RuleType): Look = when (t) {
        RuleType.PERMANENT -> Look(Hue.RED, Icons.Filled.Block)
        RuleType.TIME_LIMIT -> Look(Hue.AMBER, Icons.Filled.Timer)
        RuleType.SWITCH_LIMIT -> Look(Hue.AMBER, Icons.Filled.TouchApp)
        RuleType.SCHEDULED -> Look(Hue.INDIGO, Icons.Filled.Schedule)
        RuleType.TEMPORARY -> Look(Hue.VIOLET, Icons.Filled.EventBusy)
    }

    fun reason(r: Reason): Look = when (r) {
        Reason.PERMANENT -> rule(RuleType.PERMANENT)
        Reason.TEMPORARY -> rule(RuleType.TEMPORARY)
        Reason.LIMIT -> rule(RuleType.TIME_LIMIT)
        Reason.SWITCHES -> rule(RuleType.SWITCH_LIMIT)
        Reason.SCHEDULE -> rule(RuleType.SCHEDULED)
    }

    /** A blocked item's reason in a word or two, for its pill. */
    fun reasonText(r: Reason): String = when (r) {
        Reason.PERMANENT -> "Always"
        Reason.TEMPORARY -> "Temporary"
        Reason.LIMIT -> "Limit reached"
        Reason.SWITCHES -> "Opens used"
        Reason.SCHEDULE -> "Hours"
    }

    /** A status chip's look: only what needs attention is filled (blocked, grey, running low). */
    fun status(s: Status, grey: Boolean = false): Look = when (s) {
        Status.BLOCKED -> if (grey) GREY else rule(RuleType.PERMANENT)
        Status.SOON -> Look(Hue.AMBER, Icons.Filled.HourglassBottom)
        Status.ALLOWED -> Look(Hue.GREEN, Icons.Outlined.CheckCircle)
        Status.PAUSED -> PAUSE
    }

    /** Productive / Neutral / Distracting (null or anything else: no category, slate). */
    fun category(key: String?): Look = when (key) {
        "productive" -> Look(Hue.GREEN, Icons.AutoMirrored.Filled.TrendingUp)
        "distracting" -> Look(Hue.PINK, Icons.Filled.Bolt)
        else -> Look(Hue.SLATE, Icons.Filled.Remove)
    }

    /** How an app or a site is blocked: "close" / "grey" (apps), "dns" / "back" / "close" (sites). */
    fun way(key: String, site: Boolean): Look = when {
        !site && key == "grey" -> GREY
        !site -> Look(Hue.SLATE, Icons.Filled.Close)
        key == "dns" -> Look(Hue.TEAL, Icons.Filled.WifiOff)
        key == "back" -> Look(Hue.TEAL, Icons.AutoMirrored.Filled.ArrowBack)
        else -> Look(Hue.TEAL, Icons.Filled.Home)
    }

    // ---------- pills ----------

    private fun short(min: Int): String = if (min >= 60) (if (min % 60 == 0) "${min / 60}h" else "${min / 60}h ${min % 60}m") else "${min}m"

    /** A rule in a few words: "Always", "1h a day", "10 opens a day", "22:00–07:00", "Allowed 09:00–17:00",
     *  "until 18:00". [now]: for a temporary block's day. */
    fun ruleText(r: Rule, now: LocalDateTime? = null): String = when (r.type) {
        RuleType.PERMANENT -> "Always"
        RuleType.TIME_LIMIT -> when {
            Rules.dayLimits(r) != null -> "Per weekday"
            r.dailyLimitMin != null -> "${short(r.dailyLimitMin)} a day"
            r.weeklyLimitMin != null -> "${short(r.weeklyLimitMin)} a week"
            r.monthlyLimitMin != null -> "${short(r.monthlyLimitMin)} a month"
            else -> "Time limit"
        }
        RuleType.SWITCH_LIMIT -> when {
            r.dailySwitchLimit != null -> "${r.dailySwitchLimit} opens a day"
            r.weeklySwitchLimit != null -> "${r.weeklySwitchLimit} opens a week"
            r.monthlySwitchLimit != null -> "${r.monthlySwitchLimit} opens a month"
            else -> "Opening limit"
        }
        RuleType.SCHEDULED -> {
            val ws = r.schedule?.windows.orEmpty()
            val first = ws.firstOrNull()?.let { "${it.start}–${it.end}" + if (ws.size > 1) " +${ws.size - 1}" else "" } ?: "Hours"
            if (r.schedule?.mode == SchedMode.ALLOW) "Allowed $first" else first
        }
        RuleType.TEMPORARY -> r.tempUntil?.let { runCatching { LocalDateTime.parse(it) }.getOrNull() }?.let { untilText(it, now) } ?: "Temporary"
    }

    /** "until 18:00" today, "until Tue 18:00" within the week, else "until 12 Oct". */
    fun untilText(t: LocalDateTime, now: LocalDateTime?): String {
        val hm = "%02d:%02d".format(t.hour, t.minute)
        val days = if (now == null) 0L else java.time.temporal.ChronoUnit.DAYS.between(now.toLocalDate(), t.toLocalDate())
        return when {
            days == 0L -> "until $hm"
            days in 1..6 -> "until " + t.format(java.time.format.DateTimeFormatter.ofPattern("EEE")) + " $hm"
            else -> "until " + t.format(java.time.format.DateTimeFormatter.ofPattern("d MMM"))
        }
    }

    fun rulePill(r: Rule, now: LocalDateTime? = null, outlined: Boolean = false, plus: Boolean = false): PillSpec {
        val look = rule(r.type)
        return PillSpec((if (plus) "+ " else "") + ruleText(r, now), look.hue, look.icon, outlined)
    }

    fun rulePills(rules: List<Rule>, now: LocalDateTime? = null): List<PillSpec> = rules.map { rulePill(it, now) }

    fun groupPill(g: Group, text: String = g.name): PillSpec = PillSpec(text, GroupLook.hue(g), GroupLook.icon(g))

    /** An item's row on the Blocking list: its own rules, then one pill per group it's in (so an item ruled only by a
     *  group says why it's on the list). Nothing at all: "App" / "Site", outlined. */
    fun itemPills(item: Item, groups: List<Group>, now: LocalDateTime? = null): List<PillSpec> {
        val own = rulePills(item.rules, now)
        val inGroups = groups.filter { item.id in it.memberIds }.map { groupPill(it) }
        return (own + inGroups).ifEmpty { listOf(PillSpec(if (item.type == ItemType.APP) "App" else "Site", null, outlined = true)) }
    }

    /**
     * A member's row in its group: the group's rules ("Group rules", in the group's look), then each extra limit set
     * for it here ("+ 30m a day"), then the way it adds in this group (closed instead of grey; a site's extra way),
     * then its own rules outside the group, outlined.
     */
    fun memberPills(group: Group, item: Item, now: LocalDateTime? = null): List<PillSpec> = buildList {
        add(groupPill(group, "Group rules"))
        (group.overrides[item.id] ?: emptyMap()).toSortedMap().forEach { (typeName, r) ->
            val t = runCatching { RuleType.valueOf(typeName) }.getOrNull() ?: return@forEach
            add(rulePill(r.copy(type = t), now, plus = true))
        }
        group.memberBlocks[item.id]?.let { own ->
            val site = item.type == ItemType.SITE
            own.split(",").map { it.trim() }.filter { it.isNotEmpty() }.distinct().forEach { key ->
                val look = way(key, site)
                add(PillSpec(wayText(key, site), look.hue, look.icon))
            }
        }
        item.rules.forEach { add(rulePill(it, now, outlined = true)) }
    }

    /** A way in a word or two, for a pill. */
    fun wayText(key: String, site: Boolean): String = when {
        !site && key == "grey" -> "Grey"
        !site -> "Closed"
        key == "dns" -> "Cut connection"
        key == "back" -> "Go back"
        else -> "Leave browser"
    }

    /** A site's letter-tile hue: stable per first hostname, never RED or ORANGE (a site shouldn't look blocked or
     *  like the brand). */
    private val SITE_HUES = listOf(Hue.AMBER, Hue.GREEN, Hue.TEAL, Hue.BLUE, Hue.INDIGO, Hue.VIOLET, Hue.PINK, Hue.SLATE)

    fun siteHue(target: String): Hue {
        val host = target.trim().lowercase().split(" ").firstOrNull { it.isNotEmpty() }?.removePrefix("www.") ?: ""
        return SITE_HUES[Math.floorMod(host.hashCode(), SITE_HUES.size)]
    }
}
