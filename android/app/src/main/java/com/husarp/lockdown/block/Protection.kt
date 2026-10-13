package com.husarp.lockdown.block

import android.content.Context
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.DomainSet
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Bundled protection lists (like the PC app). All six ship inside the app as a popularity-ranked "top" set
 * (assets/protection/<key>.txt) so they work offline with no download - just toggle one on. Optionally you
 * the weekly refresh (on Wi-Fi) fetches the bigger list, which replaces the bundled top-set for that category. In memory
 * and on disk every list is a compact site set (engine/DomainSet.kt): 8 bytes a site, a lookup in microseconds.
 * The phone keeps lighter lists than the PC for the three biggest (~5 MB in memory with all six on, not ~30 MB):
 * Malware is HaGeZi's TIF "mini" (~236k sites, not the full ~1.9M), Gambling HaGeZi's "mini" (~128k, not ~583k) and
 * Adult HaGeZi's NSFW list alone (~89k, without the Block List Project's ~950k porn list the PC adds).
 */
object Protection {
    /** A list: what the weekly refresh downloads ([urls]; the first has to come, a later one only adds) and the file
     *  it's kept in ([file].bin) - a new name when the list's source changes, so an older download is known. */
    data class ListDef(val key: String, val name: String, val desc: String, val urls: List<String>, val file: String = key)

    // Fetched from the jsDelivr CDN, not raw.githubusercontent.com: restrictive networks (e.g. enterprise/DMZ
    // DNS) often refuse to resolve raw.githubusercontent.com while allowing normal CDNs and github.com.
    // Phishing: the Block List Project's list first (as the PC), then Phishing Army - which now answers a script-less
    // download with a "One moment, please..." page, so on its own the full list came back empty (and an older version
    // saved that page's lines as the list, blocking nothing).
    private fun hagezi(name: String) = "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@main/wildcard/$name-onlydomains.txt"

    val LISTS = listOf(
        ListDef("adult", "Adult", "Porn and adult sites", listOf(hagezi("nsfw"))),
        ListDef("gambling", "Gambling", "Betting and casino sites", listOf(hagezi("gambling.mini")), file = "gambling.mini"),
        ListDef("anime", "Manga & anime piracy", "Unofficial manga / anime / streaming sites", listOf(hagezi("anti.piracy"))),
        ListDef("scam", "Scam & fake shops", "Scam and fake-shop domains", listOf(hagezi("fake"))),
        ListDef("phishing", "Phishing", "Fake log-in pages that steal passwords",
            listOf("https://cdn.jsdelivr.net/gh/blocklistproject/Lists@master/alt-version/phishing-nl.txt",
                "https://phishing.army/download/phishing_army_blocklist.txt")),
        ListDef("malware", "Malware", "Malware and threat domains", listOf(hagezi("tif.mini")), file = "malware.mini"),
    )

    private fun def(key: String) = LISTS.firstOrNull { it.key == key }

    /** The list's download, as a compact site set (engine/DomainSet.kt). */
    private fun fetched(ctx: Context, key: String) = File(ctx.filesDir, "protection/${def(key)?.file ?: key}.bin")
    /** A download from the list's earlier, bigger source (the full Malware / Gambling list, ~15 / ~5 MB in memory):
     *  still used until the next refresh brings the lighter one, which deletes it; null when the source never changed. */
    private fun stale(ctx: Context, key: String) =
        File(ctx.filesDir, "protection/$key.bin").takeIf { (def(key)?.file ?: key) != key }
    /** The file the list is read from: its download, else a stale one, else none (the bundled top-set). */
    private fun current(ctx: Context, key: String) =
        fetched(ctx, key).takeIf { it.exists() } ?: stale(ctx, key)?.takeIf { it.exists() }
    /** A full list fetched by an older version: plain text, read once into [fetched] and then deleted. */
    private fun oldText(ctx: Context, key: String) = File(ctx.filesDir, "protection/$key.txt")

    /** The enabled lists' sets. A list is read once and kept until its file changes (its time stamp). */
    @Volatile private var sets: List<DomainSet> = emptyList()
    @Volatile private var allowed: Set<String> = emptySet()
    private val cache = HashMap<String, Pair<String, DomainSet>>()

    /** One list's set: the downloaded list if present, else the bundled top-set asset. */
    @Synchronized private fun setOf(ctx: Context, key: String): DomainSet {
        runCatching { upgrade(ctx, key) }
        val f = current(ctx, key)
        val stamp = if (f != null) "${f.name}@${f.lastModified()}" else ""
        cache[key]?.let { if (it.first == stamp) return it.second }
        val set = (if (f != null) runCatching { f.inputStream().use { DomainSet.read(it) } }.getOrNull() else null)
            ?: runCatching { ctx.assets.open("protection/$key.txt").bufferedReader().useLines { lines ->
                DomainSet.Builder().apply { lines.forEach { l -> DomainSet.listLine(l)?.let(::add) } }.build() } }.getOrNull()
            ?: DomainSet.EMPTY
        cache[key] = stamp to set
        return set
    }

    /** A full list an older version saved as text (up to ~50 MB, millions of Strings once loaded): made a set once. */
    @Synchronized private fun upgrade(ctx: Context, key: String) {
        val txt = oldText(ctx, key)
        if (!txt.exists()) return
        if (current(ctx, key) == null) {
            val b = DomainSet.Builder()
            txt.bufferedReader().useLines { lines -> lines.forEach { l -> DomainSet.listLine(l)?.let(b::add) } }
            // from the old source, so kept under the old name (stale for Malware / Gambling until the next refresh)
            if (b.count > 0) save(File(ctx.filesDir, "protection/$key.bin"), b.build())
        }
        txt.delete()
    }

    @Synchronized private fun save(f: File, set: DomainSet) {
        f.parentFile?.mkdirs()
        val tmp = File(f.path + ".tmp")
        tmp.outputStream().use { set.write(it) }
        if (!tmp.renameTo(f)) { f.delete(); tmp.renameTo(f) }
    }

    /** How many domains a list currently holds (for the UI), and whether it's the full list or the top-set. Cheap: a
     *  fetched list's count is in its file's header. */
    fun info(ctx: Context, key: String): Pair<Int, Boolean> {
        runCatching { upgrade(ctx, key) }
        val f = current(ctx, key)
        val n = f?.inputStream()?.use { DomainSet.countOf(it) } ?: -1
        return if (n >= 0) n to true else setOf(ctx, key).size to false
    }

    /** Load the enabled lists' sets into memory (a list turned off, or replaced by a new download, is let go). Call
     *  at VPN start and after any change. */
    fun load(ctx: Context) {
        val p = Store.config.protection
        val on = p.enabled.distinct()
        sets = on.map { setOf(ctx, it) }.filter { it.size > 0 }
        allowed = p.allowed.map { it.lowercase() }.toSet()
        synchronized(this) { cache.keys.retainAll(on.toSet()) }
    }

    /** The weekly refresh, after downloading the enabled lists: a stale download of a list that's off (so it wasn't
     *  refreshed) is deleted, so turning the list on later can't bring the big one back. */
    @Synchronized fun dropStale(ctx: Context, enabled: Collection<String>) {
        for (d in LISTS) if (d.key !in enabled) stale(ctx, d.key)?.delete()
    }

    /** Is [host] on an enabled protection list (and not excused)? */
    fun blocked(host: String): Boolean {
        val s = sets
        if (s.isEmpty()) return false
        val ok = allowed
        return s.any { it.matches(host, ok) }
    }

    /** The weekly refresh: download the list for [key] (replacing the bundled top-set, an earlier download, or a stale
     *  one from a bigger source). Off the main thread. A page that isn't a list (a network's sign-in page, a "one
     *  moment" check) has no domains: the list in use is kept. */
    fun download(ctx: Context, key: String): Int {
        val def = def(key) ?: return 0
        val b = DomainSet.Builder()
        var read = 0L
        for ((i, url) in def.urls.withIndex()) {
            // the first source has to come; a later one that fails or sends no list leaves out only its own part
            val got = runCatching {
                val c = (URL(url).openConnection() as HttpURLConnection).apply { connectTimeout = 15000; readTimeout = 180000 }
                c.inputStream.bufferedReader().use { r ->
                    r.forEachLine { line ->
                        read += line.length + 1
                        if (read > MAX_DOWNLOAD) throw java.io.IOException("${def.name} list is unexpectedly big")
                        DomainSet.listLine(line)?.let(b::add)
                    }
                }
            }
            if (i == 0) got.getOrThrow()
        }
        if (b.count == 0) return 0
        save(fetched(ctx, key), b.build())
        stale(ctx, key)?.delete()
        return b.count
    }

    private const val MAX_DOWNLOAD = 150L * 1024 * 1024       // as the PC
}
