package com.husarp.lockdown.engine

import kotlinx.serialization.Serializable

/**
 * The full blocking model, ported from the PC app (rules.py / db.py) so the phone enforces rules exactly the
 * same way. This is UI-independent - the redesign will wire a new UI onto it; nothing here draws anything.
 */

enum class RuleType { PERMANENT, SCHEDULED, TIME_LIMIT, SWITCH_LIMIT, TEMPORARY }
enum class SchedMode { ALLOW, BLOCK }
enum class ItemType { APP, SITE }
enum class SwitchCount { VISIT, SWITCH }   // opening limit: count new visits/launches, or every switch to it

/** One time window in a schedule. days: 0=Monday..6=Sunday. "HH:MM"; end<=start means it runs past midnight. */
@Serializable
data class Window(val days: List<Int>, val start: String, val end: String)

@Serializable
data class Schedule(val mode: SchedMode = SchedMode.BLOCK, val windows: List<Window> = emptyList())

/** A rule on an item or a group. Only the fields for its [type] matter. */
@Serializable
data class Rule(
    val type: RuleType,
    // time_limit: minutes per period (any combination)
    val dailyLimitMin: Int? = null,
    val weeklyLimitMin: Int? = null,
    val monthlyLimitMin: Int? = null,
    // time_limit: a daily amount for each weekday Mon..Sun (null = no limit that day), set instead of dailyLimitMin
    // (PC 0.84.12). All days the same is kept as one dailyLimitMin, so old rules read as before.
    val dailyLimitDays: List<Int?>? = null,
    // switch_limit: opens per period (any combination)
    val dailySwitchLimit: Int? = null,
    val weeklySwitchLimit: Int? = null,
    val monthlySwitchLimit: Int? = null,
    // scheduled
    val schedule: Schedule? = null,
    // temporary: block until this local datetime "yyyy-MM-ddTHH:mm:ss"
    val tempUntil: String? = null,
    // scheduled: minutes of grace usable inside a blocked stretch
    val allowanceMin: Int? = null,
    val allowanceShared: Boolean = true,      // group allowance: one shared pot (true) or per-member (false)
    // switch_limit
    val switchMode: SwitchCount = SwitchCount.VISIT,
    val visitGapMin: Int? = null,             // visit mode: minutes away before a return counts as a new visit
)

@Serializable
data class Item(
    val id: String,
    val name: String,
    val target: String,                       // sites: space-separated hostnames; apps: package name
    val type: ItemType,
    val blockType: String = "",               // flags - app: "close,background,minimize,internet"; site: "dns,close,back"
    val disabled: Boolean = false,            // paused: kept, but not enforced
    val rules: List<Rule> = emptyList(),
    val notify: String? = null,               // blocked-visit alert override: null/"on"/"off"
)

@Serializable
data class Group(
    val id: String,
    val name: String,
    val disabled: Boolean = false,
    val rules: List<Rule> = emptyList(),
    val memberIds: List<String> = emptyList(),
    // a member's extra limits, ON TOP of the group's rules (never instead): itemId -> (RuleType.name -> Rule)
    val overrides: Map<String, Map<String, Rule>> = emptyMap(),
    // how the group blocks its member sites (PC 0.84.11): "dns,close,back" flags like an item's; null = not chosen,
    // each member is blocked its own way (every group from before)
    val siteBlock: String? = null,
    // what a member site adds on top of that, in this group: itemId -> flags (only ever more, never less)
    val memberBlocks: Map<String, String> = emptyMap(),
)

/** A rule flattened for evaluation: the rule plus who owns its usage counters (item vs shared group pot). */
data class EffRule(
    val rule: Rule,
    val usageOwner: String,      // whose time/opens count: "item:<id>" or "group:<id>"
    val itemOwner: String,       // always the item ("item:<id>")
    val allowanceOwner: String,  // whose allowance pot is spent
    val ruleKey: String,         // stable id for buckets
    val groupId: String? = null,
    val groupName: String? = null,
    val extraOf: String? = null, // a member's extra rule: the name of the group it was set in
    val extraOfId: String? = null,   // ... and that group's id
)

/** Why an item is blocked. Order = priority (most important first), matching the PC's REASON_ORDER. */
enum class Reason { PERMANENT, TEMPORARY, LIMIT, SWITCHES, SCHEDULE }

/** Result of evaluating an item: why it's blocked and until when (null = indefinitely). */
data class Block(val reason: Reason, val until: java.time.LocalDateTime?, val rule: EffRule)
