package com.husarp.lockdown.engine

import kotlinx.serialization.Serializable
import java.text.Normalizer

/**
 * Bad-word check, ported from the PC keywords.py. Built-in Adult (English) and Adult (Polish) lists, on by
 * default, plus your own words. Whole-word match; a trailing "*" also matches longer words; a multi-word
 * entry is a phrase; accents are ignored. Exceptions: a site (has a dot → skipped) or a word (never counts).
 */
@Serializable
data class KeywordsCfg(
    val enabled: Boolean = true,
    val safeSearch: Boolean = true,
    val youtube: Boolean = true,
    val action: String = "close",                 // "close" | "back"
    val lists: Map<String, Boolean> = mapOf("adult_en" to true, "adult_pl" to true),
    val off: List<String> = emptyList(),          // ready-list words turned off
    val words: List<String> = emptyList(),        // your own words
    val wordsOn: Boolean = true,
    val exceptions: List<String> = emptyList(),
    val exceptionsOn: Boolean = true,
)

object Keywords {
    val READY: Map<String, Pair<String, List<String>>> = mapOf(
        "adult_en" to ("Adult words - English" to listOf(
            "porn*", "xxx", "xvideos", "xnxx", "xhamster", "redtube", "youporn", "youjizz", "spankbang", "spankwire",
            "brazzers", "bangbros", "realitykings", "naughtyamerica", "eporner", "tnaflix", "tube8", "motherless",
            "hqporner", "beeg", "txxx", "hclips", "fapello", "onlyfans", "fansly", "manyvids", "clips4sale", "chaturbate",
            "stripchat", "livejasmin", "camsoda", "bongacams", "myfreecams", "cam4", "rule34", "rule 34", "r34", "e621",
            "nhentai", "hentai*", "hanime", "ecchi", "futanari", "jav", "nsfw", "lewd", "nudes", "nude pics",
            "nude photos", "naked girls", "naked women", "boobs", "tits", "titties", "pussy", "blowjob*", "handjob*",
            "footjob*", "deepthroat*", "cumshot*", "creampie*", "milf*", "gilf", "bdsm", "bondage", "fetish*",
            "gangbang*", "threesome*", "orgy", "orgies", "orgasm*", "masturba*", "erot*", "camgirl*", "camboy*",
            "sexcam*", "sexting", "sexchat", "sex video*", "sex tape*", "sex chat", "sex cam*", "free sex", "hot sex",
            "sex dating", "anal sex", "oral sex", "bukkake", "cuckold", "upskirt", "strip club*", "stripper*",
            "striptease", "dildo*", "vibrator*", "sex toy*", "fleshlight", "adult video*", "adult movie*", "adult chat",
            "adult dating", "escort service*",
        )),
        "adult_pl" to ("Adult words - Polish" to listOf(
            "porno*", "seks", "seksi", "sexi", "darmowy seks", "seks kamerki", "sex kamerki", "sekstelefon",
            "seks telefon", "sex telefon", "sex anonse", "erotyk*", "erotycz*", "ruchanie", "ruchac", "rucha",
            "wyruchal*", "wyruchan*", "bzykanie", "bzykac", "cipka", "cipki", "cipa", "cycki", "cycuszki", "cycate",
            "nago", "nagie", "golasy", "golaski", "rozbierane*", "rozbieranki", "lodzik*", "obciaganie", "dziwka",
            "dziwki", "prostytutk*", "roksa", "anonse towarzyskie", "masturbac*", "walenie konia", "orgia", "orgie",
            "striptiz", "filmy dla doroslych",
        )),
    )

    /** Lowercase, accents stripped (ł → l too). */
    fun normalize(text: String): String {
        val pre = text.lowercase().replace("ł", "l").replace("Ł", "l")
        val nfkd = Normalizer.normalize(pre, Normalizer.Form.NFKD)
        return buildString { for (c in nfkd) if (Character.getType(c) != Character.NON_SPACING_MARK.toInt()) append(c) }
    }

    private val TOKEN = Regex("[a-z0-9]+")
    private fun words(text: String): List<String> = TOKEN.findAll(normalize(text)).map { it.value }.toList()

    fun activeWords(cfg: KeywordsCfg): List<String> {
        val off = cfg.off.toSet()
        val ready = READY.entries.filter { cfg.lists[it.key] == true }.flatMap { it.value.second }.filter { it !in off }
        val yours = if (cfg.wordsOn) cfg.words.filter { it !in ready } else emptyList()
        return ready + yours
    }

    private fun looksLikeAddress(text: String): Boolean {
        val t = text.trim()
        return t.isNotEmpty() && !t.contains(" ") && (t.contains(".") || t.contains("/"))
    }

    private fun host(address: String): String =
        runCatching { java.net.URI(if ("://" in address) address else "http://$address").host ?: "" }.getOrDefault("")

    /** The first blocked word found in the tab's address / title, or null. */
    fun find(address: String?, title: String?, cfg: KeywordsCfg): String? {
        if (!cfg.enabled) return null
        val addr = if (address != null && looksLikeAddress(address)) address else ""
        val h = if (addr.isNotEmpty()) host(addr) else ""
        val exceptions = if (cfg.exceptionsOn) cfg.exceptions.map { normalize(it) } else emptyList()
        if (h.isNotEmpty() && exceptions.any { "." in it && (h == it || h.endsWith(".$it")) }) return null
        val ignored = exceptions.filter { "." !in it }.toSet()
        val fromAddress = words(decodePlus(addr))
        val fromTitle = words(title ?: "")
        val tokens = fromAddress + fromTitle
        val joined = " ${fromAddress.joinToString(" ")} | ${fromTitle.joinToString(" ")} "  // a phrase can't span both
        for (entry in activeWords(cfg)) {
            val word = normalize(entry).trim()
            if (word.isEmpty() || word in ignored) continue
            if (word.endsWith("*")) {
                val stem = word.dropLast(1)
                if (" " in stem) { if (" $stem" in joined) return entry }
                else if (tokens.any { it.startsWith(stem) }) return entry
            } else if (" $word " in joined) return entry
        }
        return null
    }

    private fun decodePlus(s: String): String =
        runCatching { java.net.URLDecoder.decode(s.replace("+", " "), "UTF-8") }.getOrDefault(s)
}
