package com.husarp.lockdown.remind

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import com.husarp.lockdown.R
import com.husarp.lockdown.data.Store
import java.util.Calendar
import java.util.concurrent.TimeUnit

private const val CHANNEL = "reminders"
private const val WORK = "lockdown-reminders"
private const val DIGEST = "lockdown-digest"

/** Schedules a 15-minute reminder check and a weekly screen-time digest. Coarse but battery-friendly. */
object Reminders {
    fun schedule(ctx: Context) {
        val wm = WorkManager.getInstance(ctx)
        wm.enqueueUniquePeriodicWork(
            WORK, ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<ReminderWorker>(15, TimeUnit.MINUTES).build(),
        )
        wm.enqueueUniquePeriodicWork(
            DIGEST, ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<DigestWorker>(7, TimeUnit.DAYS).build(),
        )
        wm.enqueueUniquePeriodicWork(
            "lockdown-protection", ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<ProtectionWorker>(7, TimeUnit.DAYS)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(androidx.work.NetworkType.UNMETERED).build())
                .build(),
        )
    }
}

/** Weekly refresh of the enabled protection lists (on unmetered Wi-Fi). */
class ProtectionWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        val p = Store.config.protection
        for (key in p.enabled) {
            runCatching { com.husarp.lockdown.block.Protection.download(applicationContext, key) }
        }
        com.husarp.lockdown.block.Protection.load(applicationContext)
        return Result.success()
    }
}

/** Once a week: a notification with the last 7 days' screen time (only if the digest toggle is on). */
class DigestWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        if (!Store.config.settings.weeklyDigest) return Result.success()
        val total = runCatching {
            com.husarp.lockdown.usage.Usage.range(
                applicationContext, com.husarp.lockdown.usage.Usage.lastDaysStart(7), System.currentTimeMillis(),
            ).totalMs
        }.getOrDefault(0)
        val nm = applicationContext.getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Reminders", NotificationManager.IMPORTANCE_DEFAULT))
        if (ActivityCompat.checkSelfPermission(applicationContext, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED)
            NotificationManagerCompat.from(applicationContext).notify(
                7, NotificationCompat.Builder(applicationContext, CHANNEL)
                    .setSmallIcon(R.drawable.ic_launcher_foreground)
                    .setContentTitle("Your week")
                    .setContentText("Screen time, last 7 days: " + com.husarp.lockdown.ui.fmtDuration(total))
                    .setAutoCancel(true).build(),
            )
        return Result.success()
    }
}

class ReminderWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        val cfg = Store.config
        val now = Calendar.getInstance()
        val mins = now.get(Calendar.HOUR_OF_DAY) * 60 + now.get(Calendar.MINUTE)

        // Bedtime nudges are driven by the accessibility service (fine escalation cadence), not here.
        val sleep = cfg.sleep
        if (cfg.breaks.on) notify(2, "Take a break", "Step away from the screen for a moment.")
        cfg.customs.filter { it.on && it.text.isNotBlank() }.forEachIndexed { i, c -> notify(100 + i, "Reminder", c.text) }

        // tamper watchdog: nudge if a protection that should be on has been switched off
        if (cfg.enabled && !com.husarp.lockdown.block.BlockService.isEnabled(applicationContext))
            notify(3, "App blocking is off", "Lockdown's accessibility service was turned off - open the app to turn it back on.")
        if (cfg.settings.siteFilterOn && !com.husarp.lockdown.vpn.LockdownVpn.active)
            notify(4, "Site filter stopped", "The site filter isn't running - open Lockdown to restart it.")

        // daily screen-time goal (once a day)
        val budget = cfg.settings.dailyGoalMin
        if (budget > 0) {
            val total = runCatching {
                com.husarp.lockdown.usage.Usage.range(applicationContext, com.husarp.lockdown.usage.Usage.dayBounds(0).first, System.currentTimeMillis()).totalMs
            }.getOrDefault(0)
            if (total >= budget * 60_000L) {
                val prefs = applicationContext.getSharedPreferences("goal", Context.MODE_PRIVATE)
                val today = java.time.LocalDate.now().toString()
                if (prefs.getString("day", "") != today) {
                    prefs.edit().putString("day", today).apply()
                    notify(5, "Daily screen-time goal reached", "You've used your ${budget}-minute goal for today.")
                }
            }
        }

        // clock-tamper warning
        if (com.husarp.lockdown.guard.TrustedTime.setbackMs(applicationContext) > 5 * 60_000)
            notify(6, "Clock changed", "The phone's clock was set back - Lockdown's timers ignore that.")

        // bedtime grayscale: drain the screen's colour during the sleep window (needs WRITE_SECURE_SETTINGS)
        if (cfg.settings.bedtimeGrayscale) {
            val bed = hhmm(sleep.bedtime); val wake = hhmm(sleep.wake)
            val night = if (bed <= wake) mins in bed until wake else mins >= bed || mins < wake
            runCatching {
                val cr = applicationContext.contentResolver
                if (night) android.provider.Settings.Secure.putInt(cr, "accessibility_display_daltonizer", 0) // 0 = grayscale
                android.provider.Settings.Secure.putInt(cr, "accessibility_display_daltonizer_enabled", if (night) 1 else 0)
            }
        }
        return Result.success()
    }

    private fun hhmm(s: String) = s.split(":").let { (it.getOrNull(0)?.toIntOrNull() ?: 0) * 60 + (it.getOrNull(1)?.toIntOrNull() ?: 0) }

    private fun notify(id: Int, title: String, text: String) {
        val nm = applicationContext.getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Reminders", NotificationManager.IMPORTANCE_DEFAULT))
        if (ActivityCompat.checkSelfPermission(applicationContext, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            return
        NotificationManagerCompat.from(applicationContext).notify(
            id,
            NotificationCompat.Builder(applicationContext, CHANNEL)
                .setSmallIcon(R.drawable.ic_launcher_foreground)
                .setContentTitle(title).setContentText(text)
                .setAutoCancel(true).build(),
        )
    }
}
