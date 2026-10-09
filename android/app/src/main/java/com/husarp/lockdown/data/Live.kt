package com.husarp.lockdown.data

import com.husarp.lockdown.engine.Alerts
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.ModeState
import com.husarp.lockdown.engine.Modes
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.ui.Status
import java.time.LocalDateTime

/** Live status of items, computed from the tested engine + the recorded usage counters. For the UI. */
object Live {
    fun status(cfg: Config, item: Item, now: LocalDateTime = LocalDateTime.now(), mode: ModeState? = null): Status {
        if (item.disabled) return Status.PAUSED
        val eff = Rules.effectiveRules(item, cfg.groups)
        val byMode = modeBlocks(cfg, item, mode)
        if (eff.isEmpty() && !byMode) return Status.ALLOWED
        if (com.husarp.lockdown.block.Enforce.paused(cfg, now)) return Status.PAUSED    // "Pause my blocks"
        if (byMode) return Status.BLOCKED        // a running mode blocks it: no "min left" then
        val usage = UsageStore.usage
        val clock = cfg.clock()
        if (Rules.itemBlock(eff, now, usage, clock) != null) return Status.BLOCKED
        val soon = eff.any { Rules.ruleState(it, now, usage, clock) == "soon" }
        return if (soon) Status.SOON else Status.ALLOWED
    }

    /** The one line for a blocked item: "Blocked until 18:00" - the block that lasts longest of its own rules, its
     *  groups' and the running mode's, never a line per rule or member. */
    fun blockedLine(cfg: Config, item: Item, now: LocalDateTime, mode: ModeState? = null): String {
        val ends = ArrayList<LocalDateTime?>()
        Rules.blockEnd(Rules.effectiveRules(item, cfg.groups), now, UsageStore.usage, cfg.clock())
            ?.let { ends.add(it.takeIf { e -> e != LocalDateTime.MAX }) }
        if (modeBlocks(cfg, item, mode)) ends.add(mode!!.until)
        val until = if (ends.isEmpty() || ends.any { it == null }) null else ends.filterNotNull().max()
        return Alerts.blockedLine(until, now)
    }

    /** The running mode blocks [item] (as Enforce does: by its app, or by any of its hostnames). */
    private fun modeBlocks(cfg: Config, item: Item, mode: ModeState?): Boolean {
        if (!cfg.enabled || !Modes.blocking(mode)) return false
        val targets = Modes.targets(mode!!.mode, listOf(item), cfg.groups, cfg.categories)
        return if (item.type == ItemType.APP) item.target.lowercase() in Modes.blockedApps(targets)
        else Modes.blockedHosts(targets).let { hosts -> item.target.lowercase().split(" ").any { it.isNotEmpty() && it in hosts } }
    }

    /** The daily limit with the least time left (fraction used 0..1, minutes left), or null if none. */
    fun dayLimitFraction(cfg: Config, item: Item, now: LocalDateTime = LocalDateTime.now()): Pair<Float, Int>? {
        val usage = UsageStore.usage
        val clock = cfg.clock()
        var best: Pair<Float, Int>? = null
        for (eff in Rules.effectiveRules(item, cfg.groups)) {
            if (eff.rule.type != com.husarp.lockdown.engine.RuleType.TIME_LIMIT) continue
            val limit = Rules.timeLimits(eff.rule, now, clock)["day"] ?: continue     // today's amount
            if (limit <= 0) continue
            val used = usage(eff.usageOwner, Rules.timeBucket("day", now, clock))
            val frac = (used.toFloat() / (limit * 60)).coerceIn(0f, 1f)
            val leftMin = (limit - used / 60).coerceAtLeast(0)
            if (best == null || leftMin < best!!.second) best = frac to leftMin
        }
        return best
    }
}
