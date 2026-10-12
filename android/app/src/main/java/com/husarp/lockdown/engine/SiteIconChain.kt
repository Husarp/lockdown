package com.husarp.lockdown.engine

import com.husarp.lockdown.data.Config
import kotlinx.serialization.Serializable
import java.net.URI

/**
 * Real icons for sites, the parts that need no Android (ui/SiteIcons.kt does the files, the network and the
 * pictures): which site domains are on your lists, which of them to fetch now, the fetch order, and when a failure
 * is tried again. Nothing here runs by itself - a fetch only starts when the app is opened or the lists change.
 */
object SiteIconChain {
    const val MAX_BYTES = 200 * 1024                  // one answer; a bigger one is not an icon (a home page is cut here)
    const val MAX_PX = 64                             // what is saved: at most this wide / high
    const val MIN_PX = 16                             // smaller than this is no icon
    const val REFRESH_MS = 30L * 24 * 60 * 60 * 1000  // a saved icon older than this is fetched again (the old one shows meanwhile)
    const val MAX_PER_RUN = 30                        // sites fetched per app open / list change
    const val MAX_PER_DAY = 120                       // ... and per day, so a big imported list can't keep the network busy
    const val KEEP_FILES = 600                        // saved icons kept; the oldest go when there are more
    const val DAY_MS = 24L * 60 * 60 * 1000
    private val RETRY_MIN = longArrayOf(10, 60, 360, 1440, 10080)   // after the 1st, 2nd... failure; the last one repeats

    /** The favicon service, asked only when the site itself gave none (or must not be asked). */
    fun serviceUrl(domain: String) = "https://www.google.com/s2/favicons?domain=$domain&sz=64"

    private val LABEL = Regex("[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
    private val PRIVATE_ENDS = listOf(".local", ".localhost", ".internal", ".lan", ".home", ".home.arpa", ".corp", ".intranet")

    /** [raw] as a public host name to look an icon up for, or null (an address, a name without a dot, a LAN name). */
    fun hostOf(raw: String): String? {
        var h = Sites.clean(raw).removePrefix("*.").trimEnd('.')
        if (h.isEmpty()) return null
        if (h.any { it.code > 127 }) h = runCatching { java.net.IDN.toASCII(h).lowercase() }.getOrNull() ?: return null
        if (h.length > 253) return null
        val labels = h.split('.')
        if (labels.size < 2 || !labels.all { LABEL.matches(it) }) return null
        if (labels.last().all { it.isDigit() }) return null                  // 192.168.0.1: an address, not a name
        if (PRIVATE_ENDS.any { h.endsWith(it) }) return null
        return h
    }

    /** The host names in a site item's target (space-separated) - usable ones only, the main one first. */
    fun hostsOf(target: String): List<String> =
        target.split(' ', ',', '\n', '\t').mapNotNull { if (it.isBlank()) null else hostOf(it) }.distinct()

    /** [host] and its parent domains, down to two labels: m.youtube.com, youtube.com. */
    fun walk(host: String): List<String> {
        val out = ArrayList<String>()
        var h = host
        while (true) {
            out.add(h)
            val dot = h.indexOf('.')
            if (dot < 0 || h.indexOf('.', dot + 1) < 0) return out
            h = h.substring(dot + 1)
        }
    }

    /** One site of your lists: [key] (its main host, the name its saved icon goes by) and all its [hosts]. */
    class Want(val key: String, val hosts: List<String>)

    /**
     * Every site on your lists, most-looked-at first: blocked sites you named yourself, the other blocked sites, the
     * sites added straight to a mode, the exceptions (a site that's never blocked) and the excused protection domains.
     * One entry per main host. Group members are blocked items, so they are among them.
     */
    fun wanted(cfg: Config, modes: List<Mode>): List<Want> {
        val seen = HashSet<String>()
        val out = ArrayList<Want>()
        fun add(hosts: List<String>) {
            val key = hosts.firstOrNull() ?: return
            if (seen.add(key)) out.add(Want(key, hosts))
        }
        val sites = cfg.items.filter { it.type == ItemType.SITE }
        sites.filter { '.' !in it.name }.forEach { add(hostsOf(it.target)) }
        sites.filter { '.' in it.name }.forEach { add(hostsOf(it.target)) }
        for (m in modes) for (x in m.extra) if (x.kind == ItemType.SITE) x.targets.forEach { add(hostsOf(it)) }
        cfg.keywords.exceptions.filter { '.' in it }.forEach { add(hostsOf(it)) }
        cfg.protection.allowed.forEach { add(hostsOf(it)) }
        return out
    }

    /** What the phone already has for a [Want]: a bundled icon, and/or when its saved icon was fetched (ms, else null). */
    class Have(val bundled: Boolean = false, val savedAt: Long? = null)

    /** The sites to fetch now, in order: no bundled icon, none saved (or the saved one is over [REFRESH_MS] old), not
     *  in a failure's waiting time, within this run's and today's limits. [setting] off = nothing, ever. */
    fun plan(setting: Boolean, wants: List<Want>, now: Long, book: IconBook, have: (Want) -> Have, busy: (String) -> Boolean = { false }): List<String> {
        if (!setting) return emptyList()
        val room = minOf(MAX_PER_RUN, book.room(now))
        if (room <= 0) return emptyList()
        return wants.asSequence()
            .filter { w ->
                val h = have(w)
                !h.bundled && (h.savedAt == null || now - h.savedAt >= REFRESH_MS) && book.retryOk(w.key, now) && !busy(w.key)
            }
            .map { it.key }.take(room).toList()
    }

    /** The bundled icon file ("youtube.com.png") for the first of [hosts] that has one - the host itself or a parent
     *  domain, so m.youtube.com uses youtube.com's - among the file [names] of the bundled icons folder, else null. */
    fun bundledFor(names: Set<String>, hosts: List<String>): String? =
        hosts.firstNotNullOfOrNull { h -> walk(h).firstNotNullOfOrNull { d -> "$d.png".takeIf { it in names } } }

    /** How much to shrink a [w] x [h] picture while decoding it ([BitmapFactory] inSampleSize, a power of 2) so it stays at
     *  least [MAX_PX] on its short side - a big picture is never decoded in full. */
    fun sampleFor(w: Int, h: Int): Int {
        var s = 1
        while (minOf(w, h) / (s * 2) >= MAX_PX) s *= 2
        return s
    }

    /** [w] x [h] scaled to fit [MAX_PX] (the same shape), unchanged when it already does. */
    fun fitTo(w: Int, h: Int): Pair<Int, Int> {
        val big = maxOf(w, h)
        return if (big <= MAX_PX) w to h else maxOf(1, w * MAX_PX / big) to maxOf(1, h * MAX_PX / big)
    }

    /** Waiting time after the [failures]th failure in a row (ms): 10 min, 1 h, 6 h, 1 day, then a week. */
    fun retryMs(failures: Int): Long = RETRY_MIN[(failures - 1).coerceIn(0, RETRY_MIN.size - 1)] * 60_000

    /** The saved icons to delete so [keep] remain (the oldest first). [files] = file name to saved-at. */
    fun toPrune(files: Map<String, Long>, keep: Int = KEEP_FILES): List<String> =
        if (files.size <= keep) emptyList() else files.entries.sortedBy { it.value }.take(files.size - keep).map { it.key }

    // ---- the fetch order ----

    /** The network, as the chain needs it. Every call is https only; null = no answer, an error, or too big. */
    interface Net {
        /** An image answer (at most [MAX_BYTES]). */
        fun image(url: String): ByteArray?
        /** A page: the first [MAX_BYTES] of it as text, and the address it ended up at after redirects. */
        fun page(url: String): Page?
    }
    class Page(val url: String, val text: String)

    /**
     * An icon for [domain]. The site itself first - /favicon.ico, then the icons its home page names - and only if
     * that gives nothing the favicon service. A [blocked] domain (blocked right now on this phone) is never asked
     * directly: the VPN would log a blocked try and Lockdown would show a notice - the service is asked about it.
     * [decode] turns an answer into the icon or null (not a picture, blank, too small); [alive] false stops the chain.
     */
    fun <T : Any> fetch(domain: String, blocked: Boolean, net: Net, alive: () -> Boolean = { true }, decode: (ByteArray) -> T?): T? {
        if (!blocked) {
            val ico = "https://$domain/favicon.ico"
            if (!alive()) return null
            net.image(ico)?.let(decode)?.let { return it }
            if (!alive()) return null
            val page = net.page("https://$domain/")
            if (page != null) for (u in iconLinks(page.text, page.url).filter { it != ico }.take(3)) {
                if (!alive()) return null
                net.image(u)?.let(decode)?.let { return it }
            }
        }
        if (!alive()) return null
        return net.image(serviceUrl(domain))?.let(decode)
    }

    /** The address [location] sends [from] to, when it's https and [hostOk] (never a redirect to a blocked site); else null. */
    fun redirect(from: String, location: String?, hostOk: (String) -> Boolean): String? {
        if (location.isNullOrBlank()) return null
        val to = runCatching { URI(from).resolve(location.trim()) }.getOrNull() ?: return null
        if (!"https".equals(to.scheme, ignoreCase = true)) return null
        val host = to.host?.lowercase() ?: return null
        return if (hostOk(host)) to.toString() else null
    }

    private val LINK = Regex("<link\\b[^>]*>", RegexOption.IGNORE_CASE)
    private val ATTR = Regex("([a-zA-Z-]+)\\s*=\\s*(?:\"([^\"]*)\"|'([^']*)'|([^\\s>]+))")

    /**
     * The icon addresses a home page names (<link rel="icon" / "shortcut icon" / "apple-touch-icon">), best first: a
     * size from 64 up (the smallest), else the largest from 32, then ones with no size, then smaller ones. Only https
     * addresses, resolved against [base]; SVG can't be shown, so it is left out.
     */
    fun iconLinks(html: String, base: String): List<String> {
        class Cand(val url: String, val rank: Int, val size: Int, val order: Int)
        val out = ArrayList<Cand>()
        LINK.findAll(html).forEachIndexed { i, m ->
            val attrs = HashMap<String, String>()
            ATTR.findAll(m.value).forEach { a ->
                attrs.putIfAbsent(a.groupValues[1].lowercase(), a.groupValues[2].ifEmpty { a.groupValues[3].ifEmpty { a.groupValues[4] } })
            }
            val rel = attrs["rel"]?.lowercase()?.split(Regex("\\s+")).orEmpty()
            if (rel.none { it.contains("icon") } || rel.any { it.contains("mask") }) return@forEachIndexed
            val href = attrs["href"]?.trim().orEmpty()
            if (href.isEmpty() || href.startsWith("data:", ignoreCase = true)) return@forEachIndexed
            val type = attrs["type"]?.lowercase().orEmpty()
            if (type.contains("svg") || href.substringBefore('?').lowercase().endsWith(".svg")) return@forEachIndexed
            val url = redirect(base, href) { true } ?: return@forEachIndexed
            val size = sizeOf(attrs["sizes"].orEmpty())
            val rank = when { size >= MAX_PX -> 0; size >= 32 -> 1; size == 0 -> 2; else -> 3 }
            out.add(Cand(url, rank, size, i))
        }
        return out.sortedWith(compareBy<Cand>({ it.rank }, { if (it.rank == 0) it.size else -it.size }, { it.order })).map { it.url }.distinct()
    }

    /** The biggest side in a `sizes` value ("16x16 32x32"), 0 when it names none ("any", missing). */
    fun sizeOf(sizes: String): Int =
        Regex("(\\d+)\\s*[xX]\\s*(\\d+)").findAll(sizes).maxOfOrNull { maxOf(it.groupValues[1].toInt(), it.groupValues[2].toInt()) } ?: 0
}

/** One site that could not be fetched: when, and how many times in a row. */
@Serializable
data class IconFail(val at: Long, val n: Int)

/**
 * The little the phone remembers between app opens (a file): which sites failed (so they wait before the next try)
 * and how many sites were tried today. Pure; ui/SiteIcons.kt reads and writes the file.
 */
@Serializable
data class IconBook(val day: Long = 0, val tries: Int = 0, val fails: Map<String, IconFail> = emptyMap()) {
    /** May [domain] be tried now (it has not failed, or its waiting time is over)? */
    fun retryOk(domain: String, now: Long): Boolean =
        fails[domain]?.let { now >= it.at + SiteIconChain.retryMs(it.n) } ?: true

    /** How many more sites may be tried today. */
    fun room(now: Long): Int = SiteIconChain.MAX_PER_DAY - (if (day == now / SiteIconChain.DAY_MS) tries else 0)

    fun tried(count: Int, now: Long): IconBook {
        val today = now / SiteIconChain.DAY_MS
        return copy(day = today, tries = (if (day == today) tries else 0) + count)
    }

    fun failed(domain: String, now: Long): IconBook =
        copy(fails = fails + (domain to IconFail(now, (fails[domain]?.n ?: 0) + 1)))

    fun fetched(domain: String): IconBook = if (domain in fails) copy(fails = fails - domain) else this

    /** Failures older than the longest waiting time are forgotten, and at most 2000 are kept. */
    fun pruned(now: Long): IconBook {
        val keep = fails.entries.filter { now - it.value.at < SiteIconChain.retryMs(99) * 2 }.sortedByDescending { it.value.at }.take(2000)
        return if (keep.size == fails.size) this else copy(fails = keep.associate { it.key to it.value })
    }
}
