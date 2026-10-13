package com.husarp.lockdown.data

import android.content.Context
import com.husarp.lockdown.engine.ActivityLog
import com.husarp.lockdown.engine.Bypass
import com.husarp.lockdown.guard.TrustedTime

/** A small record of the ways round the blocks that nothing else keeps - a pause started, a challenge passed,
 *  Lockdown switched off - for the Activity log's "Bypass used" chip. Emergency uses are in the config already.
 *  One short line each in prefs, the newest [ActivityLog.BYPASS_KEEP] kept (engine/ActivityLog.kt). */
object BypassLog {
    private const val PREFS = "bypass_log"
    private const val KEY = "lines"

    private fun prefs(ctx: Context) = ctx.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** Notes [kind] now (trusted time); [seconds] is how long it frees things (0 for none). Never throws. */
    @Synchronized fun record(ctx: Context, kind: Bypass, seconds: Int = 0) {
        runCatching {
            val p = prefs(ctx)
            p.edit().putString(KEY, ActivityLog.bypassAdd(p.getString(KEY, "").orEmpty(), TrustedTime.local(ctx), kind, seconds)).apply()
        }
    }

    fun read(ctx: Context): String = runCatching { prefs(ctx).getString(KEY, "").orEmpty() }.getOrDefault("")
}
