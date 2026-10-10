package com.husarp.lockdown.data

/**
 * Home's cards: which show and in what order (Settings.home). The Status card is always first and isn't in the
 * list. Looks only, so changes save at once with no challenge. Pure - the cards themselves are in HomeScreen.kt.
 */
object HomeLayout {
    /** Every card (id -> on by default), in the default order. A card added in a later version is appended to a
     *  saved layout by [resolve], with its default, so no migration is needed. */
    val CATALOGUE: List<Pair<String, Boolean>> = listOf(
        "problems" to true, "mode" to true, "blocked" to true, "low" to true, "apps" to true,
        "bedtime" to true, "quick" to true, "pause" to false, "emergency" to false, "island" to false,
    )

    private val KNOWN = CATALOGUE.mapTo(HashSet()) { it.first }

    /** The layout to show: the saved cards known to this version in their saved order (duplicates and unknown ids
     *  dropped), then every catalogue card missing from it with its default. null: the default layout. */
    fun resolve(saved: List<HomeCard>?): List<HomeCard> {
        val seen = HashSet<String>()
        val kept = saved.orEmpty().filter { it.id in KNOWN && seen.add(it.id) }
        return kept + CATALOGUE.filter { it.first !in seen }.map { HomeCard(it.first, it.second) }
    }

    /** [id] moved [by] places (-1 up, +1 down); past either end nothing changes. */
    fun move(list: List<HomeCard>, id: String, by: Int): List<HomeCard> {
        val i = list.indexOfFirst { it.id == id }
        val j = i + by
        if (i < 0 || j !in list.indices) return list
        return list.toMutableList().also { val c = it.removeAt(i); it.add(j, c) }
    }

    /** [id] shown <-> hidden. */
    fun toggle(list: List<HomeCard>, id: String): List<HomeCard> = list.map { if (it.id == id) it.copy(on = !it.on) else it }

    /** The default layout. */
    fun reset(): List<HomeCard> = resolve(null)
}
