package com.husarp.lockdown.engine

/** Site addresses as people type them. Phone keyboards add a space after a dot ("youtube. com"), so no site
 *  address ever keeps a space. */
object Sites {
    /** One address: no whitespace, lower case, without http(s)://, www. or a path. */
    fun clean(raw: String): String =
        raw.filterNot(Char::isWhitespace).lowercase().removePrefix("https://").removePrefix("http://").removePrefix("www.").substringBefore("/")

    /** A box that takes several addresses: a space next to a dot is dropped, other spaces (or commas) separate them. */
    fun joinDots(raw: String): String = raw.replace(Regex("""\s*\.\s*"""), ".")

    fun cleanList(raw: String): List<String> =
        joinDots(raw).split(Regex("""[\s,]+""")).map(::clean).filter { it.isNotEmpty() }.distinct()

    /** The sites on one line of an imported list: a hosts-file line's "0.0.0.0" / "127.0.0.1" is not a site. */
    fun importLine(raw: String): List<String> =
        cleanList(raw).filter { '.' in it && !it.matches(Regex("""\d+(\.\d+){3}""")) }

    /** A box for a site or a word: with a dot it is a site, so its spaces go; a word or phrase keeps them. */
    fun cleanIfSite(raw: String): String = if ('.' in raw) raw.filterNot(Char::isWhitespace) else raw
}
