package com.husarp.lockdown.link

/**
 * Which app is in front, from the profile's usage events (the helper's fallback when it has no accessibility
 * service). Pure: feed it (type, package, activity) events in order. It tracks activities, not packages: moving
 * inside one app logs PAUSED(a), RESUMED(b), STOPPED(a), and the late STOPPED(a) mustn't clear the app.
 */
object Foreground {
    const val RESUMED = 1      // UsageEvents.Event.ACTIVITY_RESUMED
    const val PAUSED = 2       // ACTIVITY_PAUSED
    const val STOPPED = 23     // ACTIVITY_STOPPED
    const val DESTROYED = 24   // ACTIVITY_DESTROYED

    /** The activities resumed now ("pkg/class"), oldest first. Split screen can have more than one. */
    fun reduce(resumed: List<String>, events: List<Triple<Int, String, String>>): List<String> {
        val r = LinkedHashSet(resumed)
        for ((type, pkg, cls) in events) {
            val a = "$pkg/$cls"
            when (type) {
                RESUMED -> { r.remove(a); r.add(a) }
                PAUSED, STOPPED, DESTROYED -> r.remove(a)
            }
        }
        return r.toList()
    }

    /** The app in front: the one resumed last that is still resumed. */
    fun front(resumed: List<String>): String? = resumed.lastOrNull()?.substringBefore('/')
}
