package com.husarp.lockdown.guard

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.vpn.LockdownVpn

/** Bring the site filter back after a reboot, so protection survives a restart. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        Store.init(context)
        val cfg = Store.config
        if (cfg.settings.siteFilterOn && cfg.guardrails.persist && LockdownVpn.prepare(context) == null) {
            LockdownVpn.start(context)
        }
    }
}
