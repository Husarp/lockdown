package com.husarp.lockdown.update

/**
 * The updater's pure logic (no Android), unit-tested in UpdateLogicTest.
 * APP-STANDARDS sections 2 and 3: compare versions as numbers, find the APK by its ".apk" ending,
 * ask GitHub at most every 5 minutes, and a banner ✕ that hides until the next app start.
 */
object UpdateLogic {
    const val MIN_CHECK_GAP_MS = 5 * 60_000L

    /** A release file as GitHub lists it. */
    data class Asset(val name: String, val url: String, val size: Long)

    /** Compares two versions number by number ("0.5.10" > "0.5.9"); a leading "v" and any "-suffix" are ignored. */
    fun compare(a: String, b: String): Int {
        val x = parts(a); val y = parts(b)
        for (i in 0 until maxOf(x.size, y.size)) {
            val c = x.getOrElse(i) { 0 }.compareTo(y.getOrElse(i) { 0 })
            if (c != 0) return c
        }
        return 0
    }

    fun isNewer(candidate: String, current: String): Boolean = compare(candidate, current) > 0

    private fun parts(v: String): List<Int> =
        v.trim().removePrefix("v").removePrefix("V").substringBefore('-').substringBefore('+')
            .split('.').map { p -> p.takeWhile { it.isDigit() }.toIntOrNull() ?: 0 }

    /** The version in an APK's file name ("LockdownMobile-0.5.11.apk" -> "0.5.11"), or null if it has none. */
    fun apkVersion(name: String): String? {
        if (!name.endsWith(".apk", ignoreCase = true)) return null
        return Regex("""\d+(?:\.\d+)+""").findAll(name.dropLast(4)).lastOrNull()?.value
    }

    /**
     * The Android update among a release's files: the .apk files (found by their ending, never an exact
     * name) that carry a version, the highest one. The release tag is the PC version, so it is not used.
     */
    fun pickApk(assets: List<Asset>): Pair<Asset, String>? =
        assets.mapNotNull { a -> apkVersion(a.name)?.let { a to it } }
            .maxWithOrNull { p, q -> compare(p.second, q.second) }

    /** True when GitHub may be asked again ([lastMs] = time of the last ask, 0 = never). */
    fun mayCheck(nowMs: Long, lastMs: Long, gapMs: Long = MIN_CHECK_GAP_MS): Boolean =
        lastMs == 0L || nowMs - lastMs >= gapMs || nowMs < lastMs

    /** The banner's ✕: hides it until the app is next started (not when coming back from another app). */
    class BannerGate {
        var dismissed = false
            private set

        fun dismiss() { dismissed = true }

        /** A fresh app start (not a return from another app, not a rotation): the banner may show again. */
        fun onAppStart() { dismissed = false }

        fun visible(latest: String?, current: String): Boolean =
            !dismissed && latest != null && isNewer(latest, current)
    }
}
