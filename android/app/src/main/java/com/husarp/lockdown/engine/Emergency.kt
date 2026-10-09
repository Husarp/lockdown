package com.husarp.lockdown.engine

import kotlinx.serialization.Serializable
import java.time.LocalDateTime
import java.util.UUID

/** An emergency kind ("tier"): [uses] per [per] ("day" | "week" | "month"), each unblocking for [minutes]. The id
 *  never changes and is never reused: uses count, and the challenge compares, by id. */
@Serializable
data class EmergencyTier(val id: String, val uses: Int, val per: String, val minutes: Int)

/** One emergency use (PC emergency_unlocks row): started [at], of kind [tier], freeing [items] and/or pausing the
 *  bedtime and break [alerts] until [until] (ISO, trusted time). Tier "" (an unlock from before kinds) counts for none. */
@Serializable
data class EmergencyUse(val at: String, val tier: String, val until: String,
                        val items: List<String> = emptyList(), val alerts: Boolean = false)

/**
 * Emergency unlock, ported from the PC emergency.py: unblock picked items (and/or pause the bedtime and break alerts)
 * for a while. Several kinds can be set, each with its own count per day / week / month (LimitClock periods) and
 * length; one unlock (of one or more items) is one use of the kind picked. Pure.
 */
object Emergency {
    val MINUTE_OPTIONS = listOf(5, 10, 15, 20, 30, 45, 60)
    val MAX_MINUTES = MINUTE_OPTIONS.max()
    const val LEGACY_TIER = "t1"          // the single setting from before kinds, and every use made under it
    const val MAX_TIERS = 5
    const val MAX_USES = 10
    val PERIODS = linkedMapOf("day" to "today", "week" to "this week", "month" to "this month")

    data class Uses(val left: Int, val allowed: Int, val reset: LocalDateTime)

    /** A started use, or why not. */
    data class Started(val use: EmergencyUse? = null, val error: String = "")

    /** Kinds as they can be used: no id, no uses or an unknown period dropped; uses at most [MAX_USES] and minutes
     *  1..[MAX_MINUTES]; the first of a repeated id; at most [MAX_TIERS]. */
    fun clean(tiers: List<EmergencyTier>): List<EmergencyTier> {
        val seen = HashSet<String>()
        return tiers.filter { it.id.isNotBlank() && it.uses >= 1 && it.per in PERIODS }
            .map { it.copy(uses = it.uses.coerceAtMost(MAX_USES), minutes = it.minutes.coerceIn(1, MAX_MINUTES)) }
            .filter { seen.add(it.id) }
            .take(MAX_TIERS)
    }

    /** A fresh kind id, never one in [taken] (nor "t1", which isn't hex). */
    fun newId(taken: Collection<String> = emptyList()): String =
        generateSequence { UUID.randomUUID().toString().take(8) }.first { it !in taken }

    private fun at(s: String) = runCatching { LocalDateTime.parse(s) }.getOrNull()

    /** (uses left, uses allowed, when the count resets) of [tier] in its current period. */
    fun usesLeft(uses: List<EmergencyUse>, tier: EmergencyTier, now: LocalDateTime, clock: LimitClock = LimitClock.DEFAULT): Uses {
        val (key, reset) = clock.period(tier.per, now)
        // A use dated after now (the clock was set forward, then back) still counts as used.
        val used = uses.count { u -> u.tier == tier.id && at(u.at)?.let { it.isAfter(now) || clock.period(tier.per, it).first == key } == true }
        return Uses(maxOf(0, tier.uses - used), tier.uses, reset)
    }

    /** "Emergency (N left)": the uses left of every kind together, 0 while emergencies are off. */
    fun available(enabled: Boolean, tiers: List<EmergencyTier>, uses: List<EmergencyUse>, now: LocalDateTime,
                  clock: LimitClock = LimitClock.DEFAULT): Int =
        if (!enabled) 0 else tiers.sumOf { usesLeft(uses, it, now, clock).left }

    /** The kind offered first: the shortest with a use left (the smallest way out), or null. */
    fun shortest(tiers: List<EmergencyTier>, uses: List<EmergencyUse>, now: LocalDateTime, clock: LimitClock = LimitClock.DEFAULT) =
        tiers.filter { usesLeft(uses, it, now, clock).left > 0 }.minByOrNull { it.minutes }

    /** A use of kind [tierId] at [now] (trusted time) for [items] and/or the [alerts], or why not (PC emergency.unlock).
     *  [permanent]: an item blocked for good, which the emergency never frees. */
    fun start(enabled: Boolean, tiers: List<EmergencyTier>, uses: List<EmergencyUse>, tierId: String, items: List<String>,
              alerts: Boolean, now: LocalDateTime, clock: LimitClock = LimitClock.DEFAULT,
              permanent: (String) -> Boolean = { false }): Started {
        if (!enabled) return Started(error = "Emergency unlocks are off.")
        if (items.isEmpty() && !alerts) return Started(error = "Pick something to unlock.")
        val tier = tiers.firstOrNull { it.id == tierId } ?: return Started(error = "That kind is gone - pick another.")
        if (usesLeft(uses, tier, now, clock).left <= 0)
            return Started(error = if (tiers.size == 1) "No emergency unlocks left." else "No ${tier.minutes}-min emergency unlocks left ${PERIODS[tier.per]}.")
        if (items.any(permanent)) return Started(error = "A permanent block can't be unlocked.")
        return Started(EmergencyUse(now.toString(), tier.id, now.plusMinutes(tier.minutes.toLong()).toString(), items.distinct(), alerts))
    }

    /** [u]'s end if it's running at [now]: begun (at most a minute ahead), not over, and no longer than any kind can
     *  run - one started with the clock set forward (then back) or written into a file doesn't hold for days. */
    private fun runningUntil(u: EmergencyUse, now: LocalDateTime): LocalDateTime? {
        val start = at(u.at) ?: return null
        val end = at(u.until) ?: return null
        return end.takeIf { !start.isAfter(now.plusMinutes(1)) && now.isBefore(it) && !it.isAfter(start.plusMinutes(MAX_MINUTES + 1L)) }
    }

    /** When [itemId]'s emergency unlock ends: the latest running use that covers it, or null. */
    fun itemUntil(uses: List<EmergencyUse>, itemId: String, now: LocalDateTime): LocalDateTime? =
        uses.filter { itemId in it.items }.mapNotNull { runningUntil(it, now) }.maxOrNull()

    /** When an emergency's pause of the bedtime and break alerts ends, or null. */
    fun alertsPausedUntil(uses: List<EmergencyUse>, now: LocalDateTime): LocalDateTime? =
        uses.filter { it.alerts }.mapNotNull { runningUntil(it, now) }.maxOrNull()
}
