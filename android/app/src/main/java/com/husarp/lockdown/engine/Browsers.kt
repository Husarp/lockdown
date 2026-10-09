package com.husarp.lockdown.engine

/**
 * The browsers whose address bar the accessibility service reads: site blocks, time on sites and blocked words in
 * the browser all come from it. A browser listed without a bar id is blind to all of that (DuckDuckGo and Opera
 * were), so each one needs at least one; a unit test checks it.
 */
object Browsers {
    // Browser families that share their address bar's id (the ids as in Bitwarden's autofill list). Each version a
    // browser ships separately (beta, nightly, a fork) is its own package: one left out isn't watched at all.
    private val CHROMIUM = listOf(
        "com.android.chrome", "com.chrome.beta", "com.chrome.dev", "com.chrome.canary",
        "com.brave.browser", "com.brave.browser.bt1", "com.brave.browser_beta", "com.brave.browser_nightly",
        "com.microsoft.emmx", "com.microsoft.emmx.beta",
        "com.kiwibrowser.browser", "com.vivaldi.browser", "org.cromite.cromite",
    )
    private val GECKO = listOf(
        "org.mozilla.firefox", "org.mozilla.firefox_beta", "org.mozilla.fenix", "org.mozilla.focus", "org.mozilla.klar",
        "org.torproject.torbrowser", "io.github.forkmaintainers.iceraven",
    )

    /** Package -> the view ids that hold its address, tried in order (the first with text wins; an
     *  empty box's hint, "Search", doesn't count). */
    val BARS: Map<String, List<String>> = CHROMIUM.associateWith { listOf("url_bar") } +
        GECKO.associateWith { listOf("mozac_browser_toolbar_url_view", "url_bar_title") } + mapOf(
        "com.sec.android.app.sbrowser" to listOf("location_bar_edit_text"),
        "com.opera.browser" to listOf("url_field"),
        "com.opera.mini.native" to listOf("url_field"),
        "com.opera.gx" to listOf("addressbarEdit"),
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
