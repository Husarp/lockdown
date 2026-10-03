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

    /** The amount on each weekday (Mon..Sun, null = no limit that day) of a daily time limit set per weekday,
     *  or null for one amount every day. */
    fun dayLimits(r: Rule): List<Int?>? =
        r.dailyLimitDays?.takeIf { it.size == 7 }?.map { if (it != null && it > 0) it else null }

    /** (dailyLimitMin, dailyLimitDays) for the amounts on Mon..Sun: the same every day is kept as one amount (how
     *  every limit was before), otherwise the per-weekday list. */
    fun makeDayLimits(amounts: List<Int?>): Pair<Int?, List<Int?>?> {
        val a = amounts.map { if (it != null && it > 0) it else null }
        return if (a.toSet().size == 1) a[0] to null else null to a
    }

    /** The weekday (0 = Monday) of the limit day containing [now]: the one it started on - with a 03:00 reset,
     *  Saturday 01:00 is still Friday's. (A reset at 12:00 or later: the date most of the day falls on.) */
    fun limitWeekday(now: LocalDateTime, clock: LimitClock): Int = clock.day(now).first.plusHours(12).dayOfWeek.value - 1

    /** The weekday the reset time alone gives [now] - differs from [limitWeekday] only while a changed reset
     *  stretches the running day over the next weekday's. */
    private fun naturalWeekday(now: LocalDateTime, clock: LimitClock): Int = clock.naturalDay(now).first.plusHours(12).dayOfWeek.value - 1

    private val DAY_ABBR = listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

    /** [180]*5 + [120]*2 -> "3h 00m Mon–Fri, 2h 00m Sat–Sun"; a day without a limit reads "no limit". */
    fun dayLimitsText(days: List<Int?>, fmt: (Int) -> String = ::minutesText): String {
        val runs = ArrayList<MutableList<Int>>()
        for (d in 0..6) if (runs.isNotEmpty() && days[d] == days[runs.last().last()]) runs.last().add(d) else runs.add(mutableListOf(d))
        return runs.joinToString(", ") { r ->
            val span = if (r.size == 1) DAY_ABBR[r[0]] else "${DAY_ABBR[r.first()]}–${DAY_ABBR[r.last()]}"
            "${days[r[0]]?.let(fmt) ?: "no limit"} $span"
        }
    }

    /** 70 -> "1h 10m", 45 -> "45m". */
    fun minutesText(min: Int): String = if (min >= 60) "${min / 60}h %02dm".format(min % 60) else "${min}m"

    /** {period: minutes} of a time limit. A daily limit set per weekday gives the amount of the limit day containing
     *  [now] (none that day: no "day"); while a changed reset stretches that day over the next weekday's, the
     *  stricter of the two, so moving the reset later can't skip a weekday's amount. Without [now]: the day one at its largest - which periods time counts in. */
    fun timeLimits(r: Rule, now: LocalDateTime? = null, clock: LimitClock = LimitClock.DEFAULT): Map<String, Int> {
        val days = dayLimits(r)
        val day = if (days == null) r.dailyLimitMin
                  else if (now == null) days.filterNotNull().maxOrNull()
                  else listOfNotNull(days[limitWeekday(now, clock)], days[naturalWeekday(now, clock)]).minOrNull()
        return buildMap {
            day?.let { put("day", it) }
            r.weeklyLimitMin?.let { put("week", it) }
            r.monthlyLimitMin?.let { put("month", it) }
        }
    }

    private fun switchLimits(r: Rule): Map<String, Int> = buildMap {
        r.dailySwitchLimit?.let { put("day", it) }
        r.weeklySwitchLimit?.let { put("week", it) }
        r.monthlySwitchLimit?.let { put("month", it) }
    }

    // ---------- effective rules (item + groups, plus a member's extra limits) ----------

    /**
     * The item's own rules + every rule of every group it's in + the extra rules a group gives this member
     * ([Group.overrides], keyed by rule type). A member's extras never REPLACE the group's rules - they come ON
     * TOP of them (PC 0.84.3): every member always gets every group rule, counted in the group's shared pot, and
     * its extras are more rules besides, with their own pot. Blocked if any rule blocks, so the first limit to run
     * out wins, blocked hours add up and allowed hours narrow. An extra can only make the member stricter.
     */
    fun effectiveRules(item: Item, groups: List<Group>): List<EffRule> {
        if (item.disabled) return emptyList()
        val me = "item:${item.id}"
        val out = ArrayList<EffRule>()
        for (r in item.rules) out.add(EffRule(r, me, me, me, "i${item.id}${r.type.name}"))
        for (g in groups) {
            if (item.id !in g.memberIds || g.disabled) continue
            val pot = "group:${g.id}"
            for (r in g.rules) {
                val t = r.type
                // a group time / opening limit is one shared total; so is the allowance in its blocked hours,
                // unless the group says each member has its own
                val owner = if (t == RuleType.TIME_LIMIT || t == RuleType.SWITCH_LIMIT) pot else me
                val allowance = if (t == RuleType.SCHEDULED && r.allowanceShared) pot else me
                out.add(EffRule(r, owner, me, allowance, "g${g.id}${t.name}", g.id, g.name))
            }
            for ((typeName, r) in (g.overrides[item.id] ?: emptyMap()).toSortedMap()) {
                val t = runCatching { RuleType.valueOf(typeName) }.getOrNull() ?: continue
                // the member's own time, openings and allowance; same key as the PC's (its owner is the member,
                // so it never mixes with the group's pot)
                out.add(EffRule(r.copy(type = t), me, me, me, "g${g.id}${t.name}", extraOf = g.name, extraOfId = g.id))
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
                val ends = timeLimits(r, now, clock).mapNotNull { (p, limit) ->
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

    /** (reason, until, rule) for the most important rule blocking the item now, else null. Of the rules blocking
     *  for that reason, the one that blocks longest (null = indefinitely), since a member's extras and the group's
     *  rules can block at once. An emergency unlock lifts every block except a permanent one (PC 0.84.7). */
    fun itemBlock(effRules: List<EffRule>, now: LocalDateTime, usage: Usage = noUsage,
                  clock: LimitClock = LimitClock.DEFAULT, unlockedUntil: LocalDateTime? = null): Block? {
        val unlocked = unlockedUntil != null && now.isBefore(unlockedUntil)
        val active = effRules.filter { !unlocked || it.rule.type == RuleType.PERMANENT }
            .mapNotNull { eff -> ruleBlock(eff, now, usage, clock)?.let { Block(it.first, it.second, eff) } }
        val top = active.minOfOrNull { it.reason.ordinal } ?: return null
        return active.filter { it.reason.ordinal == top }.maxBy { it.until ?: LocalDateTime.MAX }
    }

    /** Every rule blocking the item now (all reasons), for how it is blocked (BlockMethod.blockingWay). */
    fun blockingRules(effRules: List<EffRule>, now: LocalDateTime, usage: Usage = noUsage,
                      clock: LimitClock = LimitClock.DEFAULT, unlockedUntil: LocalDateTime? = null): List<EffRule> {
        val unlocked = unlockedUntil != null && now.isBefore(unlockedUntil)
        return effRules.filter { (!unlocked || it.rule.type == RuleType.PERMANENT) && ruleBlock(it, now, usage, clock) != null }
    }

    /** When the next blocked stretch of a schedule starts (it isn't blocking now). */
    private fun scheduleNextStart(s: Schedule, now: LocalDateTime): LocalDateTime? =
        if (s.mode == SchedMode.BLOCK) nextWindowStart(s.windows, now)
        else s.windows.mapNotNull { windowUntil(it, now) }.minOrNull()

    /** (used seconds, allowed seconds, end of this blocked stretch) for "N minutes allowed during blocked hours"
     *  while inside such a stretch, else null. */
    fun allowanceLeft(eff: EffRule, now: LocalDateTime, usage: Usage = noUsage): Triple<Int, Int, LocalDateTime>? {
        val r = eff.rule
        if (r.type != RuleType.SCHEDULED || (r.allowanceMin ?: 0) <= 0) return null
        val until = r.schedule?.let { scheduleUntil(it, now) } ?: return null
        if (until == LocalDateTime.MAX) return null
        return Triple(usage(eff.allowanceOwner, allowanceBucket(eff, until)), r.allowanceMin!! * 60, until)
    }

    /**
     * When the next block starts for an item that isn't blocked now (PC next_block), or null. Time limits and the
     * allowance are only predicted while it's [inUse]. During an emergency unlock: its end, if the item is blocked
     * then ([NextBlock.eff] null).
     */
    fun nextBlock(effRules: List<EffRule>, now: LocalDateTime, usage: Usage = noUsage, clock: LimitClock = LimitClock.DEFAULT,
                  inUse: Boolean = false, unlockedUntil: LocalDateTime? = null): NextBlock? {
        if (unlockedUntil != null && now.isBefore(unlockedUntil))
            return if (itemBlock(effRules, unlockedUntil, usage, clock) != null) NextBlock(unlockedUntil, null) else null
        val found = ArrayList<NextBlock>()
        for (eff in effRules) {
            val r = eff.rule
            when (r.type) {
                RuleType.SCHEDULED -> {
                    val sched = r.schedule ?: continue
                    val until = scheduleUntil(sched, now)
                    if (until == null) scheduleNextStart(sched, now)?.let { found.add(NextBlock(it, eff)) }
                    else if (inUse) allowanceLeft(eff, now, usage)?.let { (used, allowed, _) ->
                        found.add(NextBlock(now.plusSeconds(maxOf(0, allowed - used).toLong()), eff))
                    }
                }
                RuleType.TIME_LIMIT -> if (inUse) {
                    val lims = timeLimits(r, now, clock)
                    if (lims.isNotEmpty()) {
                        val left = lims.minOf { (p, limit) -> limit * 60 - usage(eff.usageOwner, timeBucket(p, now, clock)) }
                        found.add(NextBlock(now.plusSeconds(maxOf(0, left).toLong()), eff))
                    }
                }
                else -> {}
            }
        }
        return found.minByOrNull { it.at }
    }

    /** Does any of these rules block for good? (The emergency unlock never offers or frees such an item.) */
    fun permanent(effRules: List<EffRule>) = effRules.any { it.rule.type == RuleType.PERMANENT }

    /** "blocked" / "soon" (nearly out of time, or hours start soon) / "allowed" - for a rule's status colour. */
    fun ruleState(eff: EffRule, now: LocalDateTime, usage: Usage = noUsage, clock: LimitClock = LimitClock.DEFAULT): String {
        if (ruleBlock(eff, now, usage, clock) != null) return "blocked"
        val r = eff.rule
        when (r.type) {
            RuleType.TIME_LIMIT, RuleType.SWITCH_LIMIT -> {
                val timeBased = r.type == RuleType.TIME_LIMIT
                val fractions = (if (timeBased) timeLimits(r, now, clock) else switchLimits(r)).map { (p, limit) ->
                    if (timeBased) usage(eff.usageOwner, timeBucket(p, now, clock)).toDouble() / maxOf(limit * 60, 1)
                    else usage(eff.usageOwner, openingBucket(eff, p, now, clock)).toDouble() / maxOf(limit, 1)
                }
                return if (fractions.isNotEmpty() && fractions.max() >= NEARLY) "soon" else "allowed"
            }
            RuleType.SCHEDULED -> {
                val sched = r.schedule ?: return "allowed"
                if (scheduleUntil(sched, now) != null) return "soon"     // inside blocked hours, on its allowance
                val start = nextWindowStart(sched.windows, now)
                if (start != null && !start.isAfter(now.plusMinutes(SOON_MIN))) return "soon"
            }
            else -> {}
        }
        return "allowed"
    }
}
