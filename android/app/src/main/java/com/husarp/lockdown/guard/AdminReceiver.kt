package com.husarp.lockdown.guard

import android.app.admin.DeviceAdminReceiver
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent

/**
 * Device-admin lets Android refuse to uninstall the app until admin is turned off - the friction that
 * stops an impulsive uninstall. It grants no other power here; we only use it as an uninstall lock.
 */
class AdminReceiver : DeviceAdminReceiver() {
    // Shown on the system screen when someone tries to turn admin off (the last nudge before uninstall).
    override fun onDisableRequested(context: Context, intent: Intent): CharSequence =
        "Turning this off removes Lockdown's uninstall protection. If you're trying to quit on a whim, " +
            "give it a minute first."
}

object Admin {
    fun component(ctx: Context) = ComponentName(ctx, AdminReceiver::class.java)

    fun isActive(ctx: Context): Boolean =
        dpm(ctx).isAdminActive(component(ctx))

    /** Intent that opens the system "activate device admin?" screen. */
    fun activateIntent(ctx: Context): Intent =
        Intent(DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN)
            .putExtra(DevicePolicyManager.EXTRA_DEVICE_ADMIN, component(ctx))
            .putExtra(
                DevicePolicyManager.EXTRA_ADD_EXPLANATION,
                "Lets Lockdown block its own uninstall, so you can't remove it on impulse.",
            )

    fun deactivate(ctx: Context) = runCatching { dpm(ctx).removeActiveAdmin(component(ctx)) }

    private fun dpm(ctx: Context) =
        ctx.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
}
