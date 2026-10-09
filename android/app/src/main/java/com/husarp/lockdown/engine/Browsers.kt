package com.husarp.lockdown.engine

/**
 * The browsers whose address bar the accessibility service reads: site blocks, time on sites and blocked words in
 * the browser all come from it. A browser listed without a bar id is blind to all of that (DuckDuckGo and Opera
 * were), so each one needs at least one; a unit test checks it.
 */
object Browsers {
    /** Package -> the view ids that hold its address, tried in order (the first with text wins). */
    val BARS: Map<String, List<String>> = mapOf(
        "com.android.chrome" to listOf("url_bar"),
        "com.brave.browser" to listOf("url_bar"),
        "com.brave.browser.bt1" to listOf("url_bar"),
        "com.microsoft.emmx" to listOf("url_bar"),
        "com.sec.android.app.sbrowser" to listOf("location_bar_edit_text"),
        "org.mozilla.firefox" to listOf("mozac_browser_toolbar_url_view", "url_bar_title"),
        "com.opera.browser" to listOf("url_field"),
        "com.duckduckgo.mobile.android" to listOf(
            "omnibarTextInput",                         // the address bar, at the top or the bottom
            "omnibarTextInputBottom",                   // the search widget's screen with the bar at the bottom
            "inputField",                               // the "Search / Duck.ai" box
            "customTabDomain", "customTabDomainOnly",   // a link opened from another app: just its site
        ),
    )

    fun isBrowser(pkg: String) = pkg in BARS

    /** The full view ids ("package:id/name") of [pkg]'s address bar. */
    fun barIds(pkg: String): List<String> = BARS[pkg].orEmpty().map { "$pkg:id/$it" }

    private const val DDG = "com.duckduckgo.mobile.android"

    /** The site in [pkg]'s address bar [text], or null for a search phrase. DuckDuckGo's Duck Player (YouTube videos
     *  without YouTube's page: "duck://player/<id>", or "Duck Player" when DuckDuckGo shows short addresses) is
     *  youtube.com, as is YouTube's privacy player (youtube-nocookie.com). */
    fun hostOf(pkg: String, text: String?): String? {
        val t = text?.trim()?.lowercase() ?: return null
        if (pkg == DDG && (t.startsWith("duck://player") || t == "duck player")) return "youtube.com"
        if (t.isEmpty() || " " in t) return null                 // a search phrase, not a host
        val noScheme = t.substringAfter("://", t)
        val host = noScheme.substringBefore('/').substringBefore('?').removePrefix("www.")
        return if ("." in host) Sites.alias(host) ?: host else null
    }
}
