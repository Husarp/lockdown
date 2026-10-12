package com.husarp.lockdown.engine

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.text.Normalizer

/** One site of the bundled list (assets/sites/sites.json): its friendly [name], its host names (the main one first),
 *  its [category] and [icon] file; [rank] is its place in the list (1 = most popular). */
@Serializable
data class ListedSite(val name: String, val domains: List<String>, val category: String = "", val icon: String? = null, val rank: Int = 0) {
    val main: String get() = domains.firstOrNull().orEmpty()
    /** What a site item's target holds: all the host names, space-separated. */
    val target: String get() = domains.joinToString(" ")
}

@Serializable
private data class SiteFile(val version: Int = 1, val categories: List<String> = emptyList(), val sites: List<ListedSite> = emptyList())

/**
 * The bundled popular sites, for the suggestions under every "add a site" box. [suggest] ranks like the PC's search
 * (src/search.py): every word typed found - exactly or with a small typo - then name starts with the text, a domain
 * starts with it, a word of the name starts with it, the rest; then popularity.
 */
class SiteIndex(val sites: List<ListedSite>) {
    private class Entry(val site: ListedSite, val text: String, val tokens: List<String>, val name: String, val nameWords: List<String>, val domains: List<String>)

    private val entries = sites.map { s ->
        val text = SiteSearch.normalize(s.name + " " + s.domains.joinToString(" "))
        Entry(s, text, SiteSearch.words(text), SiteSearch.normalize(s.name), SiteSearch.words(s.name), s.domains.map(SiteSearch::normalize))
    }

    private val byHost: Map<String, ListedSite> = HashMap<String, ListedSite>().also { m ->
        sites.forEach { s -> s.domains.forEach { m.putIfAbsent(it.lowercase(), s) } }     // the more popular site keeps a shared host
    }

    /** The list site [host] is - the host itself or a parent domain of it ("www." and a path ignored) - else null. */
    fun siteOf(host: String): ListedSite? {
        var h = Sites.clean(host)
        if (h.isEmpty()) return null
        while (true) {
            byHost[h]?.let { return it }
            val dot = h.indexOf('.')
            if (dot < 0) return null
            h = h.substring(dot + 1)
        }
    }

    /** The list site some of the typed [hosts] is exactly one of the host names of (not just a subdomain: typing
     *  a.youtube.com doesn't block the whole of YouTube), else null. It gives the site its friendly name and all its hosts. */
    fun siteTyped(hosts: List<String>): ListedSite? =
        hosts.firstNotNullOfOrNull { h -> byHost[Sites.clean(h)] }

    /** Up to [limit] sites that fit the [text] typed, best first. */
    fun suggest(text: String, limit: Int = 8): List<ListedSite> {
        val q = SiteSearch.normalize(text).trim()
        if (SiteSearch.words(q).isEmpty()) return emptyList()
        class Hit(val score: Int, val group: Int, val rank: Int, val site: ListedSite)
        val hits = ArrayList<Hit>()
        entries.forEachIndexed { i, e ->
            val score = SiteSearch.score(q, e.text, e.tokens) ?: return@forEachIndexed
            val group = when {
                e.name.startsWith(q) -> 0
                e.domains.any { it.startsWith(q) } -> 1
                e.nameWords.any { it.startsWith(q) } -> 2
                else -> 3
            }
            hits.add(Hit(score, group, i, e.site))
        }
        return hits.sortedWith(compareBy({ it.score }, { it.group }, { it.rank })).take(limit).map { it.site }
    }
}

/** The forgiving search of src/search.py: capitals and accents ("ł" too) don't matter; every word must appear exactly
 *  or with a small typo (1 for words of 4-6 letters, 2 for longer ones, two swapped neighbours count as one). */
object SiteSearch {
    private val WORD = Regex("[a-z0-9]+")
    private val MARKS = Regex("\\p{M}+")

    fun normalize(text: String): String = MARKS.replace(Normalizer.normalize(text.lowercase().replace('ł', 'l'), Normalizer.Form.NFKD), "")

    fun words(normalized: String): List<String> = WORD.findAll(normalized).map { it.value }.toList()

    fun typosAllowed(word: String): Int = if (word.length <= 3) 0 else if (word.length <= 6) 1 else 2

    /** Edits (insert, delete, change, swap two neighbours) from [a] to [b]; anything over [limit] comes back as limit+1. */
    fun distance(a: String, b: String, limit: Int): Int {
        if (Math.abs(a.length - b.length) > limit) return limit + 1
        var prev2: IntArray? = null
        var prev = IntArray(b.length + 1) { it }
        for (i in 1..a.length) {
            val ca = a[i - 1]
            val cur = IntArray(b.length + 1)
            cur[0] = i
            for (j in 1..b.length) {
                val cb = b[j - 1]
                cur[j] = minOf(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + if (ca == cb) 0 else 1)
                if (prev2 != null && i > 1 && j > 1 && ca == b[j - 2] && a[i - 2] == cb) cur[j] = minOf(cur[j], prev2[j - 2] + 1)
            }
            if (cur.min() > limit) return limit + 1
            prev2 = prev; prev = cur
        }
        return prev[b.length]
    }

    /** How well [normalizedText] matches [query] (0 = every word found exactly; higher = more typos), or null. */
    fun score(query: String, normalizedText: String, tokens: List<String> = words(normalizedText)): Int? {
        val qs = words(normalize(query))
        if (qs.isEmpty()) return 0
        var total = 0
        for (q in qs) {
            if (q in normalizedText) continue
            val allowed = typosAllowed(q)
            if (allowed == 0) return null
            val n = q.length
            var best = allowed + 1
            for (t in tokens) {
                for (cand in setOf(t, t.take(n), t.take(n + 1), t.take(maxOf(n - 1, 1)))) best = minOf(best, distance(q, cand, allowed))
            }
            if (best > allowed) return null
            total += best
        }
        return total
    }
}

/** The bundled list, loaded once off the main thread. */
object SiteList {
    private val json = Json { ignoreUnknownKeys = true }
    @Volatile private var index: SiteIndex? = null

    /** The list from the file's text: a site's [ListedSite.rank] is its place in the file. */
    fun parse(text: String): SiteIndex {
        val file = json.decodeFromString(SiteFile.serializer(), text)
        return SiteIndex(file.sites.filter { it.domains.isNotEmpty() }.mapIndexed { i, s -> s.copy(rank = i + 1) })
    }

    /** Already loaded, else null (nothing is read here). */
    fun peek(): SiteIndex? = index

    /** The list, read from the app's assets on the first ask (an empty one if the file can't be read). */
    suspend fun get(ctx: Context): SiteIndex {
        index?.let { return it }
        val app = ctx.applicationContext
        return withContext(Dispatchers.IO) {
            index ?: (runCatching { parse(app.assets.open("sites/sites.json").bufferedReader().use { it.readText() }) }
                .getOrDefault(SiteIndex(emptyList()))).also { index = it }
        }
    }
}
