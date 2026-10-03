package com.husarp.lockdown.widget

import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.Intent
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import com.husarp.lockdown.MainActivity
import com.husarp.lockdown.data.Store

/** Quick Settings tile: shows Lockdown's master switch. Turning on is instant; turning off opens the app's challenge. */
class LockdownTile : TileService() {
    override fun onStartListening() = refresh()

    override fun onClick() {
        if (com.husarp.lockdown.link.IslandLink.isHelper) { refresh(); return }   // Island helper: main holds the switch
        if (!Store.config.enabled) { Store.update { it.copy(enabled = true) }; refresh(); return }
        // Turning off is a loosening: hand it to the app, which asks for the challenge (Anti-Bypass) first.
        // Its own action: the notification's and widget's PendingIntents to MainActivity must never carry turn_off.
        val i = Intent(this, MainActivity::class.java).setAction(MainActivity.ACTION_TURN_OFF).putExtra(MainActivity.EXTRA_TURN_OFF, true)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        if (Build.VERSION.SDK_INT >= 34)
            startActivityAndCollapse(PendingIntent.getActivity(this, 0, i, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
        else @SuppressLint("StartActivityAndCollapseDeprecated") @Suppress("DEPRECATION") startActivityAndCollapse(i)
    }

    private fun refresh() {
        val on = Store.config.enabled
        qsTile?.apply {
            state = if (on) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
            label = "Lockdown"
            subtitle = if (on) "On" else "Off"
            updateTile()
        }
    }
}
