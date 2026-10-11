package com.husarp.lockdown.block

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.SystemClock
import android.provider.AlarmClock
import android.provider.Settings
import com.husarp.lockdown.engine.BreakAllow

/**
 * The packages a strict break always leaves usable on THIS phone (see [BreakAllow]): resolved from intents, so a
 * vendor's own launcher / settings / clock / installer is found without naming it. Worked out once and kept for
 * [TTL_MS] (or until a package is added, removed or changed): a break asks on every window change.
 */
object BreakApps {
    private const val TTL_MS = 5 * 60_000L
    private var cached: Set<String> = emptySet()
    private var at = 0L
    private var valid = false

    /** A package was installed / removed / changed, or the default apps may have: look again next time. */
    @Synchronized fun invalidate() { valid = false }

    @Synchronized fun builtIn(ctx: Context): Set<String> {
        val now = SystemClock.elapsedRealtime()
        if (!valid || now - at >= TTL_MS) {
            cached = runCatching { resolve(ctx) }.getOrDefault(BreakAllow.KNOWN + ctx.packageName)
            at = now; valid = true
        }
        return cached
    }

    private fun resolve(ctx: Context): Set<String> {
        val pm = ctx.packageManager
        val out = HashSet<String>(BreakAllow.KNOWN)
        out.add(ctx.packageName)
        fun handlers(i: Intent) {
            runCatching { pm.queryIntentActivities(i, 0).mapNotNullTo(out) { it.activityInfo?.packageName } }
        }
        // the launcher / home screen (all of them: the default one can change)
        handlers(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME))
        // Android Settings
        handlers(Intent(Settings.ACTION_SETTINGS))
        // the dialer
        runCatching {
            val tm = ctx.getSystemService(Context.TELECOM_SERVICE) as android.telecom.TelecomManager
            tm.defaultDialerPackage?.let { out.add(it) }
            if (Build.VERSION.SDK_INT >= 29) tm.systemDialerPackage?.let { out.add(it) }
        }
        // the clock: a ringing alarm or timer must be stoppable
        handlers(Intent(AlarmClock.ACTION_SHOW_ALARMS))
        handlers(Intent(AlarmClock.ACTION_SHOW_TIMERS))
        // the permission controller (Android hides its name from apps: asked by reflection, then by what handles its screens)
        runCatching {
            val m = pm.javaClass.getMethod("getPermissionControllerPackageName")
            (m.invoke(pm) as? String)?.let { out.add(it) }
        }
        handlers(Intent("android.intent.action.MANAGE_PERMISSIONS"))
        handlers(Intent("android.intent.action.MANAGE_APP_PERMISSIONS"))
        // the package installer
        handlers(Intent(Intent.ACTION_INSTALL_PACKAGE).setDataAndType(Uri.parse("content://x/app.apk"), APK))
        handlers(Intent(Intent.ACTION_VIEW).setDataAndType(Uri.parse("content://x/app.apk"), APK))
        // the emergency / SOS and emergency-information apps
        handlers(Intent("android.telephony.action.EMERGENCY_ASSISTANCE"))
        handlers(Intent("android.settings.EDIT_EMERGENCY_INFO"))
        // the keyboard in use
        BreakAllow.keyboardPackage(Settings.Secure.getString(ctx.contentResolver, Settings.Secure.DEFAULT_INPUT_METHOD))?.let { out.add(it) }
        return out
    }

    private const val APK = "application/vnd.android.package-archive"
}
