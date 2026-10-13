package com.husarp.lockdown.data

import com.husarp.lockdown.engine.DomainSet
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType

/**
 * Sites from an imported list (Settings > Import sites), kept compactly: one name each in [Config.imported], not one
 * blocklist item each (6,000 imported sites were 6,000 items - megabytes of config written on every change, and
 * scanned one by one on every step and DNS lookup). They are blocked for good, exactly as the items were: "Pause my
 * blocks" and the master switch free them as they freed the items; an emergency doesn't (it never frees a block for
 * good), nor does an "Allowed exception" of the protection lists (it never freed an item). Kept in the config, so the
 * Island copy, export and import carry them. Looked up through a [DomainSet] made once per list. Pure, so tested.
 */
object ImportedSites {
    /** Fewer import-shaped items than this are left as items: someone may have typed them one by one. */
    const val MIN_BATCH = 50

    private val FOR_GOOD = listOf(Rule(RuleType.PERMANENT))

    /** The shape Import sites gave every site: a lone domain, named after itself, blocked for good and nothing else -
     *  not paused, no block way or alert of its own, not in a group or an emergency use. */
    fun importShaped(item: Item, used: Set<String>): Boolean =
        item.type == ItemType.SITE && !item.disabled && item.blockType.isEmpty() && item.notify == null &&
            item.rules == FOR_GOOD && item.name == item.target && item.id !in used &&
            DomainSet.listLine(item.target) == item.target

    /** Item ids something else points at (groups, emergency uses): those items stay items. */
    private fun usedIds(cfg: Config): Set<String> = buildSet {
        for (g in cfg.groups) { addAll(g.memberIds); addAll(g.overrides.keys); addAll(g.memberBlocks.keys) }
        addAll(cfg.unlockItems)
        for (u in cfg.emergencyUses) addAll(u.items)
    }

    /** [cfg] with the items an import made (when there are at least [MIN_BATCH]) moved into [Config.imported]. Every
     *  moved site stays blocked for good; the rest is untouched. */
    fun migrate(cfg: Config): Config {
        val used = usedIds(cfg)
        val moving = cfg.items.filter { importShaped(it, used) }
        if (moving.size < MIN_BATCH) return cfg
        val ids = moving.mapTo(HashSet()) { it.id }
        return cfg.copy(items = cfg.items.filter { it.id !in ids }, imported = merge(cfg.imported, moving.map { it.target }))
    }

    /** [have] plus [new] (cleaned), sorted, without repeats. */
    fun merge(have: List<String>, new: List<String>): List<String> =
        (have.asSequence() + new.asSequence().mapNotNull { DomainSet.listLine(it) }).map(DomainSet::norm)
            .filter { it.isNotEmpty() }.toSortedSet().toList()

    /** Import sites: [cfg] with [sites] added (those already on the list or an item's own hosts left out), and how
     *  many were new. */
    fun add(cfg: Config, sites: List<String>): Pair<Config, Int> {
        val items = cfg.items.filter { it.type == ItemType.SITE }.flatMapTo(HashSet()) { it.target.lowercase().split(" ") }
        val have = cfg.imported.toHashSet()
        val fresh = sites.mapNotNull { DomainSet.listLine(it)?.let(DomainSet::norm) }.filter { it.isNotEmpty() && it !in have && it !in items }.distinct()
        return (if (fresh.isEmpty()) cfg else cfg.copy(imported = merge(cfg.imported, fresh))) to fresh.size
    }

    // The set for the list in use, made again only when the list changes (it is a new list object then).
    @Volatile private var cached: Pair<List<String>, DomainSet>? = null

    fun setOf(list: List<String>): DomainSet {
        cached?.let { if (it.first === list) return it.second }
        return DomainSet.of(list).also { cached = list to it }
    }

    /** Is [host] an imported site, or under one? */
    fun blocks(cfg: Config, host: String): Boolean = cfg.imported.isNotEmpty() && setOf(cfg.imported).matches(host)
}
