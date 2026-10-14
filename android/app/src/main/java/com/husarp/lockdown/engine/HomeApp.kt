package com.husarp.lockdown.engine

/**
 * The home screen (the default launcher) isn't "using an app": its time is left out of screen-time totals and the app
 * lists everywhere - Home's total and "Time per app", Insights (totals, chart, top apps, patterns), All apps, the
 * widget, the goal and weekly reminders and the CSV - as Android's Digital Wellbeing does. Limits don't count from
 * these numbers and are unaffected. Pure: usage/Usage.homePkg finds the package and the readers call these.
 */
object HomeApp {
    /** What Android resolves the home intent to while no default launcher is chosen (its chooser). */
    const val RESOLVER = "android"
    // never "the launcher": the chooser, and Settings' boot-time fallback home screen
    private val NOT_LAUNCHERS = setOf(RESOLVER, "com.android.settings")

    /** The launcher: Android's [default] home app; with none chosen yet, the only real one among the [handlers] of
     *  the home intent (several installed and none chosen: none is left out). */
    fun pick(default: String?, handlers: Collection<String>): String? {
        if (default != null && default !in NOT_LAUNCHERS) return default
        return handlers.filter { it !in NOT_LAUNCHERS }.distinct().singleOrNull()
    }

    /** [pkg]'s time counts as app use ([home]: the launcher, null when not known). */
    fun counts(pkg: String, home: String?): Boolean = home == null || pkg != home

    /** A day of Lockdown's own history without the launcher's minutes (its switches already end at the home screen). */
    fun detail(d: DayDetail, home: String?): DayDetail = if (home == null) d else d.copy(rows = d.rows.filter { it.app != home })

    /** A day's totals without the launcher's seconds. */
    fun totals(t: DayTotals, home: String?): DayTotals = if (home == null) t else t.copy(seconds = t.seconds.filterKeys { it.first != home })
}
