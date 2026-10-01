package com.husarp.lockdown.engine

import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.format.DateTimeFormatter

/** usage(owner, bucket) -> seconds (or opens) recorded. */
typealias Usage = (String, String) -> Int

val noUsage: Usage = { _, _ -> 0 }

object Rules {
    private val WIN_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm")
    private const val NEARLY = 0.9          // this much of a limit used = "soon"
    private const val SOON_MIN = 10L        // a blocked stretch starting within this many minutes = "soon"

    fun parseHhmm(t: String): LocalTime {
        val (h, m) = t.trim().split(":")
        return LocalTime.of(h.toInt(), m.toInt())
    }

    private fun weekday(now: LocalDateTime) = now.dayOfWeek.value - 1   // 0=Mon..6=Sun

    // ---------- schedule ----------

    /** If [win] covers [now], when it ends; else null. A window belongs to the day it starts on. */
    fun windowUntil(win: Window, now: LocalDateTime): LocalDateTime? {
        val start = parseHhmm(win.start)
        val end = parseHhmm(win.end)
        val today = now.toLocalDate()
        val t = now.toLocalTime().withSecond(0).withNano(0)
        if (start < end) {
            return if (weekday(now) in win.days && t >= start && t < end) LocalDateTime.of(today, end) else null
        }
        // overnight (or full 24h when start == end)
        if (weekday(now) in win.days && t >= start) return LocalDateTime.of(today.plusDays(1), end)
        if (((weekday(now) - 1 + 7) % 7) in win.days && t < end) return LocalDateTime.of(today, end)
        return null
    }

    fun nextWindowStart(windows: List<Window>, now: LocalDateTime): LocalDateTime? {
        val starts = ArrayList<LocalDateTime>()
        for (offset in 0..7) {
            val d: LocalDate = now.toLocalDate().plusDays(offset.toLong())
            for (win in windows) if ((d.dayOfWeek.value - 1) in win.days) {
                val s = LocalDateTime.of(d, parseHhmm(win.start))
                if (s.isAfter(now)) starts.add(s)
            }
        }
        return starts.minOrNull()
    }

    /** If the schedule blocks at [now], until when (LocalDateTime.MAX = indefinitely), else null. */
    fun scheduleUntil(schedule: Schedule, now: LocalDateTime): LocalDateTime? {
        val ends = schedule.windows.mapNotNull { windowUntil(it, now) }
        if (schedule.mode == SchedMode.BLOCK) return ends.maxOrNull()
        if (ends.isNotEmpty()) return null                                   // allow mode, inside a window
        return nextWindowStart(schedule.windows, now) ?: LocalDateTime.MAX
    }

    // ---------- buckets ----------

    fun timeBucket(period: String, now: LocalDateTime, clock: LimitClock) =
        "$period:${clock.period(period, now).first}"

    fun openingBucket(eff: EffRule, period: String, now: LocalDateTime, clock: LimitClock) =
        "op:${eff.ruleKey}:${clock.period(period, now).first}"

    fun allowanceBucket(eff: EffRule, until: LocalDateTime) =
        "win:${eff.ruleKey}:${until.format(WIN_FMT)}"

    private fun timeLimits(r: Rule): Map<String, Int> = buildMap {
        r.dailyLimitMin?.let { put("day", it) }
        r.weeklyLimitMin?.let { put("week", it) }
        r.monthlyLimitMin?.let { put("month", it) }
    }

    private fun switchLimits(r: Rule): Map<String, Int> = buildMap {
        r.dailySwitchLimit?.let { put("day", it) }
        r.weeklySwitchLimit?.let { put("week", it) }
        r.monthlySwitchLimit?.let { put("month", it) }
    }

    // ---------- effective rules (item + groups, with per-member overrides) ----------

    fun effectiveRules(item: Item, groups: List<Group>): List<EffRule> {
        if (item.disabled) return emptyList()
        val me = "item:${item.id}"
        val out = ArrayList<EffRule>()
        for (r in item.rules) out.add(EffRule(r, me, me, me, "i${item.id}${r.type.name}"))
        for (g in groups) {
            if (item.id !in g.memberIds || g.disabled) continue
            val custom = g.overrides[item.id] ?: emptyMap()
            for (r in g.rules) {
                val t = r.type
                val eff: Rule
                val owner: String
                val pot: String
                if (t.name in custom) {                       // customised for this member: counted alone
                    eff = custom.getValue(t.name).copy(type = t)
                    owner = me; pot = me
                } else {                                       // inherited: group time/switch limits are one shared pot
                    eff = r
                    owner = if (t == RuleType.TIME_LIMIT || t == RuleType.SWITCH_LIMIT) "group:${g.id}" else me
                    pot = if (t == RuleType.SCHEDULED && r.allowanceShared) "group:${g.id}" else me
                }
                out.add(EffRule(eff, owner, me, pot, "g${g.id}${t.name}", g.id, g.name))
            }
        }
        return out
    }

    /** Every rule whose usage is COUNTED, enforced now or not (disabling stops blocking, not the clock). */
    fun countedRules(item: Item, groups: List<Group>): List<EffRule> =
        effectiveRules(item.copy(disabled = false), groups.map { it.copy(disabled = false) })

    // ---------- usage accounting: where recorded time / opens go ----------

    const val DEFAULT_VISIT_GAP_MIN = 5

    /** The item's own running totals (calendar day) - for stats, always counted. */
    fun dayBucket(now: LocalDateTime) = "day:${now.toLocalDate()}"

    fun switchBucket(now: LocalDateTime) = "sw:${now.toLocalDate()}"

    /** (owner, bucket) pairs to add elapsed seconds to while [item] is in use. */
    fun usageTargets(item: Item, effRules: List<EffRule>, now: LocalDateTime, clock: LimitClock = LimitClock.DEFAULT): Set<Pair<String, String>> {
        val me = "item:${item.id}"
        val out = HashSet<Pair<String, String>>()
        out.add(me to dayBucket(now))
        for (eff in effRules) {
            val r = eff.rule
            when (r.type) {
                RuleType.TIME_LIMIT -> for (p in timeLimits(r).keys) out.add(eff.usageOwner to timeBucket(p, now, clock))
                RuleType.SCHEDULED -> if ((r.allowanceMin ?: 0) > 0) {
                    val until = r.schedule?.let { scheduleUntil(it, now) }
                    if (until != null && until != LocalDateTime.MAX) out.add(eff.allowanceOwner to allowanceBucket(eff, until))
                }
                else -> {}
            }
        }
        return out
    }

    private fun openingTargets(eff: EffRule, now: LocalDateTime, clock: LimitClock): Set<Pair<String, String>> =
        switchLimits(eff.rule).keys.mapTo(HashSet()) { eff.usageOwner to openingBucket(eff, it, now, clock) }

    /** (owner, bucket) pairs to add 1 to when the user switches to [item]. */
    fun switchTargets(item: Item, effRules: List<EffRule>, now: LocalDateTime, clock: LimitClock = LimitClock.DEFAULT): Set<Pair<String, String>> {
        val out = HashSet<Pair<String, String>>()
        out.add("item:${item.id}" to switchBucket(now))
        for (eff in effRules) if (eff.rule.type == RuleType.SWITCH_LIMIT && eff.rule.switchMode == SwitchCount.SWITCH)
            out += openingTargets(eff, now, clock)
        return out
    }

    /** (owner, bucket) pairs to add 1 to for a new visit / launch of [item].
     *  Apps count when just [launched]; sites count when back after being away for at least the rule's gap
     *  ([awaySec] null = not used before). */
    fun visitTargets(item: Item, effRules: List<EffRule>, now: LocalDateTime, launched: Boolean, awaySec: Long?,
                     clock: LimitClock = LimitClock.DEFAULT): Set<Pair<String, String>> {
        val out = HashSet<Pair<String, String>>()
        for (eff in effRules) {
            val r = eff.rule
            if (r.type != RuleType.SWITCH_LIMIT || r.switchMode != SwitchCount.VISIT) continue
            val opened = if (item.type == ItemType.APP) launched
                         else awaySec == null || awaySec >= (r.visitGapMin ?: DEFAULT_VISIT_GAP_MIN) * 60L
            if (opened) out += openingTargets(eff, now, clock)
        }
        return out
    }

    // ---------- blocking decision ----------

    /** (reason, until) if this rule blocks at [now], else null. until=null means indefinitely. */
    fun ruleBlock(eff: EffRule, now: LocalDateTime, usage: Usage = noUsage, clock: LimitClock = LimitClock.DEFAULT): Pair<Reason, LocalDateTime?>? {
        val r = eff.rule
        when (r.type) {
            RuleType.PERMANENT -> return Reason.PERMANENT to null
            RuleType.TEMPORARY -> {
                val until = r.tempUntil?.let { LocalDateTime.parse(it) } ?: return null
                return if (now.isBefore(until)) Reason.TEMPORARY to until else null
            }
            RuleType.SCHEDULED -> {
                val sched = r.schedule ?: return null
                val until = scheduleUntil(sched, now) ?: return null
                val allowance = r.allowanceMin ?: 0
                if (allowance > 0 && usage(eff.allowanceOwner, allowanceBucket(eff, until)) < allowance * 60) return null
                return Reason.SCHEDULE to (if (until == LocalDateTime.MAX) null else until)
            }
            RuleType.TIME_LIMIT -> {
                val ends = timeLimits(r).mapNotNull { (p, limit) ->
                    if (usage(eff.usageOwner, timeBucket(p, now, clock)) >= limit * 60) clock.period(p, now).second else null
                }
                return if (ends.isNotEmpty()) Reason.LIMIT to ends.max() else null
            }
            RuleType.SWITCH_LIMIT -> {
                val ends = switchLimits(r).mapNotNull { (p, limit) ->
                    if (usage(eff.usageOwner, openingBucket(eff, p, now, clock)) > limit) clock.period(p, now).second else null
                }
                return if (ends.isNotEmpty()) Reason.SWITCHES to ends.max() else null
            }
        }
    }

    /** (reason, until, rule) for the most important rule blocking the item now, else null. */
    fun itemBlock(effRules: List<EffRule>, now: LocalDateTime, usage: Usage = noUsage,
                  clock: LimitClock = LimitClock.DEFAULT, unlockedUntil: LocalDateTime? = null): Block? {
        if (unlockedUntil != null && now.isBefore(unlockedUntil)) return null      // emergency unlock
        val active = effRules.mapNotNull { eff -> ruleBlock(eff, now, usage, clock)?.let { Block(it.first, it.second, eff) } }
        return active.minByOrNull { it.reason.ordinal }
    }

    /** "blocked" / "soon" (nearly out of time, or hours start soon) / "allowed" - for a rule's status colour. */
    fun ruleState(eff: EffRule, now: LocalDateTime, usage: Usage = noUsage, clock: LimitClock = LimitClock.DEFAULT): String {
        if (ruleBlock(eff, now, usage, clock) != null) return "blocked"
        val r = eff.rule
        when (r.type) {
            RuleType.TIME_LIMIT, RuleType.SWITCH_LIMIT -> {
                val timeBased = r.type == RuleType.TIME_LIMIT
                val fractions = (if (timeBased) timeLimits(r) else switchLimits(r)).map { (p, limit) ->
                    if (timeBased) usage(eff.usageOwner, timeBucket(p, now, clock)).toDouble() / maxOf(limit * 60, 1)
                    else usage(eff.usageOwner, openingBucket(eff, p, now, clock)).toDouble() / maxOf(limit, 1)
                }
                return if (fractions.isNotEmpty() && fractions.max() >= NEARLY) "soon" else "allowed"
            }
            RuleType.SCHEDULED -> {
                val sched = r.schedule ?: return "allowed"
                val start = nextWindowStart(sched.windows, now)
                if (start != null && !start.isAfter(now.plusMinutes(SOON_MIN))) return "soon"
            }
            else -> {}
        }
        return "allowed"
    }
}
