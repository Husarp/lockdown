package com.husarp.lockdown.block

import android.accessibilityservice.AccessibilityService
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.os.SystemClock
import android.provider.Settings
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityWindowInfo
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.data.UsageStore
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.engine.Active
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.ModeState
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.SleepCfg
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.link.IslandLink
import com.husarp.lockdown.remind.Grayscale
import java.time.LocalDate
import java.time.LocalDateTime

/**
 * Watches the foreground app / browser tab. It feeds the usage engine (so time and opening limits count),
 * asks the rule engine (plus the active mode) whether to block, and when it should: pauses a video that was
 * playing, leaves the app / goes back in the browser (the item's block method) and covers the screen with a
 * notice until OK. It also stops a blocked video carrying on in picture-in-picture, blocks bad keywords in
 * browsers, and drives the bedtime nudge cadence and bedtime grayscale.
 */
class BlockService : AccessibilityService() {

    private var overlay: View? = null
    private var wm: WindowManager? = null
    private val handler = android.os.Handler(android.os.Looper.getMainLooper())

    @Volatile private var currentPkg = ""
    private val lastUrl = HashMap<String, String>()   // browser -> last address read (kept while the bar is hidden)
    private var prevTickPkg = ""
    private var lastBarRead = 0L
    private var lastPausePkg = ""
    private var lastPauseAt = 0L
    private var lastBedtimeNudge = 0L
    private var shownItemId: String? = null
    private var blockUp = false                       // the block notice is up (stays until OK)
    private var homeOnOk = false                      // OK still has to leave the page (a "can't load" site)
    private var allowedKey: String? = null            // what was in front and allowed at the last step
    private val lastAct = HashMap<String, Long>()     // what was blocked -> when it was last acted on
    private val blockedAt = HashMap<String, Long>()   // package -> when a block last fired for it (to catch PiP)
    private val pipCaught = HashSet<Int>()            // picture-in-picture windows of a blocked video
    private var prunedOn: LocalDate? = null

    override fun onServiceConnected() {
        connected = true
        wm = getSystemService(WINDOW_SERVICE) as WindowManager
        // see every window (picture-in-picture too); also set in the config, this covers a service bound before the update
        runCatching { serviceInfo = serviceInfo.apply { flags = flags or android.accessibilityservice.AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS } }
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
        // Locked or screen off: nothing is being used (audio holding the phone awake used to keep counting).
        if (!screenInUse()) { UsageStore.record(emptyList(), now); return }
        val mode = ModesStore.current(now)
        // Island link: an Island app in front is the helper's to count and block - unless main's last app is still
        // on screen too (split screen). A blocked video in picture-in-picture stays paused either way.
        // In the helper, only what its own profile has in front is its business (when it can tell, with usage access).
        if ((IslandLink.islandInFront() && !onScreen(pkg)) ||
            (IslandLink.isHelper && pkg != IslandLink.localFront && com.husarp.lockdown.usage.Usage.hasAccess(this))) {
            UsageStore.record(emptyList(), now); watchPip(cfg, now, mode); return
        }

        // The address bar is hidden in a full-screen video: keep the last address read, don't lose the site.
        if (isBrowser(pkg)) readBar(pkg)
        val host = if (isBrowser(pkg)) hostOf(lastUrl[pkg]) else null
        val appItem = cfg.items.firstOrNull { it.type == ItemType.APP && it.target.equals(pkg, true) }
        val siteItem = host?.let { h -> cfg.items.firstOrNull { it.type == ItemType.SITE && !it.disabled && Enforce.hostMatches(it.target, h) } }

        // Count what's in use - but not an item that is blocked: its time behind the notice and its retries
        // aren't use (they used to fill its limits and the group's). The opening that goes over a limit counts.
        val actives = ArrayList<Active>()
        if (appItem != null && !appItem.disabled && !Enforce.itemBlocked(cfg, appItem, now))
            actives.add(Active(appItem, Rules.countedRules(appItem, cfg.groups), launched = pkg != prevTickPkg))
        if (siteItem != null && !Enforce.itemBlocked(cfg, siteItem, now))
            actives.add(Active(siteItem, Rules.countedRules(siteItem, cfg.groups)))
        UsageStore.record(actives, now)
        prevTickPkg = pkg

        val label = if (appItem != null) appItem.name else Apps.label(packageManager, pkg)
        var verdict = Enforce.app(cfg, pkg, now, mode, label)
            ?: (if (host != null) Enforce.site(cfg, host, now, mode) else null)
        // Protection lists block in the browser without the VPN. Never gated by the emergency unlock.
        if (verdict == null && host != null && cfg.enabled && Protection.blocked(host))
            verdict = Verdict(host, "Blocked site", "$host is on a protection list.", null, "back", null, site = true)
        val key = "$pkg|${host ?: ""}"
        if (verdict != null) block(verdict, pkg, key) else allowedKey = key

        // pause-before-open: a mindful wait when opening a time-limited app
        if (verdict == null && appItem != null && cfg.settings.pauseBeforeOpen && appItem.rules.isNotEmpty() && pkg != lastPausePkg) {
            val ms = System.currentTimeMillis()
            if (ms - lastPauseAt > 5 * 60_000) { lastPausePkg = pkg; lastPauseAt = ms; showPause(appItem.name, cfg.settings.pauseSec.coerceAtLeast(1)) }
        }
        watchPip(cfg, now, mode)
    }

    /**
     * Something in front is blocked. A video already playing is paused (its block started while it was in use),
     * then the item's block method: a site goes back ("back") or leaves the browser ("close"), or is left to the
     * site filter ("dns", the page is covered and OK leaves it); an app is left for the home screen. The notice
     * stays until OK, so nothing carries on behind it.
     */
    private fun block(v: Verdict, pkg: String, key: String) {
        val wasInUse = allowedKey == key
        allowedKey = null
        val flags = if (v.site) Enforce.siteFlags(v.blockType) else emptySet()
        val navigates = !v.site || "back" in flags || "close" in flags
        showBlock(v, homeOnOk = !navigates)
        val ms = SystemClock.elapsedRealtime()
        if (ms - (lastAct[key] ?: -ACT_GAP_MS) < ACT_GAP_MS) return
        lastAct[key] = ms
        blockedAt[pkg] = ms
        if (wasInUse) Media.pause(this)
        when {
            v.site && "back" in flags -> performGlobalAction(GLOBAL_ACTION_BACK)
            navigates -> performGlobalAction(GLOBAL_ACTION_HOME)
        }
        // Left the browser: forget the blocked address, so a page opened later isn't judged by it before its bar is read.
        if (v.site && "back" !in flags && navigates) lastUrl.remove(pkg)
    }

    /** A blocked video that went on in picture-in-picture (YouTube, a browser's full-screen video) is paused -
     *  and kept paused while that window stays, notice or no notice. */
    private fun watchPip(cfg: Config, now: LocalDateTime, mode: ModeState?) {
        val ws = runCatching { windows }.getOrNull() ?: return
        val ms = SystemClock.elapsedRealtime()
        val caught = HashSet<Int>()
        for (w in ws) {
            if (!w.isInPictureInPictureMode) continue
            val p = runCatching { w.root?.packageName?.toString() }.getOrNull() ?: continue
            val justBlocked = ms - (blockedAt[p] ?: -PIP_CATCH_MS) < PIP_CATCH_MS
            if (w.id in pipCaught || justBlocked || Enforce.app(cfg, p, now, mode) != null) caught.add(w.id)
        }
        pipCaught.clear(); pipCaught.addAll(caught)
        if (caught.isNotEmpty() && Media.playing(this)) Media.pause(this)
    }

    /** [pkg] has an app window on screen (this profile's windows only). */
    private fun onScreen(pkg: String): Boolean {
        val ws = runCatching { windows }.getOrNull() ?: return false
        return ws.any { w -> w.type == AccessibilityWindowInfo.TYPE_APPLICATION &&
            runCatching { w.root?.packageName?.toString() }.getOrNull() == pkg }
    }

    private fun screenInUse(): Boolean {
        val pm = getSystemService(POWER_SERVICE) as android.os.PowerManager
        val km = getSystemService(KEYGUARD_SERVICE) as android.app.KeyguardManager
        return pm.isInteractive && !km.isKeyguardLocked
    }

    private fun readBar(pkg: String) { browserBarText()?.let { lastUrl[pkg] = it } }

    // Everything here is wrapped: an uncaught exception in an accessibility callback makes Android DISABLE the
    // service (the "it keeps losing the grant" symptom). Nothing this service does is worth that.
    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        runCatching {
            val pkg = event.packageName?.toString() ?: return@runCatching
            if (pkg == packageName || pkg == "com.android.systemui") return@runCatching
            if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
                currentPkg = pkg
                IslandLink.islandFront = null                   // a main-profile window came to the front
                if (isBrowser(pkg)) readBar(pkg)
                handler.post { runCatching { step() } }        // react immediately, don't wait for the next tick
            }
            if (!isBrowser(pkg)) return@runCatching

            // The address changes without a window change (a link, back): keep it fresh, keywords on or off.
            val ms = System.currentTimeMillis()
            if (ms - lastBarRead < 400) return@runCatching
            lastBarRead = ms
            val text = browserBarText()
            if (text != null) lastUrl[pkg] = text
            // keyword blocking: watch the address / search bar and block on a match
            val cfg = Store.config
            if (cfg.keywords.enabled) {
                val hit = Keywords.hit(cfg, text)
                if (hit != null) block(Verdict(hit, "Blocked search", "\"$hit\" is on your blocked words.", null, "back", null, site = true), pkg, "$pkg|kw")
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

    private fun showBlock(v: Verdict, homeOnOk: Boolean) {
        if (overlay != null && blockUp && shownItemId == v.itemId) return
        removeOverlay()
        shownItemId = v.itemId
        this.homeOnOk = homeOnOk
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
            setOnClickListener { val home = this@BlockService.homeOnOk; removeOverlay(); if (home) { lastUrl.remove(currentPkg); performGlobalAction(GLOBAL_ACTION_HOME) } }
            val lp = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 130); lp.topMargin = 48
            layoutParams = lp
        })
        root.addView(col, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        addOverlay(root)
        blockUp = overlay === root
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
            // not focusable: Back / the browser's back go to the page under it, and the address bar stays readable
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY, WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE, PixelFormat.OPAQUE,
        )
        runCatching { wm?.addView(view, lp); overlay = view }
    }

    private fun removeOverlay() {
        overlay?.let { runCatching { wm?.removeView(it) } }
        overlay = null; shownItemId = null; blockUp = false
    }

    // ---------- bedtime ----------

    /** Every minute: bedtime nudges, bedtime grayscale on/off (colour back within a minute of wake time, not up
     *  to 15+), and once a day dropping usage counters that can no longer matter. */
    private val bedtimeTick = object : Runnable {
        override fun run() {
            runCatching {
                val cfg = Store.config
                val sleep = cfg.sleep
                val cal = java.util.Calendar.getInstance()
                val mins = cal.get(java.util.Calendar.HOUR_OF_DAY) * 60 + cal.get(java.util.Calendar.MINUTE)
                val now = Enforce.ldt(TrustedTime.now(this@BlockService))
                val every = bedtimeInterval(sleep, mins)
                if (every != null && !Enforce.alertsPaused(cfg, now) && !IslandLink.isHelper) {   // main sends the helper's
                    val ms = System.currentTimeMillis()
                    if (ms - lastBedtimeNudge >= every * 60_000L) { lastBedtimeNudge = ms; notifyBedtime() }
                }
                if (prunedOn != now.toLocalDate()) { prunedOn = now.toLocalDate(); UsageStore.prune(now) }
            }
            runCatching { Grayscale.sync(this@BlockService) }
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
    override fun onUnbind(intent: Intent?): Boolean { connected = false; return super.onUnbind(intent) }
    override fun onDestroy() { connected = false; removeOverlay(); handler.removeCallbacksAndMessages(null); super.onDestroy() }

    companion object {
        private const val TICK_MS = 3000L
        private const val ACT_GAP_MS = 2000L        // act on the same block at most this often (Back / Home)
        private const val PIP_CATCH_MS = 15_000L    // a PiP window this soon after its app's block is the blocked video

        /** The service really runs (bound by Android), not just ticked in Settings. Same process as its readers. */
        @Volatile var connected = false
            private set

        fun isEnabled(ctx: Context): Boolean {
            val flat = Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES) ?: return false
            val me = "${ctx.packageName}/${BlockService::class.java.name}"
            val split = TextUtils.SimpleStringSplitter(':'); split.setString(flat)
            while (split.hasNext()) if (split.next().equals(me, ignoreCase = true)) return true
            return false
        }

        fun settingsIntent() = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)

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
