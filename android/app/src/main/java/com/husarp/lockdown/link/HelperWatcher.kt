package com.husarp.lockdown.link

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import com.husarp.lockdown.MainActivity
import com.husarp.lockdown.R
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.HelperWatch

/**
 * Main's side of "the Island helper was stopped" (engine/HelperWatch.kt decides; this reads the phone and acts).
 * When a linked helper has been quiet for 2 minutes of screen time it is a tamper event, handled like the other
 * guardrail watches (the 15-minute worker's "App blocking is off" notices): a notification that cannot be
 * missed - high importance, ongoing, put up again every 5 minutes while the screen is on - plus a count that
 * Home's Island card and Guardrails show. The setting is Guardrails > Switches > "Alert when Island stops"
 * (on by default; turning it off is a loosening, behind the challenge). The notice is never silenced by a pause.
 *
 * Android cannot stop this from happening: "Stop" in the foreground-service list or Force stop ends the helper
 * and nothing in main can restart a process in another profile. Seeing it fast and refusing to let it pass quietly
 * is what main can do.
 */
object HelperWatcher {
    private const val CHANNEL = "island-helper"
    private const val TAG = "helper-stopped"
    private const val ID = 9

    private lateinit var app: Context
    private var st = HelperWatch.State()
    private var graceUntil = 0L                // elapsed ms: no new silence before this (start-up, Island coming up)
    private var wasPaused = false
    @Volatile private var open = false          // an episode is open (read without the lock on every check-in)

    private fun prefs() = app.getSharedPreferences("island_watch", Context.MODE_PRIVATE)

    /** How many times the helper went quiet, and when it last did (wall ms, 0: never). Kept apart from the link's
     *  own prefs, so unlinking and linking again doesn't wipe the record. */
    data class Record(val count: Int, val lastAt: Long)

    fun record(ctx: Context): Record {
        val p = ctx.applicationContext.getSharedPreferences("island_watch", Context.MODE_PRIVATE)
        return Record(p.getInt("count", 0), p.getLong("last", 0))
    }

    /** "Went quiet twice · last 14:03", or null when it never has. */
    fun summary(ctx: Context): String? {
        val r = record(ctx)
        if (r.count == 0) return null
        val t = java.time.Instant.ofEpochMilli(r.lastAt).atZone(java.time.ZoneId.systemDefault())
        return "Lockdown in Island went quiet ${HelperWatch.times(r.count)} · last %02d:%02d".format(t.hour, t.minute)
    }

    @Synchronized fun init(ctx: Context) {
        app = ctx.applicationContext
        // An episode open when the process died is still open: it is not counted again, and the alert comes back.
        open = prefs().getLong("open", 0) > 0
        st = if (open) HelperWatch.resumed(IslandLink.heardAt) else HelperWatch.State(heard = IslandLink.heardAt)
        graceUntil = SystemClock.elapsedRealtime() + HelperWatch.GRACE_MS      // a fresh process: the helper may still be starting
        wasPaused = false
    }

    /** One look (main's service, once a minute with the screen on, and whenever the screen changes). */
    @Synchronized fun look(ctx: Context, screenOn: Boolean) {
        if (!::app.isInitialized) init(ctx)
        val cfg = Store.config
        val now = SystemClock.elapsedRealtime()
        val paused = IslandLink.profilePaused(app)        // switched off, or locked after a reboot: nothing can run there
        if (wasPaused && !paused) graceUntil = now + HelperWatch.GRACE_MS      // Island just came up: its helper needs a moment
        wasPaused = paused
        val watching = HelperWatch.watchedNow(
            cfg.enabled && cfg.guardrails.helperAlert && IslandLink.role(app) == Role.MAIN &&
                IslandLink.mainState.value.state == "linked" && !paused,
            st.silent, now, graceUntil)
        val out = HelperWatch.look(st, now, IslandLink.heardAt, screenOn, watching)
        st = out.state
        when (out.act) {
            HelperWatch.Act.RAISE -> {
                val r = record(app)
                prefs().edit().putInt("count", r.count + 1).putLong("last", System.currentTimeMillis())
                    .putLong("open", System.currentTimeMillis()).apply()
                open = true
                post(app, loud = true)
            }
            HelperWatch.Act.REPEAT -> post(app, loud = true)
            HelperWatch.Act.CLEAR -> close()
            HelperWatch.Act.NONE -> {}
        }
    }

    /** The helper checked in (the link server, any thread): an open episode ends now, not at the next look. */
    fun heard() {
        if (!open) return
        synchronized(this) {
            if (!open) return
            st = HelperWatch.State(heard = IslandLink.heardAt)
            close()
        }
    }

    private fun close() {
        open = false
        prefs().edit().remove("open").apply()
        runCatching { app.getSystemService(NotificationManager::class.java).cancel(TAG, ID) }
    }

    /** The 15-minute worker: while an episode is open the alert is put up again (quietly when the screen is off), so
     *  it is still there if the service that normally repeats it isn't running. False when none is open. [wanted] is
     *  false when blocking or the switch is off: an open alert is then taken down here too (the service that would
     *  do it on its next look may not be running). */
    @Synchronized fun repost(ctx: Context, wanted: Boolean = true): Boolean {
        if (!::app.isInitialized) init(ctx)
        if (!open) return false
        if (!wanted) { st = HelperWatch.State(heard = IslandLink.heardAt); close(); return false }
        if (IslandLink.mainState.value.state != "linked") { st = HelperWatch.State(heard = IslandLink.heardAt); close(); return false }   // unlinked meanwhile
        val on = runCatching { app.getSystemService(android.os.PowerManager::class.java).isInteractive }.getOrDefault(false)
        post(app, loud = on)
        return true
    }

    private fun post(ctx: Context, loud: Boolean) {
        if (android.os.Build.VERSION.SDK_INT >= 33 &&
            ctx.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        val nm = ctx.getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Island helper stopped", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Tells you when Lockdown in Island has stopped, so its apps are not covered."
            })
        val n = record(ctx).count
        val text = "Your Island apps aren't covered right now. Open Lockdown in Island to start it again." +
            if (n > 1) " It has gone quiet ${HelperWatch.times(n)}." else ""
        val tap = PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        nm.notify(TAG, ID, NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("Lockdown in Island stopped")
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setContentIntent(tap)
            .setOngoing(true)
            .setSilent(!loud)
            .build())
    }
}
