package com.husarp.lockdown.block

import android.content.Context
import com.husarp.lockdown.data.Store
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Bundled protection lists (like the PC app). All six ship inside the app as a popularity-ranked "top" set
 * (assets/protection/<key>.txt) so they work offline with no download - just toggle one on. Optionally you
 * can fetch the full list later, which replaces the bundled top-set for that category.
 */
object Protection {
    data class ListDef(val key: String, val name: String, val desc: String, val source: String)

    val LISTS = listOf(
        ListDef("adult", "Adult", "Porn and adult sites", "nsfw"),
        ListDef("gambling", "Gambling", "Betting and casino sites", "gambling"),
        ListDef("anime", "Manga & anime piracy", "Unofficial manga / anime / streaming sites", "anti.piracy"),
        ListDef("scam", "Scam & fake shops", "Scam and fake-shop domains", "fake"),
        ListDef("phishing", "Phishing", "Fake log-in pages that steal passwords", "phishing"),
        ListDef("malware", "Malware", "Malware and threat domains", "tif"),
    )

    // Fetched from the jsDelivr CDN, not raw.githubusercontent.com: restrictive networks (e.g. enterprise/DMZ
    // DNS) often refuse to resolve raw.githubusercontent.com while allowing normal CDNs and github.com.
    private fun fullUrl(source: String) =
        if (source == "phishing") "https://phishing.army/download/phishing_army_blocklist.txt"
        else "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@main/wildcard/$source-onlydomains.txt"

    private fun fetched(ctx: Context, key: String) = File(ctx.filesDir, "protection/$key.txt")

    @Volatile private var domains: Set<String> = emptySet()
    @Volatile private var allowed: Set<String> = emptySet()

    /** Domains of one list: the fetched full list if present, else the bundled top-set asset. */
    private fun linesOf(ctx: Context, key: String): Sequence<String> {
        val f = fetched(ctx, key)
        val reader = if (f.exists()) f.bufferedReader()
        else runCatching { ctx.assets.open("protection/$key.txt").bufferedReader() }.getOrNull() ?: return emptySequence()
        return reader.lineSequence().map { it.trim().lowercase() }.filter { it.isNotEmpty() && !it.startsWith("#") }
    }

    /** How many domains a list currently holds (for the UI), and whether it's the full list or the top-set. */
    fun info(ctx: Context, key: String): Pair<Int, Boolean> {
        val full = fetched(ctx, key).exists()
        return linesOf(ctx, key).count() to full
    }

    /** Load the enabled lists' domains into memory. Call at VPN start and after any change. */
    fun load(ctx: Context) {
        val p = Store.config.protection
        val set = HashSet<String>()
        for (key in p.enabled) linesOf(ctx, key).forEach { set.add(it) }
        domains = set
        allowed = p.allowed.map { it.lowercase() }.toSet()
    }

    /** Is [host] on an enabled protection list (and not excused)? */
    fun blocked(host: String): Boolean {
        if (domains.isEmpty()) return false
        var d = host.lowercase().removePrefix("www.")
        while (true) {
            if (d in allowed) return false
            if (d in domains) return true
            val dot = d.indexOf('.'); if (dot < 0) return false
            d = d.substring(dot + 1)
        }
    }

    /** Optional: download the full list for [key], replacing the bundled top-set. Off the main thread. */
    fun download(ctx: Context, key: String): Int {
        val def = LISTS.firstOrNull { it.key == key } ?: return 0
        fetched(ctx, key).parentFile?.mkdirs()
        val tmp = File(fetched(ctx, key).path + ".tmp")
        var n = 0
        val c = (URL(fullUrl(def.source)).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15000; readTimeout = 180000
        }
        c.inputStream.bufferedReader().use { r ->
            tmp.bufferedWriter().use { w ->
                r.forEachLine { line ->
                    val d = line.trim().removePrefix("*.")
                    if (d.isNotEmpty() && !d.startsWith("#")) { w.write(d); w.write("\n"); n++ }
                }
            }
        }
        tmp.renameTo(fetched(ctx, key))
        return n
    }
}
