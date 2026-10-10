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
 * Apps on Android: "close" (the notice, then the home screen - what an app's empty way means) or "grey" (it stays
 * open, but the screen goes grey while it's in front). Closing covers greying. A group's "How member apps are
 * blocked" ([Group.appBlock], PC app_block) works as the sites' does, but a member app's own way in it REPLACES the
 * group's (PC schema 8): e.g. one app only greyed while the group closes. Its way here is its own if it has one, else
 * the group's; a lighter one than it had is the Anti-Bypass challenge ([waysLooser] compares the effective ways).
 */
object BlockMethod {
    private val ORDER = listOf("dns", "back", "close")
    private val APP_ORDER = listOf("close", GREY)
    const val GREY = "grey"

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

    /** An app's flags; none set means "close". (The PC's other flags - minimize, background, internet - close it here.) */
    fun appFlags(blockType: String?): Set<String> =
        (blockType ?: "").split(",").map { it.trim() }.filterTo(HashSet()) { it.isNotEmpty() }.ifEmpty { setOf("close") }

    /** Everything an app way does, for comparing two ways: closing covers minimizing and greying. */
    fun appStrength(blockType: String?): Set<String> = appFlags(blockType).let { if ("close" in it) it + "minimize" + GREY else it }

    /** One app way that does everything these ask for (closing it covers greying it). */
    fun mergeApp(vararg blockTypes: String?): String {
        val f = blockTypes.flatMapTo(HashSet()) { appFlags(it) }
        if ("close" in f) f.remove(GREY)
        return (APP_ORDER.filter { it in f } + (f - APP_ORDER.toSet()).sorted()).joinToString(",")
    }

    /** The app way only greys the screen: it stays open, nothing in the way closes it. */
    fun greyOnly(blockType: String?): Boolean = appFlags(blockType) == setOf(GREY)

    /** Some app or group greys apps (so the grayscale permission matters). */
    fun greyUsed(items: List<Item>, groups: List<Group>): Boolean =
        items.any { it.type == ItemType.APP && GREY in appFlags(it.blockType) } ||
            groups.any { g -> GREY in appFlags(g.appBlock ?: "close") || g.memberBlocks.any { (id, w) -> GREY in appFlags(w) && items.any { it.id == id && it.type == ItemType.APP } } }

    private fun mergeFor(type: ItemType, ways: Array<out String?>) = if (type == ItemType.SITE) merge(*ways) else mergeApp(*ways)

    /** How [group] blocks this member: the group's way for its kind (else the member's own setting). With [own], a
     *  member app's own way in this group instead of it, and a site's own on top. */
    fun inGroup(item: Item, group: Group, own: Boolean = true): String {
        val base = (if (item.type == ItemType.SITE) group.siteBlock else group.appBlock) ?: item.blockType
        val extra = if (own) group.memberBlocks[item.id] else null
        if (extra != null && item.type == ItemType.APP) return extra
        return if (extra != null) mergeFor(item.type, arrayOf(base, extra)) else base
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
            else -> mergeFor(item.type, ways.toTypedArray())
        }
    }

    /** How the group blocks a member got weaker for any member it keeps: its way, or a member's own in it (e.g.
     *  "leave the browser" dropped, or apps greyed instead of closed). Compared per member, as each one ends up, so
     *  a group's first choice is compared with how each member was blocked before it had one. Adding to it is free. */
    fun waysLooser(old: Group, new: Group, oldItems: Map<String, Item>, newItems: Map<String, Item>): Boolean {
        for (member in old.memberIds.toSet() intersect new.memberIds.toSet()) {
            val before = oldItems[member] ?: continue
            val after = newItems[member] ?: before
            val strengthOf: (String?) -> Set<String> = if (before.type == ItemType.SITE) ::strength else ::appStrength
            if (!strengthOf(inGroup(after, new)).containsAll(strengthOf(inGroup(before, old)))) return true
        }
        return false
    }
}
