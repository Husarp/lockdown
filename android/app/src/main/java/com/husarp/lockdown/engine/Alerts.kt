package com.husarp.lockdown.engine

import kotlinx.serialization.Serializable
import java.time.Duration
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import kotlin.math.ceil

/**
 * Notification settings (PC Notifications page). [visits] / [messages] / [cooldownMin] are the blocked-visit alerts:
 * on the phone the block notice is that alert, so they decide whether it explains itself and what it says. The rest
 * are the warnings before a block starts and the "block started" notices. All in the config, so the Island copy has
 * them too. Nothing here loosens a block: no challenge.
 */
@Serializable
data class AlertsCfg(
    val visits: Map<String, Boolean> = emptyMap(),     // reason -> explain it (missing = on)
    val messages: Map<String, String> = emptyMap(),    // reason -> your own text (missing = the default)
    val cooldownMin: Int = 30,                         // the same thing explained at most once per this
    val warn: Boolean = true,                          // warn before something gets blocked
    val warnMin: Int = 5,
    val repeatMin: Int = 0,                            // while you're using it, again every (0 = don't)
    val started: Boolean = true,                       // notify when a block starts
)

/** Message formatting and the per-item / per-reason / cooldown check, ported from the PC alerts.py. Pure. */
object Alerts {
    /** reason -> (label in settings, text for {reason}). */
    val REASONS: Map<String, Pair<String, String>> = linkedMapOf(
        "permanent" to ("Permanently blocked" to "permanently blocked"),
        "schedule" to ("Outside its allowed hours" to "blocked at this time"),
        "limit" to ("Over its time limit" to "over its time limit"),
        "switches" to ("Opened too often" to "opened too many times"),
        "temporary" to ("Temporarily blocked" to "temporarily blocked"),
        "mode" to ("Blocked by a mode" to "blocked while a mode is on"),
        "protection" to ("On a protection list" to "on a blocked list"),
    )
    val DEFAULT_MESSAGES: Map<String, String> = mapOf(
        "permanent" to "{site} is permanently blocked.",
        "schedule" to "{site} is blocked until {until}.",
        "limit" to "{site}: time limit reached - blocked until {until}.",
        "switches" to "{site}: opened too many times - blocked until {until}.",
        "temporary" to "{site} is blocked for now - until {until}.",
        "mode" to "{site} is blocked while this mode is on - until {until}.",
        "protection" to "{site} is blocked - it's {reason}.",
    )
    val COOLDOWN_OPTIONS = listOf(1, 5, 15, 30, 60)
    val WARN_MINUTE_OPTIONS = listOf(1, 2, 5, 10, 15, 20, 30, 60)
    val REPEAT_OPTIONS = listOf(0, 1, 2, 5, 10)
    private val DAY_NAMES = listOf("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    private val HM = DateTimeFormatter.ofPattern("HH:mm")

    fun reasonKey(r: Reason): String = when (r) {
        Reason.PERMANENT -> "permanent"
        Reason.TEMPORARY -> "temporary"
        Reason.LIMIT -> "limit"
        Reason.SWITCHES -> "switches"
        Reason.SCHEDULE -> "schedule"
    }

    /** "21:00" today, "Tuesday 08:00" another day, "further notice" with no end. */
    fun untilText(until: LocalDateTime?, now: LocalDateTime): String {
        if (until == null || until == LocalDateTime.MAX) return "further notice"
        return whenText(until, now)
    }

    fun whenText(t: LocalDateTime, now: LocalDateTime): String =
        if (t.toLocalDate() == now.toLocalDate()) t.format(HM) else "${DAY_NAMES[t.dayOfWeek.value - 1]} ${t.format(HM)}"

    /** Your own text for [reason], or null when you haven't written one. */
    fun ownMessage(cfg: AlertsCfg, reason: String): String? =
        cfg.messages[reason]?.trim()?.takeIf { it.isNotEmpty() && it != DEFAULT_MESSAGES[reason] }

    /** [template] with {site}, {reason} and {until} filled in (an unknown {placeholder} stays as typed). */
    fun format(template: String, site: String, reason: String, until: LocalDateTime?, now: LocalDateTime): String =
        template.replace("{site}", site).replace("{reason}", REASONS[reason]?.second ?: reason)
            .replace("{until}", untilText(until, now))

    /** Is the block notice for this one explained? [itemNotify]: the item's own choice ("on" / "off" / null = follow
     *  the reason's setting); [lastShownMs]: when this item was last explained (the cooldown). */
    fun shouldNotify(itemNotify: String?, enabled: Boolean, lastShownMs: Long?, nowMs: Long, cooldownMin: Int): Boolean {
        if (itemNotify == "off" || (itemNotify != "on" && !enabled)) return false
        return lastShownMs == null || nowMs - lastShownMs >= cooldownMin * 60_000L
    }

    fun enabled(cfg: AlertsCfg, reason: String) = cfg.visits[reason] != false
}

/** What's coming for an item that isn't blocked: when, and the rule (null: an emergency unlock ending). */
data class NextBlock(val at: LocalDateTime, val eff: EffRule?)

/**
 * Warnings before blocks start, repeated reminders while you use the item, "block started" notices and "you're on
 * your allowance" notes - the PC's BlockWatcher. Blocks coming from the same group are announced together. Keep one
 * instance; call [check] every few seconds. Messages about what you're using right now land in [urgent].
 */
class BlockWatcher {
    val warned = HashMap<String, LocalDateTime>()
    var prevBlocked: Set<String>? = null
    val urgent = HashSet<String>()

    private class Upcoming(val at: LocalDateTime, val eff: EffRule?, val names: MutableList<String> = ArrayList(), var inUse: Boolean = false)
    private class Started(val eff: EffRule, var reason: Reason?, var until: LocalDateTime?, val names: MutableList<String> = ArrayList(), var inUse: Boolean = false)

    private fun key(item: Item, nb: NextBlock): String {
        val eff = nb.eff ?: return "unlock|${nb.at}"
        val source = eff.groupId?.let { "group:$it" } ?: "item:${item.id}"
        return when {
            eff.rule.type == RuleType.TIME_LIMIT -> "$source|limit|${nb.at.toLocalDate()}"   // the predicted time drifts
            eff.rule.type == RuleType.SCHEDULED && (eff.rule.allowanceMin ?: 0) > 0 -> "$source|allowance|${nb.at.toLocalDate()}"
            else -> "$source|schedule|${nb.at.withSecond(0).withNano(0)}"
        }
    }

    /**
     * [inUse]: ids of the items in front now. [unlockOf]: an item's emergency unlock end, or null. [paused]: "Pause my
     * blocks" is running (nothing blocks; its end is said by its own note). Returns the messages to show.
     */
    fun check(items: List<Item>, groups: List<Group>, usage: Usage, clock: LimitClock, now: LocalDateTime,
              inUse: Set<String>, cfg: AlertsCfg, paused: Boolean = false, unlockOf: (String) -> LocalDateTime? = { null }): List<String> {
        val messages = ArrayList<String>()
        urgent.clear()
        warned.entries.removeIf { Duration.between(it.value, now).toHours() >= 48 }
        val warnSec = cfg.warnMin * 60L
        val repeatSec = cfg.repeatMin * 60L
        val upcoming = LinkedHashMap<String, Upcoming>()
        val blocked = LinkedHashMap<String, Pair<Item, Block>>()
        if (!paused) for (item in items) {
            val eff = Rules.effectiveRules(item, groups)
            if (eff.isEmpty()) continue
            val unlock = unlockOf(item.id)
            val block = Rules.itemBlock(eff, now, usage, clock, unlock)
            if (block != null) { blocked[item.id] = item to block; continue }
            val nb = Rules.nextBlock(eff, now, usage, clock, item.id in inUse, unlock) ?: continue
            if (Duration.between(now, nb.at).seconds > warnSec) continue
            val e = upcoming.getOrPut(key(item, nb)) { Upcoming(nb.at, nb.eff) }
            e.names.add(item.name)
            e.inUse = e.inUse || item.id in inUse
        }

        if (cfg.warn) {
            val spending = allowanceNotices(items, groups, usage, now, inUse)
            urgent.addAll(spending)
            messages += spending
            for ((k, e) in upcoming) {
                val last = warned[k]
                if (last == null || (e.inUse && repeatSec > 0 && Duration.between(last, now).seconds >= repeatSec)) {
                    warned[k] = now
                    val text = warning(e, now)
                    if (e.inUse) urgent.add(text)
                    messages.add(text)
                }
            }
        }

        val prev = prevBlocked
        if (prev != null && cfg.started) {
            val started = LinkedHashMap<String, Started>()
            for ((id, pair) in blocked) {
                if (id in prev) continue
                val (item, block) = pair
                val source = block.rule.groupId?.let { "group:$it" } ?: "item"
                val e = started.getOrPut(source) { Started(block.rule, block.reason, block.until) }
                e.names.add(item.name)
                e.inUse = e.inUse || id in inUse
                if (e.until != block.until || e.reason != block.reason) { e.until = null; e.reason = null }   // no shared end
            }
            for (e in started.values) {
                val text = startedText(e, now)
                if (e.inUse) urgent.add(text)
                messages.add(text)
            }
        }
        prevBlocked = blocked.keys.toSet()
        return messages
    }

    /** Opened inside its blocked hours with "N minutes allowed" left: said once per stretch. */
    private fun allowanceNotices(items: List<Item>, groups: List<Group>, usage: Usage, now: LocalDateTime, inUse: Set<String>): List<String> {
        data class Found(val left: Int, val allowed: Int, val until: LocalDateTime, val name: String)
        val found = LinkedHashMap<String, Found>()
        for (item in items) {
            if (item.id !in inUse) continue
            for (eff in Rules.effectiveRules(item, groups)) {
                val (used, allowed, until) = Rules.allowanceLeft(eff, now, usage) ?: continue
                val left = allowed - used
                if (left <= 0) continue
                val pot = eff.allowanceOwner       // a shared group allowance is one notice, not one per member
                val k = "$pot|allowance|$until"
                if (k in warned) continue
                if (k !in found || left < found[k]!!.left)
                    found[k] = Found(left, allowed, until, if (pot.startsWith("group:")) eff.groupName ?: item.name else item.name)
            }
        }
        return found.map { (k, f) ->
            warned[k] = now
            "${f.name} is blocked now - you have ${maxOf(1, ceil(f.left / 60.0).toInt())} min of your ${f.allowed / 60} min " +
                "allowance left (until ${f.until.format(DateTimeFormatter.ofPattern("HH:mm"))})."
        }
    }

    private fun names(n: List<String>) = n.toSortedSet().joinToString(", ")

    private fun warning(e: Upcoming, now: LocalDateTime): String {
        val minutes = maxOf(1L, ceil(Duration.between(now, e.at).seconds / 60.0).toLong())
        val names = names(e.names)
        val eff = e.eff ?: return "Emergency unlock ends in $minutes min: $names will be blocked again."
        val r = eff.rule
        val group = eff.groupName
        val at = Alerts.whenText(e.at, now)
        return when {
            r.type == RuleType.TIME_LIMIT -> "${if (group != null) "$group ($names)" else names}: $minutes min of the time limit left."
            (r.allowanceMin ?: 0) > 0 -> "$names: $minutes min of your allowance left - then it's blocked."
            group != null -> "$group starts in $minutes min ($at): $names will be blocked."
            else -> "$names will be blocked in $minutes min ($at)."
        }
    }

    /** One line for the group (and one for everything else), not one per app and site. */
    private fun startedText(e: Started, now: LocalDateTime): String {
        val until = e.until?.let { " until ${Alerts.whenText(it, now)}" } ?: ""
        val why = when (e.reason) {
            Reason.LIMIT -> " - time limit reached"
            Reason.SWITCHES -> " - opened too many times"
            Reason.TEMPORARY -> " (temporary block)"
            else -> ""
        }
        val names = names(e.names)
        val count = e.names.toSet().size
        e.eff.groupName?.let { g -> return "$g started - ${if (count == 1) names else "$count things"} blocked$until$why." }
        return if (count == 1) "$names is now blocked$until$why." else "$count things are now blocked$until$why."
    }
}
