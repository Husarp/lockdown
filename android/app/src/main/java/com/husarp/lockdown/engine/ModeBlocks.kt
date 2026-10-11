package com.husarp.lockdown.engine

/**
 * What a running mode blocks, as lookups: the app packages and the site hosts (lower case). Working it out walks every
 * item and group ([Modes.targets]) - with an imported list that is thousands of them - and the block service asked for
 * it on every step, window and DNS query while a mode ran (bedtime's runs all night). [ModeBlocksCache] keeps the last.
 */
class ModeBlocks(val apps: Set<String>, val hosts: Set<String>) {
    fun blocksApp(pkg: String): Boolean = pkg.lowercase() in apps

    /** [host] is one of the blocked hosts or under one (a dot-aligned suffix of it is in the set). */
    fun blocksHost(host: String): Boolean {
        var h = host.lowercase()
        while (true) {
            if (h in hosts) return true
            val dot = h.indexOf('.')
            if (dot < 0) return false
            h = h.substring(dot + 1)
        }
    }
}

/** The last [ModeBlocks], kept while the mode, items, groups and categories are the same objects (they are never
 *  changed in place: an edit makes new ones, so a different object means worked out again). Thread-safe. */
class ModeBlocksCache {
    private class Entry(val mode: Mode, val items: List<Item>, val groups: List<Group>, val categories: Map<String, String>, val blocks: ModeBlocks)

    @Volatile private var last: Entry? = null

    fun get(mode: Mode, items: List<Item>, groups: List<Group>, categories: Map<String, String>): ModeBlocks {
        last?.let { if (it.mode === mode && it.items === items && it.groups === groups && it.categories === categories) return it.blocks }
        val targets = Modes.targets(mode, items, groups, categories)
        val blocks = ModeBlocks(Modes.blockedApps(targets), Modes.blockedHosts(targets))
        last = Entry(mode, items, groups, categories, blocks)
        return blocks
    }
}

/** Does any app get greyed by the rules ([BlockMethod.greyUsed])? Asked on every step, so the answer is kept while the
 *  items and groups are the same objects. When nothing greys, the windows on screen needn't be looked at. */
class GreyUsedCache {
    private class Entry(val items: List<Item>, val groups: List<Group>, val used: Boolean)

    @Volatile private var last: Entry? = null

    fun used(items: List<Item>, groups: List<Group>): Boolean {
        last?.let { if (it.items === items && it.groups === groups) return it.used }
        val used = BlockMethod.greyUsed(items, groups)
        last = Entry(items, groups, used)
        return used
    }
}
