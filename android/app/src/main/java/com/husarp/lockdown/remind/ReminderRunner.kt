package com.husarp.lockdown.remind

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.SystemClock
import android.widget.Toast
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.husarp.lockdown.MainActivity
import com.husarp.lockdown.R
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.block.Enforce
import com.husarp.lockdown.engine.Emergency
import com.husarp.lockdown.engine.Modes
import com.husarp.lockdown.engine.RPrompt
import com.husarp.lockdown.engine.RemindersEngine
import com.husarp.lockdown.engine.TickResult
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.link.IslandLink
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.time.LocalDate
import java.time.LocalDateTime

/**
 * Runs the reminders engine (bedtime, breaks, your own reminders) from the accessibility service's tick, as the
 * PC's tray agent does. Popups are notifications with the same buttons; the bedtime screen and a strict break are
 * full-screen ([Screen], drawn by the service). Only the main copy runs it: a linked Island helper would nudge
 * twice. Everything here runs on the main thread (service tick, notification buttons, the app).
 */
object ReminderRunner {
    /** What the service draws: the full-screen prompts (bedtime) and a strict break. */
    interface Screen {
        fun show(p: RPrompt)
        fun close(key: String)
        fun startBreak(until: LocalDateTime)
        fun endBreak()
    }

    var screen: Screen? = null
    private var engine: RemindersEngine? = null
    private const val CHANNEL = "nudges"
    private const val NOTE_ID = 40
    private const val ASK_LIMIT_MS = 2 * 60_000L    // "Disable alerts": away from the app this long = Dismiss
    private const val ASK_MAX_MS = 16 * 60_000L     // ... and never open longer than this

    private val _asking = MutableStateFlow(false)
    /** The bedtime screen's "Disable alerts" is waiting for the challenge in the app. */
    val asking: StateFlow<Boolean> = _asking
    private var askStart = 0L
    private var askSeen = 0L
    private val posted = HashSet<String>()            // prompts up as notifications, waiting for an answer
    /** The app is in front (set by MainActivity), so the challenge is still being worked on. */
    @Volatile var appVisible = false

    fun now(ctx: Context): LocalDateTime = Enforce.ldt(TrustedTime.now(ctx))

    fun tick(ctx: Context, idleSec: Double, dt: Double) {
        if (IslandLink.isHelper) { stop(ctx); return }   // main nudges for both profiles
        val e = engine ?: load(ctx).also { engine = it }
        val cfg = Store.config
        val now = now(ctx)
        if (_asking.value) {
            val ms = SystemClock.elapsedRealtime()
            if (appVisible) askSeen = ms
            if (ms - askSeen > ASK_LIMIT_MS || ms - askStart > ASK_MAX_MS) askAnswered(ctx, "dismiss")
        }
        gone(ctx)
        val r = e.tick(now, idleSec, cfg.sleep, cfg.breaks, cfg.customs, dt,
            paused = Enforce.alertsPaused(cfg, now), emergencyLeft = emergencyLeft(cfg, now), silent = Enforce.silenced(cfg, now))
        apply(ctx, r, now)
    }

    /** A button: on a notification, on the bedtime screen, or the app's answer to "Disable alerts". */
    fun answer(ctx: Context, key: String, action: String) {
        NotificationManagerCompat.from(ctx).cancel(key, NOTE_ID)
        posted.remove(key)
        val e = engine ?: return
        val cfg = Store.config
        val now = now(ctx)
        // "swipe" (swiped away, or gone without an answer): the engine decides what it counts as
        apply(ctx, e.answer(key, action, now, cfg.sleep, cfg.breaks, cfg.customs), now)
        if (action == "done") saveDone(ctx, e, now.toLocalDate())
    }

    /** A notification that went away with no answer and no swipe (its channel or all notifications turned off
     *  in Settings): counts as swiped, so it can't hold the break or a reminder open for good. */
    private fun gone(ctx: Context) {
        if (posted.isEmpty()) return
        val nm = ctx.getSystemService(NotificationManager::class.java)
        val up = runCatching { nm.activeNotifications.filter { it.id == NOTE_ID }.map { it.tag }.toSet() }.getOrNull() ?: return
        for (key in posted.filter { it !in up }) answer(ctx, key, "swipe")
    }

    /** "Disable alerts" on the bedtime screen: the challenge in the app first, then off tonight or a snooze.
     *  The bedtime screen stays away while that's open, but not for ever (see [tick]). */
    fun askDisable(ctx: Context) {
        askStart = SystemClock.elapsedRealtime(); askSeen = askStart
        _asking.value = true
        runCatching {
            ctx.startActivity(Intent(ctx, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
        }
    }

    /** The app's answer to "Disable alerts": "off_tonight", "snooze:15", or "dismiss" (cancelled / timed out). */
    fun askAnswered(ctx: Context, action: String) {
        if (!_asking.value) return
        _asking.value = false
        answer(ctx, "sleep", action)
    }

    fun emergencyLeft(cfg: Config, now: LocalDateTime): Int =
        if (!cfg.emergency.enabled) 0
        else Emergency.usesLeft(cfg.unlocks.mapNotNull { runCatching { LocalDateTime.parse(it) }.getOrNull() },
            now, cfg.emergency.per, cfg.emergency.uses, cfg.clock()).left

    /** "Emergency (N left)": one use pauses the bedtime and break alerts (and bedtime grayscale) for its length.
     *  The bedtime settings stay as they are. False if no use is left. */
    fun spendEmergency(ctx: Context): Boolean {
        val now = TrustedTime.local(ctx)
        val cfg = Store.config
        if (emergencyLeft(cfg, now) <= 0) { toast(ctx, "No emergency unlocks left."); return false }
        val until = now.plusMinutes(cfg.emergency.minutes.toLong())
        Store.update { it.copy(unlocks = it.unlocks + now.toString(), alertsPausedUntil = until.toString()) }
        runCatching { Grayscale.sync(ctx) }
        toast(ctx, "Emergency: bedtime and break alerts paused until %02d:%02d.".format(until.hour, until.minute))
        return true
    }

    private fun apply(ctx: Context, r: TickResult, now: LocalDateTime) {
        for (key in r.close) {
            NotificationManagerCompat.from(ctx).cancel(key, NOTE_ID)
            posted.remove(key)
            screen?.close(key)
        }
        for (p in r.show) when {
            p.overlay -> screen?.show(p)
            notify(ctx, p) -> {}
            // Notifications are off: the break and important reminders come full screen instead (turning
            // notifications off must not switch them off); anything else is let go, so it doesn't stay pending.
            important(p) && screen != null -> screen?.show(p.copy(overlay = true))
            else -> answer(ctx, p.key, "close")
        }
        for (t in r.toasts) toast(ctx, t)
        r.startMode?.let { (id, until) ->
            // a locked mode already on stays (as on the PC)
            if (Modes.canStop(ModesStore.current(now), false)) ModesStore.start(id, until, now = now)
        }
        r.startBreak?.let { screen?.startBreak(it) }
        if (r.breakEnded) { screen?.endBreak(); toast(ctx, "Break over - welcome back.") }
    }

    private fun important(p: RPrompt): Boolean {
        val cfg = Store.config
        return if (p.key == "break") cfg.breaks.strict || cfg.breaks.guarded
        else cfg.customs.any { "custom:${it.id}" == p.key && it.guarded }
    }

    /** Posts [p] as a notification; false if it can't be shown (no permission, notifications or the channel off). */
    private fun notify(ctx: Context, p: RPrompt): Boolean {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Bedtime, breaks and reminders", NotificationManager.IMPORTANCE_HIGH))
        if (Build.VERSION.SDK_INT >= 33 &&
            ActivityCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return false
        if (!NotificationManagerCompat.from(ctx).areNotificationsEnabled()) return false
        if (nm.getNotificationChannel(CHANNEL)?.importance == NotificationManager.IMPORTANCE_NONE) return false
        val b = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(p.title).setContentText(p.text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(p.text))
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
        for (btn in p.buttons.take(3))
            b.addAction(0, if (btn.action == "dismiss") "Dismiss" else btn.label, pending(ctx, p.key, btn.action))
        // A strict break has no way to wave it off: kept on where Android allows. Android 14+ lets it be swiped
        // anyway, and that counts as a snooze (the engine), so it still starts on its own.
        if (p.key == "break" && p.buttons.none { it.action == "dismiss" }) b.setOngoing(true)
        b.setDeleteIntent(pending(ctx, p.key, "swipe"))
        NotificationManagerCompat.from(ctx).notify(p.key, NOTE_ID, b.build())
        posted.add(p.key)
        return true
    }

    private fun pending(ctx: Context, key: String, action: String): PendingIntent =
        PendingIntent.getBroadcast(ctx, 0,
            Intent(ctx, ReminderActionReceiver::class.java)
                .setData(Uri.Builder().scheme("lockdown").authority("remind").appendPath(key).appendPath(action).build())
                .putExtra("key", key).putExtra("action", action),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    private fun toast(ctx: Context, text: String) {
        runCatching { Toast.makeText(ctx, text, Toast.LENGTH_LONG).show() }
    }

    /** This copy became a linked helper: main nudges now, so nothing of this copy's is left up. */
    private fun stop(ctx: Context) {
        if (engine == null) return
        engine = null
        _asking.value = false
        posted.clear()
        for (key in listOf("sleep", "break")) screen?.close(key)
        screen?.endBreak()
        val nm = ctx.getSystemService(NotificationManager::class.java)
        runCatching { nm.activeNotifications.filter { it.id == NOTE_ID }.forEach { nm.cancel(it.tag, NOTE_ID) } }
    }

    // The per-day "stop after N done" count survives the service restarting (the rest starts afresh, as on the PC).
    private fun prefs(ctx: Context) = ctx.getSharedPreferences("reminders", Context.MODE_PRIVATE)

    private fun load(ctx: Context): RemindersEngine {
        val e = RemindersEngine()
        val today = now(ctx).toLocalDate().toString()
        for (line in prefs(ctx).getString("done", "").orEmpty().lines()) {
            val k = line.substringBefore("=")
            val n = line.substringAfter("=", "").toIntOrNull() ?: continue
            if (k.endsWith("|$today")) e.doneToday[k] = n
        }
        return e
    }

    private fun saveDone(ctx: Context, e: RemindersEngine, today: LocalDate) {
        val keep = e.doneToday.filterKeys { it.endsWith("|$today") }
        prefs(ctx).edit().putString("done", keep.entries.joinToString("\n") { "${it.key}=${it.value}" }).apply()
    }
}

/** Done / Snooze / Dismiss (and swiping away) on a reminder notification. */
class ReminderActionReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val key = intent.getStringExtra("key") ?: return
        val action = intent.getStringExtra("action") ?: return
        runCatching { ReminderRunner.answer(ctx.applicationContext, key, action) }
    }
}
