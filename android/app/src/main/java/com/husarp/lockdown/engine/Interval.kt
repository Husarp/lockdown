package com.husarp.lockdown.engine

import kotlin.math.roundToLong

/**
 * How often an alert comes (the bedtime steps, breaks and their snooze, your reminders' "every", the "N min left"
 * repeats), in seconds, as typed and shown - the PC's parse_interval / seconds_text. The reminders and the warnings
 * tick every 5 s, so 5 s is the shortest.
 */
object Interval {
    const val MIN_SEC = 5
    const val MAX_SEC = 24 * 3600

    private val HOURS = Regex("""(\d+)\s*h(?:\s*(\d+)\s*(?:m|min|mins|minutes?)?)?""")
    private val SECONDS = Regex("""(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds?)""")
    private val MINUTES = Regex("""(\d+(?:\.\d+)?)\s*(?:m|min|mins|minutes?)?""")

    /** "30s" / "30 s" / "45 sec" -> 30, "5" / "5 min" -> 300 (a bare number is minutes, as it always was), "1.5" ->
     *  90, "1h" -> 3600, "1h30" -> 5400. Null when it isn't a time or is outside [MIN_SEC]..[MAX_SEC]. */
    fun parse(text: String): Int? {
        val t = text.trim().lowercase().replace(',', '.')
        val sec = HOURS.matchEntire(t)?.let { (it.groupValues[1].toDouble() * 60 + (it.groupValues[2].toDoubleOrNull() ?: 0.0)) * 60 }
            ?: SECONDS.matchEntire(t)?.let { it.groupValues[1].toDouble() }
            ?: MINUTES.matchEntire(t)?.let { it.groupValues[1].toDouble() * 60 }
            ?: return null
        return sec.roundToLong().takeIf { it in MIN_SEC..MAX_SEC }?.toInt()
    }

    /** 30 -> "30 s", 90 -> "90 s", 300 -> "5 min", 3600 -> "1h", 5400 -> "1h30"; 0 -> "Off". */
    fun text(sec: Int): String = when {
        sec <= 0 -> "Off"
        sec % 60 != 0 -> "$sec s"
        sec < 3600 -> "${sec / 60} min"
        sec % 3600 == 0 -> "${sec / 3600}h"
        else -> "%dh%02d".format(sec / 3600, sec % 3600 / 60)
    }
}
