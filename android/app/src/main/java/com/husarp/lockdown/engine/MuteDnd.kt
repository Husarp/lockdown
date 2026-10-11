package com.husarp.lockdown.engine

/**
 * "Mute notifications" on a mode turns Do Not Disturb on while the mode runs and puts the user's own setting back
 * after. Pure decision (the service does the Android calls). Filters use Android's INTERRUPTION_FILTER_* numbers.
 *
 * What we remember ([Saved]) survives a restart: [prev] = the user's filter before, [set] = what we set, [yielded] =
 * the user changed Do Not Disturb themselves while the mode ran (we leave it alone until the mode ends).
 * Rules: restore only what we changed and only if it still is what we set; Do Not Disturb already on before the mode
 * is left as it is (and not turned off after); losing access forgets everything.
 */
object MuteDnd {
    const val ALL = 1            // INTERRUPTION_FILTER_ALL = Do Not Disturb off
    const val UNKNOWN = 0        // INTERRUPTION_FILTER_UNKNOWN = couldn't be read: says nothing about the user
    const val ALARMS = 4         // INTERRUPTION_FILTER_ALARMS = what the mode sets: only alarms get through

    data class Saved(val prev: Int, val set: Int, val yielded: Boolean = false)

    /** [filter]: set it to this; null = leave it. [saved]: what to remember from now on (null = nothing). */
    data class Step(val filter: Int?, val saved: Saved?)

    fun decide(wantMute: Boolean, access: Boolean, current: Int, saved: Saved?): Step {
        if (!access) return Step(null, null)
        if (saved == null) {
            // already on (or unknown): the user's, leave it. Off: turn it on and remember what to put back.
            return if (wantMute && current == ALL) Step(ALARMS, Saved(current, ALARMS)) else Step(null, null)
        }
        if (current == UNKNOWN) return Step(null, saved)      // an unreadable value is not the user changing it
        if (saved.yielded) return Step(null, if (wantMute) saved else null)
        if (current != saved.set) return Step(null, if (wantMute) saved.copy(yielded = true) else null)   // the user's choice now
        return if (wantMute) Step(null, saved) else Step(saved.prev, null)
    }

    fun encode(s: Saved?): String = if (s == null) "" else "${s.prev},${s.set},${if (s.yielded) 1 else 0}"

    fun decode(t: String?): Saved? {
        val p = t?.split(",") ?: return null
        if (p.size != 3) return null
        return Saved(p[0].toIntOrNull() ?: return null, p[1].toIntOrNull() ?: return null, p[2] == "1")
    }
}
