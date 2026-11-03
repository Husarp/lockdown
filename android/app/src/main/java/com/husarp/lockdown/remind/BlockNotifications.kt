package com.husarp.lockdown.remind

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import com.husarp.lockdown.MainActivity
import com.husarp.lockdown.R
import com.husarp.lockdown.block.Enforce
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Modes
import com.husarp.lockdown.engine.ModeTarget
import com.husarp.lockdown.engine.NotificationBatch
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.vpn.LockdownVpn
import java.time.LocalDateTime

/** A single replaceable blocking notice, shared with the VPN's required, quiet foreground notification. */
object BlockNotifications {
    private const val ID = 60
    private const val TAG = "alert"
    private val handler = Handler(Looper.getMainLooper())
    private val batch = NotificationBatch()
    private val waiting = ArrayList<Pair<String, Boolean>>()
    private var scheduled = false
    private var currentStatus: String? = null

    fun post(ctx: Context, text: String, urgent: Boolean = false) {
        val app = ctx.applicationContext
        handler.post {
            waiting.add(text to urgent)
            if (!scheduled) {
                scheduled = true
                handler.postDelayed({ runCatching { flush(app) } }, 750)
            }
        }
    }

    private fun flush(ctx: Context) {
        scheduled = false
        val cfg = Store.config
        val now = TrustedTime.local(ctx)
        val muted = ModesStore.current(now)?.mode?.mute == true
        val allowed = waiting.filter { !Enforce.silenced(cfg, now) && (!muted || it.second) }
        waiting.clear()
        val text = batch.merge(allowed.map { it.first }, SystemClock.elapsedRealtime()) ?: return
        val nm = ctx.getSystemService(NotificationManager::class.java)
        // Clean up old rotating slots even when the foreground notice now carries the details.
        for (old in 61..79) nm.cancel(TAG, old)
        if (LockdownVpn.active) {
            currentStatus = current(ctx)
            nm.cancel(TAG, ID)
            nm.notify(42, notification(ctx, "site_filter", text, foreground = true))
            return
        }
        if (Build.VERSION.SDK_INT >= 33 && ctx.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED) return
        val urgent = allowed.any { it.second }
        val channel = if (urgent) "alerts-now" else "alerts"
        if (nm.getNotificationChannel(channel) == null)
            nm.createNotificationChannel(NotificationChannel(channel,
                if (urgent) "Blocks on what you're using" else "Block warnings",
                if (urgent) NotificationManager.IMPORTANCE_HIGH else NotificationManager.IMPORTANCE_DEFAULT))
        nm.notify(TAG, ID, notification(ctx, channel, text))
    }

    /** When blocks end, do not leave the foreground notice claiming they are still blocked.
     * Keep warnings up while the actual block state stays the same. Called on the service's main-thread tick. */
    fun refresh(ctx: Context) {
        if (!LockdownVpn.active || scheduled) return
        val status = current(ctx)
        if (status == currentStatus) return
        currentStatus = status
        ctx.getSystemService(NotificationManager::class.java)
            .notify(42, notification(ctx, "site_filter", status, foreground = true))
    }

    fun notification(ctx: Context, channel: String, text: String = current(ctx), foreground: Boolean = false): Notification {
        val open = PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        return NotificationCompat.Builder(ctx, channel)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(if (foreground) "Lockdown site filter" else "Lockdown")
            .setContentText(text).setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(open).setOnlyAlertOnce(true)
            .setSilent(foreground).setOngoing(foreground).setAutoCancel(!foreground).build()
    }

    /** Populate the first foreground notice with actual blocks, including a mode started by bedtime. */
    fun current(ctx: Context): String = details(ctx).joinToString("\n").ifEmpty { "Site filter is on." }

    fun details(ctx: Context, now: LocalDateTime = TrustedTime.local(ctx)): List<String> {
        val cfg = Store.config
        val mode = ModesStore.current(now)
        val targets = cfg.items.map { ModeTarget(it.name, it.target, it.type, it.blockType, it.id) } +
            if (Modes.blocking(mode)) Modes.targets(mode!!.mode, cfg.items, cfg.groups, cfg.categories) else emptyList()
        return targets.distinctBy { it.type to it.target }.mapNotNull { item ->
            val v = when (item.type) {
                ItemType.APP -> Enforce.app(cfg, item.target, now, mode, item.name)
                ItemType.SITE -> Enforce.site(cfg, item.target.substringBefore(' '), now, mode)
            } ?: return@mapNotNull null
            if (v.grey) return@mapNotNull null
            "${item.name}: ${v.headline}. ${v.sub}"
        }
    }
}
