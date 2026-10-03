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

/**
 * Bedtime grayscale (Android's daltonizer, needs WRITE_SECURE_SETTINGS - granted once over adb, and lost when the
 * app is uninstalled and installed again). [sync] writes the wanted state both ways; on its own it only turns off
 * grayscale Lockdown itself turned on, never one the user set in Android's accessibility settings. Switching the
 * grayscale (or Bedtime) off by hand gives the colour back whoever turned it on ([switchedOff]).
 */
object Grayscale {
    const val GRANT = "adb shell pm grant com.husarp.lockdown android.permission.WRITE_SECURE_SETTINGS"
    private const val ENABLED = "accessibility_display_daltonizer_enabled"
    private const val MODE = "accessibility_display_daltonizer"     // 0 = grayscale
    private const val APPLIED = "applied"

    private fun prefs(ctx: Context) = ctx.getSharedPreferences("gray", Context.MODE_PRIVATE)

    /** Lockdown may change the screen's colour (the one-time adb grant is there). */
    fun canWrite(ctx: Context) =
        ctx.checkSelfPermission(Manifest.permission.WRITE_SECURE_SETTINGS) == PackageManager.PERMISSION_GRANTED

    /** This is a second copy in another profile (Island / work profile). The screen's colour setting belongs to the
     *  main profile, so this copy can never grey the screen or bring the colour back - only the main copy can. */
    fun otherProfile(ctx: Context): Boolean =
        runCatching { !ctx.getSystemService(android.os.UserManager::class.java).isSystemUser }.getOrDefault(false)

    /** Grayscale (not another colour correction) is on right now; null when Android won't say (newer Android
     *  can refuse apps reading hidden settings) - then Lockdown writes rather than guess it's already right. */
    fun isOn(ctx: Context): Boolean? = runCatching {
        val cr = ctx.contentResolver
        android.provider.Settings.Secure.getInt(cr, ENABLED, 0) == 1 && android.provider.Settings.Secure.getInt(cr, MODE, -1) == 0
    }.getOrNull()

    fun sync(ctx: Context) {
        if (otherProfile(ctx)) return               // not this copy's screen (and it never has the permission)
        val cfg = Store.config
        val now = Calendar.getInstance()
        val mins = now.get(Calendar.HOUR_OF_DAY) * 60 + now.get(Calendar.MINUTE)
        val paused = com.husarp.lockdown.block.Enforce.alertsPaused(cfg,
            com.husarp.lockdown.block.Enforce.ldt(com.husarp.lockdown.guard.TrustedTime.now(ctx)))
        val p = prefs(ctx)
        val wanted = !paused && com.husarp.lockdown.engine.bedtimeGrayscaleWanted(cfg.settings.bedtimeGrayscale, cfg.sleep, mins)
        val mark = if (p.contains(APPLIED)) p.getBoolean(APPLIED, false) else null
        val (write, newMark) = com.husarp.lockdown.engine.grayscaleStep(wanted, cfg.settings.bedtimeGrayscale, mark, isOn(ctx))
        // The mark only changes once the write went through: refused (no permission), it is tried again next minute.
        val ok = runCatching {
            val cr = ctx.contentResolver
            if (write == true) { android.provider.Settings.Secure.putInt(cr, MODE, 0); android.provider.Settings.Secure.putInt(cr, ENABLED, 1) }
            if (write == false) android.provider.Settings.Secure.putInt(cr, ENABLED, 0)
        }.isSuccess
        if (ok && newMark != null && newMark != mark) p.edit().putBoolean(APPLIED, newMark).apply()
    }

    /**
     * The grayscale switch (or Bedtime) was switched off by hand: colour back now, even if the mark that Lockdown
     * turned it on is missing. Only grayscale is touched, never another colour correction. False when Android
     * refused (the permission is missing) - then it says so.
     */
    fun switchedOff(ctx: Context): Boolean {
        if (otherProfile(ctx)) {
            prefs(ctx).edit().putBoolean(APPLIED, false).apply()
            android.widget.Toast.makeText(ctx, "This is Lockdown's copy in your work profile: it can't change the " +
                "screen's colour. Use Lockdown in your main profile.", android.widget.Toast.LENGTH_LONG).show()
            return false
        }
        if (isOn(ctx) == false) { prefs(ctx).edit().putBoolean(APPLIED, false).apply(); return true }
        val ok = runCatching { android.provider.Settings.Secure.putInt(ctx.contentResolver, ENABLED, 0) }.getOrDefault(false)
        // Refused: keep the mark, so the colour comes back by itself within a minute once the permission is granted.
        prefs(ctx).edit().putBoolean(APPLIED, !ok).apply()
        if (!ok) {
            android.widget.Toast.makeText(ctx, "Couldn't turn grayscale off: Lockdown's permission is missing. " +
                "Turn Colour correction off on the page that opens; Guardrails shows how to fix the permission.",
                android.widget.Toast.LENGTH_LONG).show()
            openColourSettings(ctx)
        }
        return ok
    }

    /** Android's own colour-correction page (where grayscale can be switched off by hand), else Accessibility. */
    fun openColourSettings(ctx: Context) {
        val flags = android.content.Intent.FLAG_ACTIVITY_NEW_TASK
        runCatching { ctx.startActivity(android.content.Intent("android.settings.ACCESSIBILITY_COLOR_SPACE_SETTINGS").addFlags(flags)) }
            .recoverCatching { ctx.startActivity(android.content.Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(flags)) }
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

        // Bedtime nudges are driven by the accessibility service (fine escalation cadence), not here.
        val sleep = cfg.sleep
        val paused = com.husarp.lockdown.block.Enforce.alertsPaused(cfg,
            com.husarp.lockdown.block.Enforce.ldt(com.husarp.lockdown.guard.TrustedTime.now(applicationContext)))
        if (cfg.breaks.on && !paused) notify(2, "Take a break", "Step away from the screen for a moment.")
        cfg.customs.filter { it.on && it.text.isNotBlank() }.forEachIndexed { i, c -> notify(100 + i, "Reminder", c.text) }

        // tamper watchdog: nudge if a protection that should be on has been switched off
        if (cfg.enabled && !com.husarp.lockdown.block.BlockService.isEnabled(applicationContext))
            notify(3, "App blocking is off", "Lockdown's accessibility service was turned off - open the app to turn it back on.")
        if (cfg.settings.siteFilterOn && !com.husarp.lockdown.vpn.LockdownVpn.active)
            notify(4, "Site filter stopped", "The site filter isn't running - open Lockdown to restart it.")
        val link = com.husarp.lockdown.link.IslandLink
        if (cfg.enabled && link.mainState.value.state == "linked" && !link.healthy(applicationContext))
            notify(8, "Island apps not covered", "Lockdown in Island isn't checking in or can't block - open it there.")

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
        Grayscale.sync(applicationContext)
        return Result.success()
    }

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
