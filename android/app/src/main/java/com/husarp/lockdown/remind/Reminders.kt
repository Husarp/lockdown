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
import java.util.concurrent.TimeUnit

private const val CHANNEL = "reminders"
private const val WORK = "lockdown-reminders"
private const val DIGEST = "lockdown-digest"
private const val ISLAND_ID = 8      // "Island apps not covered"

/** Schedules a 15-minute check (tamper watchdog, daily goal, clock) and a weekly screen-time digest. */
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

    /** Failed Island checks in a row ([com.husarp.lockdown.engine.IslandSight.noticeStep]); kept across processes. */
    internal fun islandPrefs(ctx: Context) = ctx.getSharedPreferences("island_notice", Context.MODE_PRIVATE)

    /** A failed Island check is counted (the "not covered" notice may be up). */
    fun islandFailing(ctx: Context) = islandPrefs(ctx).getInt("fails", 0) > 0

    /** Lockdown in Island checked in and Island is covered: the count starts again and the notice goes now. */
    fun islandBack(ctx: Context) {
        islandPrefs(ctx).edit().putInt("fails", 0).apply()
        runCatching { NotificationManagerCompat.from(ctx).cancel(ISLAND_ID) }
    }
}

/**
 * Bedtime grayscale (Android's daltonizer, needs WRITE_SECURE_SETTINGS - granted once over adb, and lost when the
 * app is uninstalled and installed again). [sync] writes the wanted state both ways; on its own it only turns off
 * grayscale Lockdown itself turned on, never one the user set in Android's accessibility settings. Switching the
 * grayscale (or Bedtime) off by hand gives the colour back whoever turned it on ([switchedOff]). Giving the colour
 * back puts the colour correction as it was before bedtime ([com.husarp.lockdown.engine.grayscaleRestore]). Checked every minute and on
 * unlock (the service), every 15 min (the worker) and whenever the app opens.
 * "Grey this app" uses the same: grey while an app whose block greys it is in front ([app]), and grey while bedtime
 * OR that app wants it - the two never fight; the colour comes back (as it was) once neither does.
 */
object Grayscale {
    const val GRANT = "adb shell pm grant com.husarp.lockdown android.permission.WRITE_SECURE_SETTINGS"
    private const val ENABLED = "accessibility_display_daltonizer_enabled"
    private const val MODE = "accessibility_display_daltonizer"     // 0 = grayscale
    private const val APPLIED = "applied"
    private const val PREV_ENABLED = "prev_enabled"   // the colour correction from before Lockdown took the screen over
    private const val PREV_MODE = "prev_mode"
    private const val PREV_OWN = "prev_own"           // that was grayscale the user turned on after Lockdown's last colour-back

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

    private fun mark(p: android.content.SharedPreferences) = if (p.contains(APPLIED)) p.getBoolean(APPLIED, false) else null

    /** Grayscale should be on now: bedtime grayscale (in trusted time, and not while an emergency or a silent pause
     *  holds the alerts), or an app that greys is in front ([app]). */
    private fun wanted(ctx: Context): Boolean {
        if (appGrey) return true
        val cfg = Store.config
        val now = com.husarp.lockdown.block.Enforce.ldt(com.husarp.lockdown.guard.TrustedTime.now(ctx))   // trusted time
        val paused = com.husarp.lockdown.block.Enforce.alertsPaused(cfg, now) || com.husarp.lockdown.block.Enforce.silenced(cfg, now)
        return !paused && com.husarp.lockdown.engine.bedtimeGrayscaleWanted(cfg.settings.bedtimeGrayscale, cfg.sleep, now.hour * 60 + now.minute)
    }

    @Volatile private var appGrey = false

    /** "Grey this app" (the service, on every step): [wants] = an app whose block greys it is in front. Grey now, and
     *  checked again on every step while it stays (colour correction switched off by hand comes back grey); colour
     *  back, as it was before, once it's gone - unless bedtime grayscale wants it too. */
    fun app(ctx: Context, wants: Boolean) {
        if (!wants && !appGrey) return
        appGrey = wants
        sync(ctx)
    }

    // (synchronized: the 15-minute worker runs it off the main thread)
    @Synchronized fun sync(ctx: Context) {
        if (otherProfile(ctx)) return               // not this copy's screen (and it never has the permission)
        val p = prefs(ctx)
        val mark = mark(p)
        val on = isOn(ctx)
        val (write, newMark) = com.husarp.lockdown.engine.grayscaleStep(wanted(ctx), Store.config.settings.bedtimeGrayscale, mark, on)
        // Taking the screen over: keep its colour correction as it is now, to put back at wake time. Grey already on
        // after Lockdown's last colour-back (mark false) is the user's own.
        if (newMark == true && mark != true) keepPrev(ctx, ownGrey = mark == false && on == true)
        // The mark only changes once Android took the write (and the screen shows it): refused, it is tried again next minute.
        val ok = when (write) {
            true -> runCatching {
                val cr = ctx.contentResolver
                android.provider.Settings.Secure.putInt(cr, MODE, com.husarp.lockdown.engine.DALTONIZER_GRAYSCALE) &&
                    android.provider.Settings.Secure.putInt(cr, ENABLED, 1)
            }.getOrDefault(false) && isOn(ctx) != false
            false -> restore(ctx)
            null -> true
        }
        if (ok && newMark != null && newMark != mark) p.edit().putBoolean(APPLIED, newMark).apply()
    }

    private fun keepPrev(ctx: Context, ownGrey: Boolean) {
        val cr = ctx.contentResolver
        val enabled = runCatching { android.provider.Settings.Secure.getInt(cr, ENABLED, 0) }.getOrNull()
        val mode = runCatching { android.provider.Settings.Secure.getString(cr, MODE)?.toIntOrNull() }.getOrNull()
        prefs(ctx).edit().apply {
            if (enabled != null) putInt(PREV_ENABLED, enabled) else remove(PREV_ENABLED)
            if (mode != null) putInt(PREV_MODE, mode) else remove(PREV_MODE)
            putBoolean(PREV_OWN, ownGrey)
        }.apply()
    }

    /** Colour back, with the colour correction as it was before ([com.husarp.lockdown.engine.grayscaleRestore]). True once Android took it and
     *  the screen shows it (not grey any more, or grey again when that was the user's own). */
    private fun restore(ctx: Context): Boolean {
        val p = prefs(ctx)
        val (enabled, mode) = com.husarp.lockdown.engine.grayscaleRestore(
            if (p.contains(PREV_ENABLED)) p.getInt(PREV_ENABLED, 0) else null,
            if (p.contains(PREV_MODE)) p.getInt(PREV_MODE, 0) else null,
            p.getBoolean(PREV_OWN, false),
        )
        val grey = enabled == 1 && mode == com.husarp.lockdown.engine.DALTONIZER_GRAYSCALE
        val cr = ctx.contentResolver
        val ok = runCatching {
            // grayscale off first; the user's own correction gets its mode back before it's switched on
            if (enabled == 0) android.provider.Settings.Secure.putInt(cr, ENABLED, 0) && android.provider.Settings.Secure.putInt(cr, MODE, mode)
            else android.provider.Settings.Secure.putInt(cr, MODE, mode) && android.provider.Settings.Secure.putInt(cr, ENABLED, 1)
        }.getOrDefault(false) && isOn(ctx) != !grey
        if (ok) p.edit().remove(PREV_ENABLED).remove(PREV_MODE).remove(PREV_OWN).apply()
        return ok
    }

    /**
     * The grayscale switch (or Bedtime) was switched off by hand: colour back now, even if the mark that Lockdown
     * turned it on is missing. Only grayscale is touched, never another colour correction (one from before bedtime
     * comes back, also grayscale the user had on themselves). False when Android refused (the permission is missing) - then it says so.
     */
    @Synchronized fun switchedOff(ctx: Context): Boolean {
        if (otherProfile(ctx)) {
            prefs(ctx).edit().putBoolean(APPLIED, false).apply()
            android.widget.Toast.makeText(ctx, "This is Lockdown's copy in your work profile: it can't change the " +
                "screen's colour. Use Lockdown in your main profile.", android.widget.Toast.LENGTH_LONG).show()
            return false
        }
        val p = prefs(ctx)
        // colour already back: nothing to do, unless it was ours and Lockdown can still put the mode back
        if (isOn(ctx) == false && (mark(p) != true || !canWrite(ctx))) { p.edit().putBoolean(APPLIED, false).apply(); return true }
        val ok = restore(ctx)
        // Refused: keep the mark, so the colour comes back by itself within a minute once the permission is granted.
        p.edit().putBoolean(APPLIED, !ok).apply()
        if (!ok) {
            android.widget.Toast.makeText(ctx, "Couldn't turn grayscale off: Lockdown's permission is missing. " +
                "Turn Colour correction off on the page that opens; Guardrails shows how to fix the permission.",
                android.widget.Toast.LENGTH_LONG).show()
            openColourSettings(ctx)
        }
        return ok
    }

    /** The screen is grey outside bedtime and it may be Lockdown's doing ([com.husarp.lockdown.engine.grayscaleStuck]). */
    fun stuck(ctx: Context): Boolean = !otherProfile(ctx) &&
        com.husarp.lockdown.engine.grayscaleStuck(wanted(ctx), Store.config.settings.bedtimeGrayscale, mark(prefs(ctx)), isOn(ctx))

    /** "Turn grayscale off now": colour back, whoever turned it on - but never during bedtime (then it stays). */
    fun turnOffNow(ctx: Context) { if (wanted(ctx)) sync(ctx) else switchedOff(ctx) }

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
            if (isStopped) return Result.success()
            runCatching { com.husarp.lockdown.block.Protection.download(applicationContext, key) }
        }
        runCatching { com.husarp.lockdown.block.Protection.dropStale(applicationContext, p.enabled) }
        com.husarp.lockdown.block.Protection.load(applicationContext)
        return Result.success()
    }
}

/** Once a week: a notification with the last 7 days' screen time (only if the digest toggle is on). */
class DigestWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        if (!Store.config.settings.weeklyDigest) return Result.success()
        val main = runCatching {
            com.husarp.lockdown.usage.Usage.range(
                applicationContext, com.husarp.lockdown.usage.Usage.lastDaysStart(applicationContext, 7), System.currentTimeMillis(),
            ) { isStopped }.totalMs
        }.getOrDefault(0)
        if (isStopped) return Result.success()   // stopped by Android: no half-counted week
        // Island apps count too, day by day (as Home's total)
        val today = com.husarp.lockdown.guard.TrustedTime.local(applicationContext).toLocalDate()
        val week = (0L..6L).map { today.minusDays(it) }
        val total = runCatching { com.husarp.lockdown.link.IslandLink.withIsland(applicationContext, main, week) }.getOrDefault(main)
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

        // Bedtime, breaks and your own reminders run in the accessibility service (ReminderRunner), on their settings.

        // tamper watchdog: nudge if a protection that should be on has been switched off
        if (cfg.enabled && !com.husarp.lockdown.block.BlockService.isEnabled(applicationContext))
            notify(3, "App blocking is off", "Lockdown's accessibility service was turned off - open the app to turn it back on.")
        if (cfg.settings.siteFilterOn && !com.husarp.lockdown.vpn.LockdownVpn.active)
            notify(4, "Site filter stopped", "The site filter isn't running - open Lockdown to restart it.")
        val link = com.husarp.lockdown.link.IslandLink
        // An open "Island helper stopped" alert (the service counts it) is put up again here, in case that service is
        // gone; the plain notice is for everything else that leaves Island uncovered.
        val stoppedAlert = com.husarp.lockdown.link.HelperWatcher.repost(applicationContext, cfg.enabled && cfg.guardrails.helperAlert)
        // Only after 2 failed checks in a row, and taken down once one passes (IslandSight.noticeStep).
        val bad = !stoppedAlert && cfg.enabled && link.mainState.value.state == "linked" && !link.healthy(applicationContext)
        val screenOn = runCatching { applicationContext.getSystemService(android.os.PowerManager::class.java).isInteractive }.getOrDefault(true)
        val fails = Reminders.islandPrefs(applicationContext).getInt("fails", 0)
        val (nextFails, act) = com.husarp.lockdown.engine.IslandSight.noticeStep(fails, bad, screenOn)
        when (act) {
            com.husarp.lockdown.engine.IslandSight.Notice.POST ->
                notify(ISLAND_ID, "Island apps not covered", "Lockdown in Island isn't checking in or can't block - open it there.")
            com.husarp.lockdown.engine.IslandSight.Notice.CANCEL -> Reminders.islandBack(applicationContext)
            com.husarp.lockdown.engine.IslandSight.Notice.NONE -> {}
        }
        if (nextFails != fails) Reminders.islandPrefs(applicationContext).edit().putInt("fails", nextFails).apply()

        // daily screen-time goal (once a day) - held while a pause silences alerts (PC 0.84.8); the tamper notices above
        // and below are never silenced
        val silent = com.husarp.lockdown.block.Enforce.silenced(cfg, com.husarp.lockdown.guard.TrustedTime.local(applicationContext))
        val budget = cfg.settings.dailyGoalMin
        if (budget > 0 && !silent) {
            val main = runCatching {
                com.husarp.lockdown.usage.Usage.dayBounds(applicationContext, 0).let { (s, e) ->
                    com.husarp.lockdown.usage.Usage.range(applicationContext, s, e) { isStopped }.totalMs }
            }.getOrDefault(0)
            // Island apps count toward the goal too (as Home's goal bar)
            val day = com.husarp.lockdown.guard.TrustedTime.local(applicationContext).toLocalDate()   // trusted time's day
            val total = runCatching { com.husarp.lockdown.link.IslandLink.withIsland(applicationContext, main, listOf(day)) }.getOrDefault(main)
            if (total >= budget * 60_000L) {
                val prefs = applicationContext.getSharedPreferences("goal", Context.MODE_PRIVATE)
                val today = day.toString()
                if (prefs.getString("day", "") != today) {
                    prefs.edit().putString("day", today).apply()
                    notify(5, "Daily screen-time goal reached", "You've used your ${budget}-minute goal for today.")
                }
            }
        }

        // clock-tamper warning
        if (kotlin.math.abs(com.husarp.lockdown.guard.TrustedTime.driftMs(applicationContext)) > 5 * 60_000)
            notify(6, "Clock changed", "The phone's clock was changed - Lockdown's limits and timers ignore that.")

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
