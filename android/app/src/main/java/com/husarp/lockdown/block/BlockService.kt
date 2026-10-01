package com.husarp.lockdown.block

import android.accessibilityservice.AccessibilityService
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.provider.Settings
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.data.UsageStore
import com.husarp.lockdown.engine.Active
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.SleepCfg
import com.husarp.lockdown.guard.TrustedTime

/**
 * Watches the foreground app / browser tab. It feeds the usage engine (so time and opening limits count),
 * asks the rule engine (plus the active mode) whether to block, covers the screen with an overlay when it
 * should, blocks bad keywords in browsers, and drives the bedtime nudge cadence.
 */
class BlockService : AccessibilityService() {

    private var overlay: View? = null
    private var wm: WindowManager? = null
    private val handler = android.os.Handler(android.os.Looper.getMainLooper())

    @Volatile private var currentPkg = ""
    @Volatile private var lastUrl: String? = null
    private var prevTickPkg = ""
    private var lastKeywordScan = 0L
    private var lastPausePkg = ""
    private var lastPauseAt = 0L
    private var lastBedtimeNudge = 0L
    private var shownItemId: String? = null

    override fun onServiceConnected() {
        wm = getSystemService(WINDOW_SERVICE) as WindowManager
        runCatching { Protection.load(this) }   // so protection lists block in the browser even without the VPN
        handler.post(tick)
        handler.post(bedtimeTick)
    }

    /** Every few seconds: count usage for what's in front, and block it if a rule (or the active mode) says so. */
    private val tick = object : Runnable {
        override fun run() {
            runCatching { step() }
            handler.postDelayed(this, TICK_MS)
        }
    }

    private fun step() {
        val cfg = Store.config
        val pkg = currentPkg
        val now = Enforce.ldt(TrustedTime.now(this))
        UsageStore.counter.clock = cfg.clock()
        val mode = ModesStore.current(now)

        val actives = ArrayList<Active>()
        val appItem = cfg.items.firstOrNull { it.type == ItemType.APP && it.target.equals(pkg, true) }
        if (appItem != null && !appItem.disabled)
            actives.add(Active(appItem, Rules.countedRules(appItem, cfg.groups), launched = pkg != prevTickPkg))
        val host = if (isBrowser(pkg)) hostOf(lastUrl) else null
        val siteItem = host?.let { h -> cfg.items.firstOrNull { it.type == ItemType.SITE && !it.disabled && hostMatches(it.target, h) } }
        if (siteItem != null) actives.add(Active(siteItem, Rules.countedRules(siteItem, cfg.groups)))
        UsageStore.record(actives, now)
        prevTickPkg = pkg

        val label = if (appItem != null) appItem.name else Apps.label(packageManager, pkg)
        var verdict = Enforce.app(cfg, pkg, now, mode, label)
            ?: (if (host != null) Enforce.site(cfg, host, now, mode) else null)
        // Protection lists block in the browser without the VPN. Never gated by the emergency unlock.
        if (verdict == null && host != null && cfg.enabled && Protection.blocked(host))
            verdict = Verdict(host, "Blocked site", "$host is on a protection list.", null, "back", null)
        if (verdict != null) showBlock(verdict) else removeOverlay()

        // pause-before-open: a mindful wait when opening a time-limited app
        if (verdict == null && appItem != null && cfg.settings.pauseBeforeOpen && appItem.rules.isNotEmpty() && pkg != lastPausePkg) {
            val ms = System.currentTimeMillis()
            if (ms - lastPauseAt > 5 * 60_000) { lastPausePkg = pkg; lastPauseAt = ms; showPause(appItem.name, cfg.settings.pauseSec.coerceAtLeast(1)) }
        }
    }

    // Everything here is wrapped: an uncaught exception in an accessibility callback makes Android DISABLE the
    // service (the "it keeps losing the grant" symptom). Nothing this service does is worth that.
    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        runCatching {
            val pkg = event.packageName?.toString() ?: return@runCatching
            if (pkg == packageName || pkg == "com.android.systemui") return@runCatching
            if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
                currentPkg = pkg
                if (isBrowser(pkg)) lastUrl = browserBarText()
                handler.post { runCatching { step() } }        // react immediately, don't wait for the next tick
            }

            // keyword blocking: watch the address / search bar and block on a match (throttled)
            val cfg = Store.config
            if (cfg.keywords.enabled && isBrowser(pkg)) {
                val ms = System.currentTimeMillis()
                if (ms - lastKeywordScan >= 400) {
                    lastKeywordScan = ms
                    val text = browserBarText()
                    if (text != null) lastUrl = text
                    val hit = Keywords.hit(cfg, text)
                    if (hit != null) {
                        showBlock(Verdict(hit, "Blocked search", "\"$hit\" is on your blocked words.", null, "back", null))
                        performGlobalAction(GLOBAL_ACTION_BACK)
                    }
                }
            }
        }
    }

    private fun browserBarText(): String? {
        val root = rootInActiveWindow ?: return null
        for (id in URL_BAR_IDS) {
            val nodes = runCatching { root.findAccessibilityNodeInfosByViewId(id) }.getOrNull() ?: continue
            nodes.firstOrNull()?.text?.let { return it.toString() }
        }
        return null
    }

    // ---------- overlay ----------

    private fun showBlock(v: Verdict) {
        if (overlay != null && shownItemId == v.itemId) return
        removeOverlay()
        shownItemId = v.itemId
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
            text = "OK"; setTextColor(Color.parseColor("#5C1900"))
            setBackgroundColor(accent)
            setOnClickListener { removeOverlay(); performGlobalAction(GLOBAL_ACTION_HOME) }
            val lp = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 130); lp.topMargin = 48
            layoutParams = lp
        })
        root.addView(col, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        addOverlay(root)
    }

    private fun showPause(name: String, sec: Int) {
        if (overlay != null) return
        val bg = Color.parseColor("#1A110F"); val onBg = Color.parseColor("#F1DFDA")
        val root = FrameLayout(this).apply { setBackgroundColor(bg) }
        val count = TextView(this).apply { textSize = 48f; setTextColor(onBg); gravity = Gravity.CENTER; typeface = Typeface.create("sans-serif-condensed", Typeface.BOLD) }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER; setPadding(64, 64, 64, 64)
            addView(TextView(this@BlockService).apply { text = "Take a breath before $name"; textSize = 18f; setTextColor(Color.parseColor("#D8C2BC")); gravity = Gravity.CENTER })
            addView(count)
        }
        root.addView(col, FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.CENTER))
        addOverlay(root)
        val r = object : Runnable {
            var left = sec
            override fun run() {
                if (overlay !== root) return
                count.text = left.toString()
                if (left <= 0) { removeOverlay(); return }
                left--; handler.postDelayed(this, 1000)
            }
        }
        handler.post(r)
    }

    private fun addOverlay(view: View) {
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY, 0, PixelFormat.OPAQUE,
        )
        runCatching { wm?.addView(view, lp); overlay = view }
    }

    private fun removeOverlay() {
        overlay?.let { runCatching { wm?.removeView(it) } }
        overlay = null; shownItemId = null
    }

    // ---------- bedtime ----------

    private val bedtimeTick = object : Runnable {
        override fun run() {
            runCatching {
                val sleep = Store.config.sleep
                val cal = java.util.Calendar.getInstance()
                val mins = cal.get(java.util.Calendar.HOUR_OF_DAY) * 60 + cal.get(java.util.Calendar.MINUTE)
                val every = bedtimeInterval(sleep, mins)
                if (every != null) {
                    val now = System.currentTimeMillis()
                    if (now - lastBedtimeNudge >= every * 60_000L) { lastBedtimeNudge = now; notifyBedtime() }
                }
            }
            handler.postDelayed(this, 60_000)
        }
    }

    private fun notifyBedtime() {
        val nm = getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel("reminders") == null)
            nm.createNotificationChannel(NotificationChannel("reminders", "Reminders", NotificationManager.IMPORTANCE_HIGH))
        nm.notify(1, android.app.Notification.Builder(this, "reminders")
            .setContentTitle("Time for bed")
            .setContentText("It's late - wind down and get some sleep.")
            .setSmallIcon(com.husarp.lockdown.R.drawable.ic_launcher_foreground)
            .setAutoCancel(true).build())
    }

    override fun onInterrupt() {}
    override fun onDestroy() { removeOverlay(); handler.removeCallbacksAndMessages(null); super.onDestroy() }

    companion object {
        private const val TICK_MS = 3000L

        fun isEnabled(ctx: Context): Boolean {
            val flat = Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES) ?: return false
            val me = "${ctx.packageName}/${BlockService::class.java.name}"
            val split = TextUtils.SimpleStringSplitter(':'); split.setString(flat)
            while (split.hasNext()) if (split.next().equals(me, ignoreCase = true)) return true
            return false
        }

        fun settingsIntent() = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)

        private fun hostMatches(target: String, host: String): Boolean {
            val h = host.lowercase().removePrefix("www.")
            return target.lowercase().split(" ").any { d -> d.isNotBlank() && (h == d || h.endsWith(".$d")) }
        }

        private fun hostOf(text: String?): String? {
            val t = text?.trim()?.lowercase() ?: return null
            if (t.isEmpty() || " " in t) return null                 // a search phrase, not a host
            val noScheme = t.substringAfter("://", t)
            val host = noScheme.substringBefore('/').substringBefore('?').removePrefix("www.")
            return if ("." in host) host else null
        }

        private fun isBrowser(pkg: String) = pkg in BROWSERS

        private val BROWSERS = setOf(
            "com.android.chrome", "com.brave.browser", "com.brave.browser.bt1", "com.microsoft.emmx",
            "com.sec.android.app.sbrowser", "org.mozilla.firefox", "com.opera.browser", "com.duckduckgo.mobile.android",
        )

        private fun hhmm(s: String) = s.split(":").let { (it.getOrNull(0)?.toIntOrNull() ?: 0) * 60 + (it.getOrNull(1)?.toIntOrNull() ?: 0) }

        /** Minutes between bedtime nudges now, or null if outside the sleep window. Uses the latest passed tier. */
        fun bedtimeInterval(sleep: SleepCfg, mins: Int): Int? {
            if (!sleep.on) return null
            val bed = hhmm(sleep.bedtime); val wake = hhmm(sleep.wake)
            val inNight = if (bed <= wake) mins in bed until wake else mins >= bed || mins < wake
            if (!inNight) return null
            val nightLen = ((wake - bed + 1440) % 1440).let { if (it == 0) 1440 else it }
            fun sinceBed(t: Int) = (t - bed + 1440) % 1440
            val nowSb = sinceBed(mins)
            val passed = sleep.tiers.map { it.every to sinceBed(hhmm(it.from)) }
                .filter { it.second <= nightLen && it.second <= nowSb }
                .maxByOrNull { it.second }
            return passed?.first ?: 15
        }

        private val URL_BAR_IDS = listOf(
            "com.android.chrome:id/url_bar",
            "com.brave.browser:id/url_bar",
            "com.brave.browser.bt1:id/url_bar",
            "com.microsoft.emmx:id/url_bar",
            "com.sec.android.app.sbrowser:id/location_bar_edit_text",
            "org.mozilla.firefox:id/mozac_browser_toolbar_url_view",
            "org.mozilla.firefox:id/url_bar_title",
        )
    }
}
