package com.husarp.lockdown.engine

/**
 * How a blocked site is dealt with when a GROUP blocks it (PC 0.84.11, block_method.py): the group's "How member
 * sites are blocked" - "dns" (can't load), "back" (go back), "close" (leave the browser), the same flags as an
 * item's own. A member may add to that in its extra limits, and only add: its own choice comes ON TOP of the
 * group's, like its extra rules, so it can make the member stricter, never weaker.
 *
 * - A block by the item's own rules: the item's own way, as always.
 * - A block by a group (its rules, or a member's extra rule in it): the group's way + the member's own. A group
 *   that hasn't chosen blocks each member the way the member is set itself - nothing changes for older groups.
 * - Blocked by several at once: everything any of them asks for (leaving the browser covers going back).
 *
 * Apps have one way on Android (the notice, then the home screen), so this is for sites only.
 */
object BlockMethod {
    private val ORDER = listOf("dns", "back", "close")

    /** A site's flags; none set means "dns". */
    fun flags(blockType: String?): Set<String> =
        (blockType ?: "").split(",").map { it.trim() }.filterTo(HashSet()) { it.isNotEmpty() }.ifEmpty { setOf("dns") }

    /** Everything a way does, for comparing two ways: leaving the browser covers going back. */
    fun strength(blockType: String?): Set<String> = flags(blockType).let { if ("close" in it) it + "back" else it }

    /** One way that does everything these ask for. */
    fun merge(vararg blockTypes: String?): String {
        val f = blockTypes.flatMapTo(HashSet()) { flags(it) }
        if ("close" in f) f.remove("back")
        return ORDER.filter { it in f }.joinToString(",")
    }

    /** How [group] blocks this member: the group's way (else the member's own setting) plus, with [own], what the
     *  member adds on top in this group. */
    fun inGroup(item: Item, group: Group, own: Boolean = true): String {
        if (item.type != ItemType.SITE) return item.blockType
        val base = group.siteBlock ?: item.blockType
        val extra = if (own) group.memberBlocks[item.id] else null
        return if (extra != null) merge(base, extra) else base
    }

    /** The way the item is blocked while this rule blocks it. */
    fun ruleWay(item: Item, groupsById: Map<String, Group>, eff: EffRule): String {
        val g = (eff.groupId ?: eff.extraOfId)?.let { groupsById[it] }
        return if (g != null) inGroup(item, g) else item.blockType
    }

    /** The way the item is blocked while these rules (the ones blocking it now) block it. */
    fun blockingWay(item: Item, groups: List<Group>, blocking: List<EffRule>): String {
        val byId = groups.associateBy { it.id }
        val ways = blocking.mapTo(LinkedHashSet()) { ruleWay(item, byId, it) }
        return when (ways.size) {
            0 -> item.blockType
            1 -> ways.first()
            else -> merge(*ways.toTypedArray())
        }
    }

    /** How the group blocks a member got weaker for any member it keeps: its way, or a member's own in it (e.g.
     *  "leave the browser" dropped). Compared per member, as each one ends up, so a group's first choice is
     *  compared with how each member was blocked before it had one. Adding to it is free. */
    fun waysLooser(old: Group, new: Group, oldItems: Map<String, Item>, newItems: Map<String, Item>): Boolean {
        for (member in old.memberIds.toSet() intersect new.memberIds.toSet()) {
            val before = oldItems[member] ?: continue
            if (before.type != ItemType.SITE) continue
            val after = newItems[member] ?: before
            if (!strength(inGroup(after, new)).containsAll(strength(inGroup(before, old)))) return true
        }
        return false
    }
}
