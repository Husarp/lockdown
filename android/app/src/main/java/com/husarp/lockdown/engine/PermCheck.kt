package com.husarp.lockdown.engine

/** A permission row: on, allowed in Android but not running, or not allowed. */
enum class Perm { ON, NOT_RUNNING, OFF }

/**
 * The pure parts of the permission checks (the Android reads are in guard/Perms.kt), unit-tested in PermCheckTest.
 * Every check is read fresh where it's shown, never kept from before.
 */
object PermCheck {
    private const val MODE_ALLOWED = 0   // AppOpsManager.MODE_ALLOWED
    private const val MODE_DEFAULT = 3   // AppOpsManager.MODE_DEFAULT

    /** Android's list of turned-on accessibility services ("pkg/cls:pkg/cls") has [pkg]/[cls]. The class can be
     *  written in full ("com.x/com.x.Svc") or short (".Svc", after the package) - Settings, adb and ROMs differ. */
    fun a11yListed(flat: String?, pkg: String, cls: String): Boolean =
        flat.orEmpty().split(':').any { entry ->
            val p = entry.substringBefore('/', "").trim()
            val c = entry.substringAfter('/', "").trim()
            p.equals(pkg, ignoreCase = true) && (if (c.startsWith('.')) p + c else c).equals(cls, ignoreCase = true)
        }

    /** App blocking: running (Android bound the service) is what counts; turned on in Settings but not bound is
     *  "allowed but not running" (it crashed, or Android hasn't started it). */
    fun a11y(listed: Boolean, running: Boolean): Perm = when {
        running -> Perm.ON
        listed -> Perm.NOT_RUNNING
        else -> Perm.OFF
    }

    /** Usage access: the app-op allowed, or left at its default with the permission itself granted (adb pm grant). */
    fun usageAllowed(mode: Int, permissionGranted: Boolean): Boolean =
        mode == MODE_ALLOWED || (mode == MODE_DEFAULT && permissionGranted)

    /** Notifications: the Android 13+ permission and the app's notifications on. */
    fun notificationsOn(sdk: Int, permissionGranted: Boolean, appEnabled: Boolean): Boolean =
        (sdk < 33 || permissionGranted) && appEnabled

    /** The site filter you turned on: running, or not running (Start). Null while it's off. Android's VPN
     *  permission isn't checked: its only check (VpnService.prepare) takes the VPN over from another app. */
    fun vpn(wanted: Boolean, running: Boolean): Perm? = when {
        !wanted -> null
        running -> Perm.ON
        else -> Perm.NOT_RUNNING
    }
}
