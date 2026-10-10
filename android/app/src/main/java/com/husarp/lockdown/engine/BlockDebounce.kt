package com.husarp.lockdown.engine

/**
 * After OK on the notice of a blocked app, the app closing and the home-screen transition still send window events
 * naming that app; each one looked like the app being opened and brought the notice up again (three times in all).
 * The app's events are leftovers until it is really brought to front again: [SETTLE_MS] after OK, or [REOPEN_MS]
 * after another app (the launcher) came up - a reopen takes the user longer than that. Pure: times are passed in.
 */
class BlockDebounce {
    private var pkg: String? = null
    private var dismissedAt = 0L
    private var otherAt = -1L

    /** OK was pressed on the notice for app [pkg]. */
    fun dismissed(pkg: String, nowMs: Long) { this.pkg = pkg; dismissedAt = nowMs; otherAt = -1L }

    /** A window of another app came to the front (the launcher): the clock for a reopen starts at the first one. */
    fun otherFront(pkg: String, nowMs: Long) {
        if (this.pkg != null && pkg != this.pkg && otherAt < 0) otherAt = nowMs
    }

    /** [pkg] is in front per an event or the tick: true = a leftover of the dismissed block, don't judge it. */
    fun leftover(pkg: String, nowMs: Long): Boolean {
        if (pkg != this.pkg) return false
        val reopened = nowMs - dismissedAt >= SETTLE_MS || (otherAt >= 0 && nowMs - otherAt >= REOPEN_MS)
        if (reopened) this.pkg = null
        return !reopened
    }

    companion object {
        const val SETTLE_MS = 3000L
        const val REOPEN_MS = 1500L
    }
}
