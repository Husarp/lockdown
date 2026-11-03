package com.husarp.lockdown.engine

/** Suppress closing-window events and stale tick state after a block. Foreground confirmation releases suppression after the closing
 * transition; missing confirmation can suppress only for a bounded time. */
class BlockDebounce {
    private var pkg: String? = null
    private var dismissedAt = 0L
    private var otherAt = -1L

    fun dismissed(pkg: String, nowMs: Long) {
        // OK after automatic Home must not forget that the launcher has already been seen.
        if (this.pkg == pkg) return
        this.pkg = pkg; dismissedAt = nowMs; otherAt = -1L
    }

    /** Call only for a confirmed foreground window, never a delayed package-only event. */
    fun otherFront(pkg: String, nowMs: Long) {
        if (this.pkg != null && pkg != this.pkg && otherAt < 0) otherAt = nowMs
    }

    /** A confirmed return after leaving can be immediate. Without confirmation, retry after a bounded
     * closing interval so a missing accessibility root cannot disable blocking indefinitely. */
    fun leftover(pkg: String, nowMs: Long, confirmedOpening: Boolean = false): Boolean {
        if (pkg != this.pkg) return false
        val elapsed = nowMs - dismissedAt
        val reopened = (confirmedOpening && (otherAt >= 0 || elapsed >= SETTLE_MS)) || elapsed >= MAX_SUPPRESS_MS
        if (reopened) this.pkg = null
        return !reopened
    }

    companion object {
        fun confirmsWindowState(activePkg: String?, eventPkg: String, eventAgeMs: Long): Boolean =
            activePkg == eventPkg || (activePkg == null && eventAgeMs in 0L..1000L)

        const val SETTLE_MS = 3000L
        const val MAX_SUPPRESS_MS = 10_000L
    }
}
