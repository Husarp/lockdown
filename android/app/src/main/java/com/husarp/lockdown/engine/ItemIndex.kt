package com.husarp.lockdown.engine

/**
 * The blocklist by app and by site name, so the block service's step and the site filter's DNS check find the items
 * for an app or a host straight away instead of going through every item (several times a step, on every DNS
 * query). Made once per item list (a changed list is a new list) and kept. Pure, so tested.
 */
class ItemIndex private constructor(items: List<Item>) {
    private val apps = HashMap<String, Item>()
    private val sites = HashMap<String, MutableList<Int>>()     // a site name (no "www.") -> the places of its items
    private val all = items

    init {
        items.forEachIndexed { i, it ->
            if (it.type == ItemType.APP) apps.putIfAbsent(it.target.lowercase(), it)
            else for (t in it.target.lowercase().split(" ")) {
                val d = t.removePrefix("www.")
                if (d.isNotBlank()) sites.getOrPut(d) { ArrayList(1) }.let { l -> if (l.lastOrNull() != i) l.add(i) }
            }
        }
    }

    /** The first app item for [pkg] (any case), as `items.firstOrNull { app && target == pkg }`. */
    fun app(pkg: String): Item? = apps[pkg.lowercase()]

    /** The site items [host] is, or is under, in list order - as `items.filter { site && hostMatches(target, host) }`. */
    fun sites(host: String): List<Item> {
        var d = host.lowercase().removePrefix("www.")
        if (d.isEmpty() || sites.isEmpty()) return emptyList()
        var found: java.util.TreeSet<Int>? = null
        while (true) {
            sites[d]?.let { (found ?: java.util.TreeSet<Int>().also { s -> found = s }).addAll(it) }
            val dot = d.indexOf('.'); if (dot < 0) break
            d = d.substring(dot + 1)
        }
        return found?.map { all[it] } ?: emptyList()
    }

    companion object {
        @Volatile private var cached: ItemIndex? = null

        /** The index of [items], made again only when the list changes. */
        fun of(items: List<Item>): ItemIndex {
            cached?.let { if (it.all === items) return it }
            return ItemIndex(items).also { cached = it }
        }
    }
}
