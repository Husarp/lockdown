package com.husarp.lockdown.data

import com.husarp.lockdown.engine.Alerts
import com.husarp.lockdown.engine.BlockMethod
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.ModeState
import com.husarp.lockdown.engine.Modes
import com.husarp.lockdown.engine.Reason
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.TimeLeft
import com.husarp.lockdown.engine.Usage
import com.husarp.lockdown.ui.Status
import java.time.LocalDateTime

/** A blocked item's one line ("Blocked until 18:00" / "Grey until 18:00"), whether it's only greyed, and why (the
 *  most important reason; null when only the running mode blocks it), and until when (null: no end). */
data class BlockedLine(val text: String, val grey: Boolean, val reason: Reason?, val until: LocalDateTime? = null)

/** One row of Home's "Blocked now": an item a rule blocks now, why, until when (null = no end) and if only greyed. */
data class BlockedRow(val item: Item, val reason: Reason, val until: LocalDateTime?, val grey: Boolean)

/** Home's "Blocked now": the always-blocked apps and sites counted (never evaluated one by one - there can be
 *  thousands of imported sites), the always-greyed apps apart, and the first [rows] blocked by any other rule,
 *  soonest end first, of [total]. */
data class BlockedNow(val alwaysApps: Int, val alwaysSites: Int, val rows: List<BlockedRow>, val total: Int, val alwaysGrey: Int = 0)

/** Live status of items, computed from the tested engine + the recorded usage counters. For the UI. */
object Live {
    fun status(cfg: Config, item: Item, now: LocalDateTime = LocalDateTime.now(), mode: ModeState? = null,
               usage: Usage = UsageStore.usage): Status {
        if (item.disabled) return Status.PAUSED
        val eff = Rules.effectiveRules(item, cfg.groups)
        val byMode = modeBlocks(cfg, item, mode)
        if (eff.isEmpty() && !byMode) return Status.ALLOWED
        if (com.husarp.lockdown.block.Enforce.paused(cfg, now)) return Status.PAUSED    // "Pause my blocks"
        val l = TimeLeft.of(eff, now, usage, cfg.clock(), byMode, mode?.until)
        return when { l.kind == TimeLeft.Kind.BLOCKED -> Status.BLOCKED; l.low -> Status.SOON; else -> Status.ALLOWED }
    }

    /** What an item's status line says (see [TimeLeft]): blocked until, blocked with minutes allowed, time left, or
     *  free. A paused or switched-off item has nothing to show. One rule for Home's cards and the Blocking lists. */
    fun timeLeft(cfg: Config, item: Item, now: LocalDateTime = LocalDateTime.now(), mode: ModeState? = null,
                 usage: Usage = UsageStore.usage): TimeLeft.Left {
        if (item.disabled || com.husarp.lockdown.block.Enforce.paused(cfg, now)) return TimeLeft.Left(TimeLeft.Kind.FREE)
        val byMode = modeBlocks(cfg, item, mode)
        return TimeLeft.of(Rules.effectiveRules(item, cfg.groups), now, usage, cfg.clock(), byMode, mode?.until)
    }

    /** A list row's chip for a free (not blocked) item: "Allowed", "5 min left" when running low, "5 min allowed",
     *  "Paused". */
    fun chipText(cfg: Config, item: Item, now: LocalDateTime, mode: ModeState? = null, usage: Usage = UsageStore.usage): String =
        if (item.disabled || com.husarp.lockdown.block.Enforce.paused(cfg, now)) "Paused"
        else TimeLeft.chip(timeLeft(cfg, item, now, mode, usage))

    /** The one line for a blocked item: "Blocked until 18:00" - the block that lasts longest of its own rules, its
     *  groups' and the running mode's, never a line per rule or member. "Grey until 18:00" for an app only greyed. */
    fun blockedLine(cfg: Config, item: Item, now: LocalDateTime, mode: ModeState? = null): String =
        blockedInfo(cfg, item, now, mode).text

    /** [blockedLine], with whether it's only greyed and why it's blocked (the status chip's look, the reason pill). */
    fun blockedInfo(cfg: Config, item: Item, now: LocalDateTime, mode: ModeState? = null, usage: Usage = UsageStore.usage): BlockedLine {
        val eff = Rules.effectiveRules(item, cfg.groups)
        val byMode = modeBlocks(cfg, item, mode)
        val until = TimeLeft.blockedUntil(eff, now, usage, cfg.clock(), byMode, mode?.until)
        val line = Alerts.blockedLine(until, now)
        val grey = item.type == ItemType.APP && !byMode &&
            BlockMethod.greyOnly(BlockMethod.blockingWay(item, cfg.groups, Rules.blockingRules(eff, now, usage, cfg.clock())))
        val reason = Rules.itemBlock(eff, now, usage, cfg.clock())?.reason
        return BlockedLine(if (grey) line.replaceFirst("Blocked", "Grey") else line, grey, reason, until)
    }

    /**
     * Home's "Blocked now", cheap with thousands of items: an item that is only always-blocked (its own rules all
     * permanent, in no group, not paused) is counted, not evaluated. The few others (in a group, or with another
     * rule) are evaluated; one a permanent rule blocks counts as always blocked too, the rest are the rows. An app
     * that is only greyed all the time counts as always grey, not blocked. A running
     * mode is left out here (Home sums it up in one line). Main thread only, like the blocking ([usage]).
     */
    fun blockedNow(cfg: Config, now: LocalDateTime, limit: Int = 5, usage: Usage = UsageStore.usage): BlockedNow {
        val grouped = HashSet<String>()
        for (g in cfg.groups) if (!g.disabled) grouped.addAll(g.memberIds)
        var apps = 0; var sites = 0; var greys = 0
        fun always(item: Item, grey: Boolean) { if (grey) greys++ else if (item.type == ItemType.APP) apps++ else sites++ }
        val rows = ArrayList<BlockedRow>()
        val clock = cfg.clock()
        for (item in cfg.items) {
            if (item.disabled) continue
            val inGroup = item.id in grouped
            if (!inGroup && item.rules.isEmpty()) continue
            if (!inGroup && item.rules.all { it.type == RuleType.PERMANENT }) {
                always(item, item.type == ItemType.APP && BlockMethod.greyOnly(item.blockType)); continue
            }
            val eff = Rules.effectiveRules(item, cfg.groups)
            val block = Rules.itemBlock(eff, now, usage, clock) ?: continue
            val grey = item.type == ItemType.APP &&
                BlockMethod.greyOnly(BlockMethod.blockingWay(item, cfg.groups, Rules.blockingRules(eff, now, usage, clock)))
            if (block.reason == Reason.PERMANENT) { always(item, grey); continue }
            val end = Rules.blockEnd(eff, now, usage, clock)?.takeIf { it != LocalDateTime.MAX }
            rows.add(BlockedRow(item, block.reason, end, grey))
        }
        rows.sortWith(compareBy<BlockedRow> { it.until ?: LocalDateTime.MAX }.thenBy { it.item.name.lowercase() })
        return BlockedNow(apps, sites, rows.take(limit), rows.size, greys)
    }

    /** The running mode blocks [item] (as Enforce does: by its app, or by any of its hostnames). */
    private fun modeBlocks(cfg: Config, item: Item, mode: ModeState?): Boolean {
        if (!cfg.enabled || !Modes.blocking(mode)) return false
        val targets = Modes.targets(mode!!.mode, listOf(item), cfg.groups, cfg.categories)
        return if (item.type == ItemType.APP) item.target.lowercase() in Modes.blockedApps(targets)
        else Modes.blockedHosts(targets).let { hosts -> item.target.lowercase().split(" ").any { it.isNotEmpty() && it in hosts } }
    }
}
