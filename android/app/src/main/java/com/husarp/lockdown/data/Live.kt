package com.husarp.lockdown.data

import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.ui.Status
import java.time.LocalDateTime

/** Live status of items, computed from the tested engine + the recorded usage counters. For the UI. */
object Live {
    fun status(cfg: Config, item: Item, now: LocalDateTime = LocalDateTime.now()): Status {
        if (item.disabled) return Status.PAUSED
        val eff = Rules.effectiveRules(item, cfg.groups)
        if (eff.isEmpty()) return Status.ALLOWED
        val usage = UsageStore.counter.usage
        val clock = cfg.clock()
        if (Rules.itemBlock(eff, now, usage, clock) != null) return Status.BLOCKED
        val soon = eff.any { Rules.ruleState(it, now, usage, clock) == "soon" }
        return if (soon) Status.SOON else Status.ALLOWED
    }

    /** Fraction of the tightest daily-limit rule used (0..1), or null if the item has no time limit. */
    fun dayLimitFraction(cfg: Config, item: Item, now: LocalDateTime = LocalDateTime.now()): Pair<Float, Int>? {
        val usage = UsageStore.counter.usage
        val clock = cfg.clock()
        var best: Pair<Float, Int>? = null
        for (eff in Rules.effectiveRules(item, cfg.groups)) {
            val limit = eff.rule.dailyLimitMin ?: continue
            if (limit <= 0) continue
            val used = usage(eff.usageOwner, Rules.timeBucket("day", now, clock))
            val frac = (used.toFloat() / (limit * 60)).coerceIn(0f, 1f)
            val leftMin = (limit - used / 60).coerceAtLeast(0)
            if (best == null || frac > best!!.first) best = frac to leftMin
        }
        return best
    }
}
