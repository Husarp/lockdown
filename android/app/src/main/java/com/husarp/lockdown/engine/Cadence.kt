package com.husarp.lockdown.engine

/**
 * How often the background loops run (battery). Pure: the service passes in what it can see.
 *
 * While the screen is on everything keeps its quick pace, so blocking reacts exactly as before. Only with the screen
 * OFF (nothing can be used, nothing is in front) do the loops stretch; the service runs them at once again on
 * SCREEN_ON (a broadcast that always comes), and a window event of an app coming up speeds the block loop up too.
 * A screen that is on but locked keeps the quick pace as well: it is brief, and it needs no unlock broadcast.
 */
object Cadence {
    // ---- BlockService ----
    const val STEP_MS = 3000L               // count + block what's in front
    const val STEP_OFF_MS = 30_000L
    const val ALERT_MS = 5000L              // warnings / "block started"
    const val ALERT_OFF_MS = 60_000L
    const val BEDTIME_MS = 60_000L          // bedtime grayscale, daily housekeeping
    const val BEDTIME_OFF_MS = 5 * 60_000L
    const val REMIND_OFF_MS = 30_000L       // the reminders engine (bedtime, breaks, your reminders)

    fun stepMs(screenOn: Boolean) = if (screenOn) STEP_MS else STEP_OFF_MS

    /** The step loop is waiting out its stretched (screen off) delay but the screen is on: bring it back to the quick
     *  pace. The screen receiver does it at once; this is the net under it, for a receiver Android didn't register. */
    fun rearmSteps(currentDelayMs: Long, screenOn: Boolean) = screenOn && currentDelayMs > STEP_MS

    /** A screen broadcast that closes the usage gap (the time the screen was off is not use). Only SCREEN_ON and
     *  SCREEN_OFF do: USER_PRESENT comes about a second after SCREEN_ON (on a phone without a lock screen too), when
     *  the step may already be counting - closing the gap then would restart the visit and drop its seconds. */
    fun closesUsageGap(action: String?) = action == "android.intent.action.SCREEN_ON" || action == "android.intent.action.SCREEN_OFF"
    fun alertMs(screenOn: Boolean) = if (screenOn) ALERT_MS else ALERT_OFF_MS
    fun bedtimeMs(screenOn: Boolean) = if (screenOn) BEDTIME_MS else BEDTIME_OFF_MS

    /** The reminders tick. Kept quick for a minute after the screen goes off: the engine still counts that time as
     *  use ([RemindersEngine.USING_IDLE_SEC]), and its break clock adds a tick's time per tick. After that nothing
     *  counts, only clock times (bedtime, set times) matter - they have minutes of slack. */
    fun remindMs(screenOn: Boolean, idleSec: Double): Long =
        if (screenOn || idleSec < RemindersEngine.USING_IDLE_SEC + RemindersEngine.TICK_SEC) RemindersEngine.TICK_SEC * 1000L
        else REMIND_OFF_MS

    /** The bedtime screen's "Dismiss (N)": the delay until N next changes, so the button is redrawn once a second
     *  (it used to be four times a second) and not at all in between. [readyAt] / [nowMs]: the same monotonic clock. */
    fun countdownMs(readyAt: Long, nowMs: Long): Long {
        val left = readyAt - nowMs
        if (left <= 0) return 0
        val toNext = left % 1000
        return (if (toNext == 0L) 1000L else toNext) + 20
    }

    // ---- LinkService / IslandLink (the Island helper) ----
    const val LINK_POLL_MS = 1000L          // the app in front, from usage events
    const val LINK_POLL_OFF_MS = 60_000L
    const val LINK_TICK_MS = 3000L          // count + block (the fallback when the accessibility service can't run)
    const val LINK_TICK_OFF_MS = 60_000L
    const val LINK_SYNC_ACTIVE_MS = 5000L   // an Island app in front, or something waiting for main
    const val LINK_SYNC_IDLE_MS = 30_000L   // screen on, main's apps in use
    const val LINK_SYNC_OFF_MS = 3 * 60_000L   // (main calls the helper late after 10 min)

    /** The Island helper's watchdog (WorkManager): it brings a stopped helper service back. 15 min is WorkManager's
     *  shortest period, and the whole cost: one wake-up and a flag check when the service is running. */
    const val HELPER_WATCHDOG_MIN = 15L

    fun linkPollMs(screenOn: Boolean) = if (screenOn) LINK_POLL_MS else LINK_POLL_OFF_MS
    fun linkTickMs(screenOn: Boolean) = if (screenOn) LINK_TICK_MS else LINK_TICK_OFF_MS

    /** The wait between two syncs with main. A change of the app in front, SCREEN_ON and a "Resume now" sync at once
     *  (kick) whatever this says, so a slow pace only delays what nobody is waiting for. [failing]: the last sync did
     *  not get through (main isn't running or can't be reached): asking every 5 s would not help. */
    fun linkSyncMs(screenOn: Boolean, islandInFront: Boolean, pending: Boolean, failing: Boolean = false): Long = when {
        !screenOn -> LINK_SYNC_OFF_MS
        failing -> LINK_SYNC_IDLE_MS
        islandInFront || pending -> LINK_SYNC_ACTIVE_MS
        else -> LINK_SYNC_IDLE_MS
    }
}
