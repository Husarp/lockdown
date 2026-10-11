package com.husarp.lockdown.link

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.app.NotificationCompat
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import com.husarp.lockdown.MainActivity
import com.husarp.lockdown.R
import com.husarp.lockdown.engine.Cadence
import com.husarp.lockdown.engine.HelperKeep
import java.util.concurrent.TimeUnit

/**
 * The Island helper's way back after its service was stopped. What Android allows:
 *  - "Stop" in the foreground-service list (Android 13+) ends the whole app process, with no callback and no
 *    START_STICKY restart, but leaves its scheduled jobs: the 15-minute watchdog below runs, starts the process, and
 *    tries to start the service again.
 *  - A foreground service may only be started from the background in a few cases (Android 12+): after
 *    BOOT_COMPLETED / MY_PACKAGE_REPLACED (the boot receiver - always works), and when the user has turned off battery
 *    optimisation for the app (Lockdown asks for it: "Battery not restricted"). The watchdog is not exempt on its own,
 *    so with that setting off the start throws and a notification takes its place: tapping it is the user acting on a
 *    notification, which Android allows to start the service.
 *  - Force stop ends the app and Android then delivers it nothing - no job, no alarm, no boot receiver - until the
 *    user opens it. Nothing here can change that; main notices the silence instead ([HelperWatcher]).
 *  - ACTION_USER_PRESENT can't be a manifest receiver (Android 8+), so a dead process never sees it; the running
 *    service already listens to it.
 */
object HelperWatchdog {
    private const val WORK = "lockdown-helper-watchdog"
    private const val CHANNEL = "link-stopped"
    private const val NUDGE_ID = 44

    /** Every 15 minutes (WorkManager's shortest period): one wake-up that checks a flag. Kept if already queued. */
    fun schedule(ctx: Context) {
        WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(
            WORK, ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<HelperWatchdogWorker>(Cadence.HELPER_WATCHDOG_MIN, TimeUnit.MINUTES).build(),
        )
    }

    fun cancel(ctx: Context) {
        WorkManager.getInstance(ctx).cancelUniqueWork(WORK)
        clearNudge(ctx)
    }

    /** Start the helper's service. Android refusing it (a background start that isn't allowed) is not an error: the
     *  tap-to-restart notice goes up instead. True when Android took the start. */
    fun startOrNudge(ctx: Context): Boolean {
        val ok = runCatching { LinkService.start(ctx) }.isSuccess
        if (!ok) nudge(ctx)
        return ok
    }

    /** "Lockdown in Island stopped - tap to start it again." Quiet after the first time (it is the same notice). */
    fun nudge(ctx: Context) {
        if (android.os.Build.VERSION.SDK_INT >= 33 &&
            ctx.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        runCatching {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            if (nm.getNotificationChannel(CHANNEL) == null)
                nm.createNotificationChannel(NotificationChannel(CHANNEL, "Lockdown in Island stopped", NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "Tap to start Lockdown in Island again, so its apps are covered."
                })
            val tap = PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            nm.notify(NUDGE_ID, NotificationCompat.Builder(ctx, CHANNEL)
                .setSmallIcon(R.drawable.ic_launcher_foreground)
                .setContentTitle("Lockdown in Island stopped")
                .setContentText("Your Island apps aren't covered. Tap to start it again.")
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_STATUS)
                .setContentIntent(tap)
                .setOnlyAlertOnce(true)
                .setOngoing(true)
                .build())
        }
    }

    fun clearNudge(ctx: Context) {
        runCatching { ctx.getSystemService(NotificationManager::class.java).cancel(NUDGE_ID) }
    }
}

/** The helper's 15-minute watchdog: the service isn't running in a linked helper, so start it. */
class HelperWatchdogWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        // (this process was just started if the service had been stopped: Application.onCreate has already tried once)
        if (HelperKeep.shouldStart(IslandLink.isHelper, LinkService.alive)) HelperWatchdog.startOrNudge(applicationContext)
        return Result.success()
    }
}
