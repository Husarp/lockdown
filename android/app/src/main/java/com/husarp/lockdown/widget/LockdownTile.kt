package com.husarp.lockdown.widget

import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import com.husarp.lockdown.data.Store

/** Quick Settings tile: shows and toggles Lockdown's master switch. */
class LockdownTile : TileService() {
    override fun onStartListening() = refresh()

    override fun onClick() {
        Store.update { it.copy(enabled = !it.enabled) }
        refresh()
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
