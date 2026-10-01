package com.husarp.lockdown.ui

/** "2h 14m", "43m", "12s" - compact human duration from milliseconds. */
fun fmtDuration(ms: Long): String {
    val totalMin = ms / 60_000
    val h = totalMin / 60
    val m = totalMin % 60
    return when {
        h > 0 -> "${h}h ${m}m"
        m > 0 -> "${m}m"
        else -> "${ms / 1000}s"
    }
}

/** "23:00" from minutes-since-midnight. */
fun fmtHhmm(min: Int): String = "%02d:%02d".format(min / 60, min % 60)
