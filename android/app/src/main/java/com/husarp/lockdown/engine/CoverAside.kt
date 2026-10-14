package com.husarp.lockdown.engine

/**
 * A full-screen cover stepped aside for one of its buttons that finishes somewhere else (pure logic; block/BlockService.kt
 * feeds it what happens, on the steady clock, and does what it answers): the bedtime screen's "Disable alerts" (the
 * Anti-Bypass challenge in the app, then off tonight or a snooze) and a strict break's "Phone" (the dialer).
 * Adam, 2026-10-14: opening the app is no Dismiss - "it will still need you to wait this time".
 *
 * - Until the app says it is finished ([finished]: the challenge passed AND off tonight / a snooze picked), the cover is
 *   only out of the way. Leaving the target - home, another app, backing out of the app, the screen off, the app gone -
 *   brings it straight back ([Do.BACK]). Passing the challenge alone is not finishing: leaving then brings it back too.
 * - It comes back with the Dismiss wait it had left when it stepped aside ([leftMs]): the wait only runs while the cover
 *   is on screen, so stepping aside neither starts it over (the PC does) nor lets it run out unseen.
 * - Cancelled in the app ([cancelled]): back, with that wait - except once the wait is over and the screen has no
 *   "Challenge before Dismiss": then it is the Dismiss that could have been tapped ([Do.DISMISS]; 0.91.0, as on the PC).
 * - The target gets [startMs] to come up. Until it has been in front, only a NEW app in front (not [from], the one under
 *   the cover when it stepped aside: a late event of it is no leaving) brings the cover back early, and not having come
 *   up by then does too; once it has been in front, any other app does, at once. Not in front for [AWAY_MS] with no
 *   other app to name (the active window can't tell; Android pausing the app as it brings it up is shorter) brings it
 *   back too. Never aside longer than [MAX_MS].
 */
class CoverAside(
    val action: String,                 // what it stepped aside for: DISABLE / PHONE
    val leftMs: Long,                   // the Dismiss wait it had left then (0: none, or over)
    private val since: Long,            // when it stepped aside (steady clock, ms)
    private val from: String = "",      // the app in front under the cover then ("" = not known)
    private val startMs: Long = START_MS,
) {
    enum class Do { STAY, BACK, DISMISS, CLOSE }

    private var seen = false            // the target has been in front since
    private var awaySince: Long? = null // the target is not in front since then

    /** At [now]: [inTarget] the target (the app, the dialer) is in front - "returned" - or not: "left", [other] naming the
     *  app in front when it is another one (null: nothing to name, or it can't be told). */
    fun front(now: Long, inTarget: Boolean, other: String? = null): Do {
        if (now - since >= MAX_MS) return Do.BACK
        if (inTarget) { seen = true; awaySince = null; return Do.STAY }
        if (other != null && (seen || other != from)) return Do.BACK
        if (!seen) return if (now - since < startMs) Do.STAY else Do.BACK     // (it never came up)
        val away = awaySince ?: now.also { awaySince = it }
        return if (now - away >= AWAY_MS) Do.BACK else Do.STAY
    }

    /** The screen went off: back now (it shows once the phone is unlocked, still with [leftMs]). */
    fun screenOff(): Do = Do.BACK

    /** Cancelled (or backed out of) in the app. [challenge]: the screen asks a challenge before its Dismiss. */
    fun cancelled(challenge: Boolean): Do = if (leftMs > 0 || challenge) Do.BACK else Do.DISMISS

    /** Finished in the app: the challenge passed and an answer picked (off tonight, a snooze). The cover goes for good. */
    fun finished(): Do = Do.CLOSE

    companion object {
        const val DISABLE = "disable"
        const val PHONE = "phone"
        const val START_MS = 5_000L         // the app has this long to come up
        const val AWAY_MS = 1_500L          // not in front this long (no other app named) is leaving
        const val MAX_MS = 16 * 60_000L     // never aside longer than this, even in the app

        /** The Dismiss wait left at [nowMs] of a wait that ends at [readyAt] (both on the steady clock), in ms. */
        fun leftAt(readyAt: Long, nowMs: Long): Long = (readyAt - nowMs).coerceAtLeast(0)
    }
}
