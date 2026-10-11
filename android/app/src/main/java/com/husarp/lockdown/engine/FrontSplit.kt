package com.husarp.lockdown.engine

import java.time.Duration
import java.time.LocalDateTime

/** [seconds] of [app] (and its [site], in a browser) in front, ending at [at] - the moment that picks the history's minute. */
data class Charge(val at: LocalDateTime, val app: String, val site: String, val seconds: Double)

/**
 * Decides what the history charges the seconds since the last step to. A step runs every few seconds and, at once,
 * when a window or the browser's address changes - so the seconds since the previous step belong to what was in front
 * THEN, not to what the step finds in front now. That is what splits the time right when a site changes, the browser
 * leaves the front, or the screen goes off / a block comes up ([idle]): the last seconds go to the last thing, and
 * nothing is charged for a time when nothing was in front. A stretch over a minute change is cut at the minute, so
 * midnight puts each part on its own day. Pure; statistics only.
 */
class FrontSplit {
    private var last: Pair<String, String>? = null      // (app, site) at the last step; null: nothing was in front

    /** A step that finds [app] / [site] in front, [seconds] after the last step (at [now]). */
    fun step(now: LocalDateTime, app: String, site: String, seconds: Double): List<Charge> {
        val before = last
        last = app to site
        return charge(now, before, seconds)
    }

    /** A step that finds nothing of the phone's in front (screen off, a block up, Lockdown itself). */
    fun idle(now: LocalDateTime, seconds: Double): List<Charge> {
        val before = last
        last = null
        return charge(now, before, seconds)
    }

    private fun charge(now: LocalDateTime, key: Pair<String, String>?, seconds: Double): List<Charge> {
        if (key == null || seconds <= 0.0) return emptyList()
        val start = now.minusNanos((seconds * 1e9).toLong())
        val minute = now.withSecond(0).withNano(0)
        if (!start.isBefore(minute)) return listOf(Charge(now, key.first, key.second, seconds))
        val early = Duration.between(start, minute).toNanos() / 1e9           // the part before this minute began
        return listOf(Charge(start, key.first, key.second, early), Charge(now, key.first, key.second, seconds - early))
    }
}
