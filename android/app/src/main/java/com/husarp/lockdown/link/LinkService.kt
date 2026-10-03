package com.husarp.lockdown.link

import android.app.KeyguardManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.husarp.lockdown.MainActivity
import com.husarp.lockdown.R
import com.husarp.lockdown.block.Apps
import com.husarp.lockdown.block.BlockService
import com.husarp.lockdown.block.Enforce
import com.husarp.lockdown.block.Media
import com.husarp.lockdown.block.Verdict
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.data.UsageStore
import com.husarp.lockdown.engine.Active
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.guard.TrustedTime
import kotlin.concurrent.thread

/**
 * The Island helper's foreground service. It syncs with main (every 5 s while the phone is in use, 60 s otherwise,
 * at once when the app in front changes), and finds the Island app in front from usage events. When this copy's
 * accessibility service can't run (the usual case in a work profile) it also counts and blocks Island apps itself,
 * with the same engine, behind a "Display over other apps" block screen.
 */
class LinkService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    @Volatile private var running = false
    private var front: String? = null
    private var resumed: List<String> = emptyList()
    private var lastTs = 0L
    private val seenAtLast = HashSet<String>()
    private var prevPkg = ""
    private var allowedPkg: String? = null
    private var overlay: View? = null
    private var blockedPkg: String? = null

    override fun onBind(intent: Intent?) = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        runCatching {
            ServiceCompat.startForeground(this, NOTIF_ID, notification(),
                if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0)
        }
        if (!IslandLink.isHelper) { stopSelf(); return START_NOT_STICKY }
        if (!running) {
            running = true
            lastTs = System.currentTimeMillis() - 60 * 60_000L
            handler.post(poll)
            handler.post(tick)
            thread(isDaemon = true, name = "lockdown-link-sync") {
                while (running && IslandLink.isHelper) {
                    runCatching { IslandLink.syncOnce(this) }
                    IslandLink.waitKick(if (interactive()) 5_000 else 60_000)
                }
            }
        }
        IslandLink.kick()
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        handler.removeCallbacksAndMessages(null)
        hideBlock()
        IslandLink.kick()
        super.onDestroy()
    }

    private fun interactive() = getSystemService(PowerManager::class.java).isInteractive
    private fun inUse() = interactive() && !getSystemService(KeyguardManager::class.java).isKeyguardLocked
    // This copy's own accessibility service does it when it really runs (ticked in Settings isn't enough: in a
    // work profile Android usually never binds it).
    private fun enforcing() = !BlockService.connected

    /** Every second while in use (10 s otherwise): the app in front, from this profile's usage events. */
    private val poll = object : Runnable {
        override fun run() {
            runCatching { pollFront() }
            if (running) handler.postDelayed(this, if (inUse()) 1000 else 10_000)
        }
    }

    /** Every 3 s: count and block (the fallback). */
    private val tick = object : Runnable {
        override fun run() {
            runCatching { if (enforcing()) step() else hideBlock() }
            if (running) handler.postDelayed(this, TICK_MS)
        }
    }

    private fun pollFront() {
        val now = System.currentTimeMillis()
        val usm = getSystemService(UsageStatsManager::class.java)
        // From the last event's time on (an event written a moment late with that same time isn't lost); the ones
        // already seen at that time are skipped.
        val events = usm.queryEvents(lastTs, now) ?: return
        val list = ArrayList<Triple<Int, String, String>>()
        val e = UsageEvents.Event()
        while (events.hasNextEvent()) {
            events.getNextEvent(e)
            val pkg = e.packageName ?: continue
            val id = "${e.eventType}/$pkg/${e.className}"
            if (e.timeStamp < lastTs || (e.timeStamp == lastTs && id in seenAtLast)) continue
            if (e.timeStamp > lastTs) { lastTs = e.timeStamp; seenAtLast.clear() }
            seenAtLast.add(id)
            list.add(Triple(e.eventType, pkg, e.className ?: ""))
        }
        resumed = Foreground.reduce(resumed, list)
        val f = Foreground.front(resumed)
        // Main stops counting its own last app while Island has one in front: never claim one with the screen off.
        val shown = if (inUse()) f else null
        if (shown != IslandLink.localFront) { IslandLink.localFront = shown; IslandLink.kick() }
        if (f == front) return
        front = f
        if (enforcing()) step()
    }

    /** Mirrors BlockService.step for apps: count what's in front (not a blocked item), then block it if the rules
     *  or the running mode say so. */
    private fun step() {
        val cfg = Store.config
        val now = Enforce.ldt(TrustedTime.now(this))
        UsageStore.counter.clock = cfg.clock()
        val pkg = front
        if (!inUse() || pkg == null || pkg == packageName) {
            UsageStore.record(emptyList(), now)
            if (pkg == null) prevPkg = ""
            hideBlock(); return
        }
        val item = cfg.items.firstOrNull { it.type == ItemType.APP && it.target.equals(pkg, true) }
        val actives = if (item != null && !item.disabled && !Enforce.itemBlocked(cfg, item, now))
            listOf(Active(item, Rules.countedRules(item, cfg.groups), launched = pkg != prevPkg)) else emptyList()
        UsageStore.record(actives, now)
        prevPkg = pkg
        val v = Enforce.app(cfg, pkg, now, ModesStore.current(now), item?.name ?: Apps.label(packageManager, pkg))
        if (v == null) { allowedPkg = pkg; hideBlock(); return }
        if (allowedPkg == pkg) Media.pause(this)      // its block started while it was in use: stop the video
        allowedPkg = null
        showBlock(v, pkg)
    }

    // ---------- block screen (same look as BlockService's) ----------

    private fun showBlock(v: Verdict, pkg: String) {
        if (overlay != null && blockedPkg == pkg) return
        hideBlock()
        if (!Settings.canDrawOverlays(this)) return
        val bg = Color.parseColor("#1A110F"); val onBg = Color.parseColor("#F1DFDA")
        val muted = Color.parseColor("#D8C2BC"); val accent = Color.parseColor("#FFB59C")
        val root = FrameLayout(this).apply { setBackgroundColor(bg) }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER_VERTICAL
            setPadding(72, 72, 72, 72)
        }
        col.addView(TextView(this).apply {
            text = v.headline; textSize = 30f; setTextColor(onBg)
            typeface = Typeface.create("sans-serif-condensed", Typeface.BOLD)
        })
        col.addView(TextView(this).apply {
            text = v.sub; textSize = 16f; setTextColor(muted); setPadding(0, 24, 0, 0)
        })
        col.addView(Button(this).apply {
            text = "Leave"; setTextColor(Color.parseColor("#5C1900"))
            setBackgroundColor(accent)
            setOnClickListener { leave() }
            val lp = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 130); lp.topMargin = 48
            layoutParams = lp
        })
        root.addView(col, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.OPAQUE,
        )
        runCatching { getSystemService(WindowManager::class.java).addView(root, lp); overlay = root; blockedPkg = pkg }
    }

    /** The block screen stays while the blocked app is in front; Leave goes home (or, failing that, to main Lockdown). */
    private fun leave() {
        val home = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (runCatching { startActivity(home) }.isFailure) IslandLink.openOtherProfile(this)
    }

    private fun hideBlock() {
        overlay?.let { runCatching { getSystemService(WindowManager::class.java).removeView(it) } }
        overlay = null; blockedPkg = null
    }

    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Island link", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Keeps Island apps following your main Lockdown's rules."
            })
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        return Notification.Builder(this, CHANNEL)
            .setContentTitle("Lockdown in Island")
            .setContentText("Following your main Lockdown's rules")
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val CHANNEL = "link"
        private const val NOTIF_ID = 43
        private const val TICK_MS = 3000L

        fun start(ctx: Context) = ContextCompat.startForegroundService(ctx, Intent(ctx, LinkService::class.java))
        fun stop(ctx: Context) = ctx.stopService(Intent(ctx, LinkService::class.java))
    }
}
