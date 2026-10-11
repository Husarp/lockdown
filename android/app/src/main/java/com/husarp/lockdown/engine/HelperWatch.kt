package com.husarp.lockdown.engine

/**
 * Main's watch on the Island helper: has it been stopped? (Island's Lockdown copy runs in a foreground service that
 * the user can end from Android's "Active apps" list, or Force stop.) Pure: [BlockService]'s minute tick passes in
 * what it sees, and gets back what to do.
 *
 * "Quiet" is counted in SCREEN-ON time since the helper last checked in, on main's own clock (elapsedRealtime, so
 * setting the phone's clock changes nothing). The helper checks in at once when the screen comes on and then at
 * least every 30 s, so a helper that is running has no quiet screen-on time to speak of; two minutes of it is a
 * stopped helper. Screen-off time is never counted: Doze freezes both copies for hours, and a look after that sleep
 * must not read it as silence. So the time between two looks counts only if the screen was on at the first and the looks
 * were no more than [MAX_LOOK_GAP_MS] apart (they come every minute while it is on, and when the screen goes off, so the
 * interval that ends at that look still counts: short bursts of use add up); a longer gap counts for nothing.
 */
object HelperWatch {
    /** Screen-on time with no check-in that makes the helper "silent". */
    const val QUIET_ON_MS = 2 * 60_000L
    /** While silent, the alert comes again this often (screen on). */
    const val REPEAT_MS = 5 * 60_000L
    /** Looks further apart than this add nothing to the quiet time (the loop is every minute with the screen on). */
    const val MAX_LOOK_GAP_MS = 90_000L

    data class State(
        val lastLook: Long = -1,          // elapsed ms of the last look (-1: none yet)
        val lastOn: Boolean = false,      // the screen was on at it
        val heard: Long = 0,              // the helper's last check-in (elapsed ms) as of that look
        val quietOnMs: Long = 0,          // screen-on time since it checked in
        val silent: Boolean = false,      // an episode of silence is open (counted, alert up)
        val alertedAt: Long = -1,         // elapsed ms the alert was last put up (-1: not yet in this process)
    )

    /** NONE; RAISE the helper just went silent (count it, put the alert up); REPEAT the alert again; CLEAR the
     *  helper is back (or nothing is watched any more): take the alert down. */
    enum class Act { NONE, RAISE, REPEAT, CLEAR }

    data class Out(val state: State, val act: Act)

    /** The state of an episode that was open when the process started: still silent until the helper is heard. */
    fun resumed(heardAt: Long) = State(heard = heardAt, quietOnMs = QUIET_ON_MS, silent = true)

    /**
     * One look. [heardAt]: when the helper last checked in (elapsed ms; a new value means it did). [watching]: linked,
     * blocking on, the setting on, and Island not paused (a paused work profile can't run anything, so nothing
     * there needs covering).
     */
    fun look(s: State, now: Long, heardAt: Long, screenOn: Boolean, watching: Boolean): Out {
        if (!watching) return Out(State(heard = heardAt), if (s.silent) Act.CLEAR else Act.NONE)
        val back = heardAt != s.heard
        var quiet = if (back) 0L else s.quietOnMs
        val gap = now - s.lastLook
        if (!back && s.lastOn && s.lastLook >= 0 && gap in 0..MAX_LOOK_GAP_MS) quiet += gap
        val silent = quiet >= QUIET_ON_MS
        val next = State(now, screenOn, heardAt, quiet, silent, s.alertedAt)
        return when {
            !silent -> Out(next.copy(alertedAt = -1), if (s.silent) Act.CLEAR else Act.NONE)
            !s.silent -> Out(next.copy(alertedAt = now), Act.RAISE)
            screenOn && (s.alertedAt < 0 || now - s.alertedAt >= REPEAT_MS) -> Out(next.copy(alertedAt = now), Act.REPEAT)
            else -> Out(next, Act.NONE)
        }
    }

    /** After main's process starts, and after Island's profile becomes usable again (unlocked after a reboot, switched
     *  back on), the helper needs a moment to start: no new silence can begin for this long. Not applied to an episode
     *  already open, and only a profile that can run is ever watched, so keeping it running with the helper stopped is
     *  still caught. */
    const val GRACE_MS = 2 * 60_000L

    /** Whether to watch at this look: [watching] and, unless an episode is already open, past the start-up grace. */
    fun watchedNow(watching: Boolean, episodeOpen: Boolean, now: Long, graceUntil: Long) =
        watching && (episodeOpen || now >= graceUntil)

    /** "once", "twice", "5 times" - for the count on the Island card and in the alert. */
    fun times(n: Int) = when (n) { 1 -> "once"; 2 -> "twice"; else -> "$n times" }
}

/** The Island helper's own side: is its service up, and should something try to bring it back? */
object HelperKeep {
    /** The watchdog (and the boot / update receiver) starts the service only for a linked helper whose service
     *  isn't running. A service already up is left alone: starting it again would only repeat its start-up. */
    fun shouldStart(isHelper: Boolean, serviceAlive: Boolean) = isHelper && !serviceAlive
}
