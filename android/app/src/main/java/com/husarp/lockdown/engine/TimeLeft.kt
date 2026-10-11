package com.husarp.lockdown.engine

import java.time.Duration
import java.time.LocalDateTime

/**
 * What one item's status line says, worked out in ONE place so Home's "Running low", Home's "Time per app" and the
 * Blocking lists (items and group members) all agree:
 *  - [Kind.BLOCKED]: a rule, or the running mode, blocks it now. "Blocked until 18:00" - when it is really free
 *    again (the latest of all blockers), no "min left".
 *  - [Kind.ALLOWED]: inside blocked hours, but the allowance isn't used up: "Blocked · 5 min allowed". The
 *    time is the least of the allowance and any time limit; always "running low".
 *  - [Kind.LEFT]: free, with limits: the time (or opens) until the EARLIEST blocker bites: a daily / weekly /
 *    monthly limit (group's too), an allowance, blocked hours starting within [Rules.SOON_MIN] minutes (plus their
 *    allowance), or an opening limit when it is the most used. "Running low" = that one is nearly out.
 *  - [Kind.FREE]: no limit applies now.
 */
object TimeLeft {
    enum class Kind { FREE, LEFT, ALLOWED, BLOCKED }

    /** [seconds] left (LEFT / ALLOWED), or [opens] left when an opening limit ends first; [fraction] of that same
     *  budget used (0..1); [until] when a BLOCKED item is free again (null = no end). */
    data class Left(val kind: Kind, val seconds: Int = 0, val opens: Int? = null, val fraction: Float = 0f,
                    val low: Boolean = false, val until: LocalDateTime? = null) {
        /** Whole minutes left, rounded up (4m30s -> 5). */
        val minutes: Int get() = (seconds + 59) / 60
    }

    private class Budget(val seconds: Int, val fraction: Double, val soon: Boolean = false)

    private fun used(used: Int, total: Int) = if (total <= 0) 1.0 else (used.toDouble() / total).coerceIn(0.0, 1.0)

    /** When a blocked item is free again: the latest end of its rules' blocks and the running mode's (null = no end). */
    fun blockedUntil(eff: List<EffRule>, now: LocalDateTime, usage: Usage = noUsage, clock: LimitClock = LimitClock.DEFAULT,
                     modeBlocks: Boolean = false, modeUntil: LocalDateTime? = null): LocalDateTime? {
        val ends = ArrayList<LocalDateTime?>()
        Rules.blockEnd(eff, now, usage, clock)?.let { ends.add(it.takeIf { e -> e != LocalDateTime.MAX }) }
        if (modeBlocks) ends.add(modeUntil)
        return if (ends.isEmpty() || ends.any { it == null }) null else ends.filterNotNull().max()
    }

    fun of(eff: List<EffRule>, now: LocalDateTime, usage: Usage = noUsage, clock: LimitClock = LimitClock.DEFAULT,
           modeBlocks: Boolean = false, modeUntil: LocalDateTime? = null): Left {
        if (modeBlocks || eff.any { Rules.ruleBlock(it, now, usage, clock) != null })
            return Left(Kind.BLOCKED, fraction = 1f, until = blockedUntil(eff, now, usage, clock, modeBlocks, modeUntil))

        val times = ArrayList<Budget>()
        val opens = ArrayList<Pair<Int, Double>>()     // opens left, fraction used
        var inAllowance = false
        for (e in eff) {
            val r = e.rule
            when (r.type) {
                RuleType.TIME_LIMIT -> for ((p, limit) in Rules.timeLimits(r, now, clock)) {
                    val u = usage(e.usageOwner, Rules.timeBucket(p, now, clock))
                    times.add(Budget(maxOf(0, limit * 60 - u), used(u, limit * 60)))
                }
                RuleType.SWITCH_LIMIT -> for ((p, limit) in Rules.switchLimits(r)) {
                    val u = usage(e.usageOwner, Rules.openingBucket(e, p, now, clock))
                    opens.add(maxOf(0, limit - u) to used(u, limit))
                }
                RuleType.SCHEDULED -> {
                    val sched = r.schedule ?: continue
                    val a = Rules.allowanceLeft(e, now, usage)
                    if (a != null) {                       // inside blocked hours, on the allowance
                        inAllowance = true
                        times.add(Budget(maxOf(0, a.second - a.first), used(a.first, a.second)))
                    } else if (Rules.scheduleUntil(sched, now) == null) {      // free: do the hours start soon?
                        val start = Rules.scheduleNextStart(sched, now) ?: continue
                        val secs = Duration.between(now, start).seconds
                        if (secs in 0..Rules.SOON_MIN * 60)
                            times.add(Budget((secs + (r.allowanceMin ?: 0) * 60L).toInt(), 1.0 - secs.toDouble() / (Rules.SOON_MIN * 60), soon = true))
                    }
                }
                else -> {}
            }
        }
        val bestTime = times.minWithOrNull(compareBy<Budget> { it.seconds }.thenByDescending { it.fraction })
        if (inAllowance && bestTime != null) return Left(Kind.ALLOWED, bestTime.seconds, fraction = bestTime.fraction.toFloat(), low = true)
        val bestOpens = opens.minWithOrNull(compareByDescending<Pair<Int, Double>> { it.second }.thenBy { it.first })
        if (bestOpens != null && (bestTime == null || bestOpens.second > bestTime.fraction))
            return Left(Kind.LEFT, opens = bestOpens.first, fraction = bestOpens.second.toFloat(), low = bestOpens.second >= Rules.NEARLY)
        if (bestTime != null)
            return Left(Kind.LEFT, bestTime.seconds, fraction = bestTime.fraction.toFloat(), low = bestTime.soon || bestTime.fraction >= Rules.NEARLY)
        return Left(Kind.FREE)
    }

    /** "5 min", "1h 05m", "3 opens". */
    fun amount(l: Left): String = l.opens?.let { "$it open${if (it == 1) "" else "s"}" }
        ?: l.minutes.let { if (it >= 60) Rules.minutesText(it) else "$it min" }

    /** The full line: "5 min left" / "Blocked · 5 min allowed" / "Blocked until 18:00" / "Blocked"; "" when free. */
    fun line(l: Left, now: LocalDateTime): String = when (l.kind) {
        Kind.FREE -> ""
        Kind.LEFT -> "${amount(l)} left"
        Kind.ALLOWED -> "Blocked · ${amount(l)} allowed"
        Kind.BLOCKED -> Alerts.blockedLine(l.until, now)
    }

    /** The short chip for a list row (its colour says the rest): "5 min left" / "5 min allowed" / "Blocked" / "Allowed".
     *  A free item with plenty left stays "Allowed" - only running low shows the time. */
    fun chip(l: Left): String = when {
        l.kind == Kind.ALLOWED -> "${amount(l)} allowed"
        l.kind == Kind.LEFT && l.low -> "${amount(l)} left"
        l.kind == Kind.BLOCKED -> "Blocked"
        else -> "Allowed"
    }
}
