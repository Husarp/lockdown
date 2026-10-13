package com.husarp.lockdown.block

import android.accessibilityservice.AccessibilityService
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.media.AudioManager
import android.os.Build
import android.os.SystemClock
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import com.husarp.lockdown.data.History
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.data.UsageStore
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.engine.Active
import com.husarp.lockdown.engine.Alerts
import com.husarp.lockdown.engine.BlockWatcher
import com.husarp.lockdown.engine.BreakAllow
import com.husarp.lockdown.engine.Browsers
import com.husarp.lockdown.engine.Cadence
import com.husarp.lockdown.engine.Emergency
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.ModeState
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.RPrompt
import com.husarp.lockdown.engine.RemindersEngine
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.link.HelperWatcher
import com.husarp.lockdown.link.IslandLink
import com.husarp.lockdown.remind.Grayscale
import com.husarp.lockdown.remind.ReminderRunner
import java.time.LocalDate
import java.time.LocalDateTime

/**
 * Watches the foreground app / browser tab. It feeds the usage engine (so time and opening limits count),
 * asks the rule engine (plus the active mode) whether to block, and when it should: pauses a video that was
 * playing, leaves the app / goes back in the browser (the item's block method) and covers the screen with a
 * notice until OK; an app whose way is "grey" stays open and the screen goes grey while it's in front. It also
 * stops a blocked video carrying on in picture-in-picture, blocks bad keywords in browsers, and runs the reminders
 * (bedtime screen, breaks, your own reminders) and bedtime grayscale.
 */
class BlockService : AccessibilityService(), ReminderRunner.Screen {

    private var overlay: View? = null
    private var wm: WindowManager? = null
    private val handler = android.os.Handler(android.os.Looper.getMainLooper())
    private val cover by lazy { CoverLook(this) }     // the bedtime / strict-break screen's look

    @Volatile private var currentPkg = ""
    private val lastUrl = HashMap<String, String>()   // browser -> last address read (kept while the bar is hidden)
    private val privateTabs = HashSet<String>()       // browsers whose tab in front says it is private (not recorded as a site)
    private var prevTickPkg = ""
    private var lastBarRead = 0L
    private var lastPausePkg = ""
    private var lastPauseAt = 0L
    private val remindViews = HashMap<String, View>() // full-screen reminder prompts on screen: "sleep", "strict-break"
    private var remindHidden = false                  // they're out of the way: a call, the dialer, the lock screen
    private val onScreenKeys = HashSet<String>()      // the reminder views now added to the window manager
    private var breakDismissed = false                // the strict break's cover was dismissed: it comes back for a not-allowed app only
    private var breakCovered = false                  // the strict break's cover is wanted on screen now (call / lock screen aside)
    private var breakFront = ""                       // what is in front since the dismiss ("" = not known yet: counts as allowed)
    private var dialing = false                       // "Phone" was tapped on a strict break
    private var dialUntil = 0L                        // ... and the dialer has until then to come up
    private var lastInUse = 0L                        // the screen was last on and unlocked (elapsed ms)
    private var lastRemindTick = 0L
    private var shownItemId: String? = null
    private var blockUp = false                       // the block notice is up (stays until OK)
    private var homeOnOk = false                      // OK still has to leave the page (a "can't load" site)
    private var allowedKey: String? = null            // what was in front and allowed at the last step
    private val lastAct = HashMap<String, Long>()     // what was blocked -> when it was last acted on
    private val blockedAt = HashMap<String, Long>()   // package -> when a block last fired for it (to catch PiP)
    private val pipCaught = HashSet<Int>()            // picture-in-picture windows of a blocked video
    private var prunedOn: LocalDate? = null
    private var pauseWarned: String? = null           // the pause whose "back on in 5 min" was said
    private var quietUp = false                       // the notice up is a quiet one: it goes once what's in front is allowed
    private val lastNotice = HashMap<String, Long>()  // item (or reason:name) -> when its notice was last explained
    private var lastHistMs = 0L                       // the history was last given time (elapsed ms)
    @Volatile private var inUseIds: Set<String> = emptySet()   // the items in front at the last step
    private val watcher = BlockWatcher()
    private val debounce = com.husarp.lockdown.engine.BlockDebounce()   // leftover events of an app whose notice was just dismissed
    private var shownApp: String? = null              // the app the notice up is for (null: a site / keyword)
    private var alertSeq = 0
    @Volatile private var stepDelay = Cadence.STEP_MS   // the wait before the step loop's next run (stretched with the screen off)

    /** The screen went off / came on / was unlocked: the loops stretched for the screen being off run now, at the
     *  pace the screen calls for. A broadcast that can't be missed, so the stretch never delays blocking. */
    private val screenReceiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context, intent: Intent) { runCatching { wake(Cadence.closesUsageGap(intent.action)) } }
    }

    /** An app was installed, removed or changed: the apps a break leaves usable are looked up again (BreakApps). */
    private val packageReceiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context, intent: Intent) { BreakApps.invalidate() }
    }

    override fun onServiceConnected() {
        connected = true
        wm = getSystemService(WINDOW_SERVICE) as WindowManager
        // see every window (picture-in-picture too); also set in the config, this covers a service bound before the update
        runCatching { serviceInfo = serviceInfo.apply { flags = flags or android.accessibilityservice.AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS } }
        runCatching { Protection.load(this) }   // so protection lists block in the browser even without the VPN
        runCatching {
            androidx.core.content.ContextCompat.registerReceiver(this, screenReceiver, IntentFilter().apply {
                addAction(Intent.ACTION_SCREEN_ON); addAction(Intent.ACTION_SCREEN_OFF); addAction(Intent.ACTION_USER_PRESENT)
            }, androidx.core.content.ContextCompat.RECEIVER_NOT_EXPORTED)
        }
        runCatching {
            androidx.core.content.ContextCompat.registerReceiver(this, packageReceiver, IntentFilter().apply {
                addAction(Intent.ACTION_PACKAGE_ADDED); addAction(Intent.ACTION_PACKAGE_REMOVED)
                addAction(Intent.ACTION_PACKAGE_REPLACED); addAction(Intent.ACTION_PACKAGE_CHANGED)
                addDataScheme("package")
            }, androidx.core.content.ContextCompat.RECEIVER_NOT_EXPORTED)
        }
        handler.post(tick)
        handler.post(bedtimeTick)
        ReminderRunner.screen = this
        lastInUse = SystemClock.elapsedRealtime(); lastRemindTick = lastInUse
        handler.post(remindTick)
        lastHistMs = lastInUse
        handler.postDelayed(alertTick, Cadence.ALERT_MS)
    }

    /** Every few seconds: count usage for what's in front, and block it if a rule (or the active mode) says so.
     *  With the screen off nothing is in front: every 30 s (and at once when it comes on, see [wake]). */
    private val tick = object : Runnable {
        override fun run() {
            runCatching { step() }
            stepDelay = Cadence.stepMs(screenOn())
            handler.removeCallbacks(this)           // (step() may have set the next run already: one pending run only)
            handler.postDelayed(this, stepDelay)
        }
    }

    /** The screen changed: close out the time it was off (so the first step counts none of it) - only on SCREEN_ON /
     *  SCREEN_OFF ([closeGap], see [Cadence.closesUsageGap]) - then run the loops now: each sets its own next pace
     *  from the screen's state. */
    private fun wake(closeGap: Boolean) {
        if (closeGap) {
            val ms = SystemClock.elapsedRealtime()
            runCatching { UsageStore.record(emptyList(), Enforce.ldt(TrustedTime.now(this))) }
            if (screenOn()) lastHistMs = ms     // (off: the step that follows still gives the history the last seconds before it)
            lastRemindTick = ms
        }
        for (r in listOf(tick, bedtimeTick, remindTick, alertTick)) { handler.removeCallbacks(r); handler.post(r) }
    }

    /** The step loop is running at its stretched (screen off) pace but the screen is on: run it again soon. Called when
     *  an app comes up and at every step - so a screen receiver Android didn't register can't leave blocking at the
     *  slow pace. [stepDelay] is the quick pace from now on, which keeps a stream of events from pushing the run out. */
    private fun speedUpSteps() {
        if (!Cadence.rearmSteps(stepDelay, screenOn())) return
        stepDelay = Cadence.STEP_MS
        handler.removeCallbacks(tick); handler.postDelayed(tick, Cadence.STEP_MS)
    }

    private fun step() {
        speedUpSteps()
        val cfg = Store.config
        // the app whose notice was just dismissed is still closing: not in front (until really brought back)
        val pkg = currentPkg.takeUnless { debounce.leftover(it, SystemClock.elapsedRealtime()) } ?: ""
        val now = Enforce.ldt(TrustedTime.now(this))
        UsageStore.counter.clock = cfg.clock()
        val ms = SystemClock.elapsedRealtime()
        val dt = ((ms - lastHistMs) / 1000.0).coerceIn(0.0, 10.0)   // (a long gap was the phone asleep)
        lastHistMs = ms
        // Locked or screen off: nothing is being used (audio holding the phone awake used to keep counting).
        if (!screenInUse()) {
            UsageStore.record(emptyList(), now); inUseIds = emptySet()
            if (!IslandLink.isHelper) History.idle(now, dt)
            return
        }
        val mode = ModesStore.current(now)
        // Island link: an Island app in front is the helper's to count and block - unless main's last app is still
        // on screen too (split screen). A blocked video in picture-in-picture stays paused either way.
        // In the helper, only what its own profile has in front is its business (when it can tell, with usage access).
        if ((IslandLink.islandInFront() && !onScreen(pkg)) ||
            (IslandLink.isHelper && pkg != IslandLink.localFront && com.husarp.lockdown.usage.Usage.hasAccess(this))) {
            UsageStore.record(emptyList(), now); watchPip(cfg, now, mode)
            // main keeps the history for both profiles, and warns about the Island app in front
            val island = IslandLink.islandFront?.takeIf { !IslandLink.isHelper && IslandLink.islandInFront() }
            if (island != null) History.record(now, island, "", dt)
            inUseIds = setOfNotNull(island?.let { p -> cfg.items.firstOrNull { it.type == ItemType.APP && it.target.equals(p, true) }?.id })
            // the helper can't grey the screen: main greys it while an Island app that greys is in front (not
            // Lockdown itself over it), or while one of main's that greys is still on screen (picture-in-picture)
            Grayscale.app(this, (island != null && Enforce.app(cfg, island, now, mode)?.grey == true && !ReminderRunner.appVisible) ||
                greyOnScreen(cfg, now, mode))
            if (quietUp) removeOverlay()
            return
        }

        // The address bar is hidden in a full-screen video: keep the last address read, don't lose the site.
        if (isBrowser(pkg)) readBar(pkg)
        val host = if (isBrowser(pkg)) Browsers.hostOf(pkg, lastUrl[pkg]) else null
        val appItem = cfg.items.firstOrNull { it.type == ItemType.APP && it.target.equals(pkg, true) }
        val siteItem = host?.let { h -> cfg.items.firstOrNull { it.type == ItemType.SITE && !it.disabled && Enforce.hostMatches(it.target, h) } }
        inUseIds = setOfNotNull(appItem?.takeIf { !it.disabled }?.id, siteItem?.id)

        // Count what's in use - but not an item that is blocked: its time behind the notice and its retries
        // aren't use (they used to fill its limits and the group's). The opening that goes over a limit counts.
        // An app that is only greyed is in use: it counts.
        val actives = ArrayList<Active>()
        if (appItem != null && !appItem.disabled && Enforce.counts(cfg, appItem, now))
            actives.add(Active(appItem, Rules.countedRules(appItem, cfg.groups), launched = pkg != prevTickPkg))
        if (siteItem != null && !Enforce.itemBlocked(cfg, siteItem, now))
            actives.add(Active(siteItem, Rules.countedRules(siteItem, cfg.groups)))
        UsageStore.record(actives, now)
        prevTickPkg = pkg

        val label = if (appItem != null) appItem.name else Apps.label(packageManager, pkg)
        // "Grey this app": it stays open and the screen goes grey while it's in front (a site in it is still judged),
        // and while any window of it is on screen: split screen, a floating window, its video in picture-in-picture.
        // Lockdown itself in front isn't the greyed app (its windows don't move currentPkg).
        val appVerdict = Enforce.app(cfg, pkg, now, mode, label)
        val grey = appVerdict?.grey == true
        Grayscale.app(this, (grey && !ReminderRunner.appVisible) || greyOnScreen(cfg, now, mode))
        var verdict = appVerdict?.takeUnless { grey }
            ?: (if (host != null) Enforce.site(cfg, host, now, mode) else null)
        // Protection lists block in the browser without the VPN. Never gated by the emergency unlock.
        if (verdict == null && host != null && cfg.enabled && Protection.blocked(host))
            verdict = Verdict(host, "Blocked site", "$host is on a protection list.", null, "back", null, site = true, reason = "protection")
        val key = "$pkg|${host ?: ""}"
        // The history is what was used: not a blocked try (it's logged as one), not Lockdown itself in front.
        if (!IslandLink.isHelper && pkg.isNotEmpty()) {
            // (a site in a private tab isn't recorded: the browser is, the site isn't)
            if (verdict == null && !ReminderRunner.appVisible) History.record(now, pkg, host?.takeUnless { pkg in privateTabs } ?: "", dt) else History.idle(now, dt)
        }
        if (verdict != null) block(verdict, pkg, key)
        else { allowedKey = key; if (quietUp) removeOverlay() }   // what's in front is allowed: a quiet notice has done its job

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
        // A fresh try: the notice explains it (the blocked-visit alert) unless alerts are off for its reason or the
        // item, or it was explained within the cooldown - then it's a quiet notice. The block itself is the same.
        val cfg = Store.config
        val now = Enforce.ldt(TrustedTime.now(this))
        var loud = true
        if (!(overlay != null && blockUp && shownItemId == v.itemId)) {
            val item = v.itemId?.let { id -> cfg.items.firstOrNull { it.id == id } }
            val noticeKey = v.itemId ?: "${v.reason}:${v.name}"
            val ms = SystemClock.elapsedRealtime()
            if (v.reason.isNotEmpty()) {
                loud = Alerts.shouldNotify(item?.notify, Alerts.enabled(cfg.alerts, v.reason), lastNotice[noticeKey], ms, cfg.alerts.cooldownMin)
                if (loud) lastNotice[noticeKey] = ms
            }
            if (!IslandLink.isHelper) History.blocked(now, item?.id ?: "", item?.target?.substringBefore(' ') ?: if (v.site) v.name else pkg)
        } else loud = !quietUp
        shownApp = pkg.takeUnless { v.site }
        showBlock(v, homeOnOk = !navigates, loud = loud, sub = if (loud) Alerts.ownMessage(cfg.alerts, v.reason)
            ?.let { Alerts.format(it, v.name, v.reason, v.until, now) } ?: v.sub else null)
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
        if (v.site && "back" !in flags && navigates) { lastUrl.remove(pkg); privateTabs.remove(pkg) }
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
            if (w.id in pipCaught || justBlocked || Enforce.app(cfg, p, now, mode)?.takeUnless { it.grey } != null) caught.add(w.id)
        }
        pipCaught.clear(); pipCaught.addAll(caught)
        if (caught.isNotEmpty() && Media.playing(this)) Media.pause(this)
    }

    /** An app that greys is on screen (any window). Nothing greys in most setups: then the windows aren't even listed
     *  (that asks every visible app for its root, every step). */
    private fun greyOnScreen(cfg: Config, now: LocalDateTime, mode: ModeState?): Boolean =
        Enforce.greyPossible(cfg) && Enforce.greyOnScreen(cfg, appsOnScreen(), now, mode)

    /** The apps with a window on screen, picture-in-picture included (this profile's windows only; not Lockdown). */
    private fun appsOnScreen(): List<String> {
        val ws = runCatching { windows }.getOrNull() ?: return emptyList()
        return ws.filter { it.type == AccessibilityWindowInfo.TYPE_APPLICATION || it.isInPictureInPictureMode }
            .mapNotNull { w -> runCatching { w.root?.packageName?.toString() }.getOrNull() }
            .filter { it != packageName }
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

    /** The screen is on (locked or not): the loops keep their quick pace. Off: they stretch ([Cadence]). When it can't
     *  be told it counts as on, so a failure never slows blocking (or ends a loop). */
    private fun screenOn(): Boolean =
        runCatching { (getSystemService(POWER_SERVICE) as android.os.PowerManager).isInteractive }.getOrDefault(true)

    /** Reads [pkg]'s address bar. The tab is looked at for a private mark when the browser has just come to the front
     *  ([fresh]) or the address changed - not on every step. */
    private fun readBar(pkg: String, fresh: Boolean = false) {
        val root = rootInActiveWindow
        val text = browserBarText(pkg, root) ?: return
        if (fresh || text != lastUrl[pkg]) markPrivate(pkg, root)
        lastUrl[pkg] = text
    }

    /** Does [pkg]'s toolbar say the tab in front is private (incognito, InPrivate)? Best effort: a browser that says
     *  nothing in its toolbar can't be told, and its tab counts as an ordinary one. */
    private fun markPrivate(pkg: String, root: AccessibilityNodeInfo?) {
        val marked = runCatching { Browsers.privateMark(toolbarTexts(pkg, root)) }.getOrDefault(false)
        if (marked) privateTabs.add(pkg) else privateTabs.remove(pkg)
    }

    /** The text and descriptions around [pkg]'s address bar - its toolbar, a few levels down from a few up, never the
     *  page and never the box being typed in. */
    private fun toolbarTexts(pkg: String, root: AccessibilityNodeInfo?): List<CharSequence?> {
        root ?: return emptyList()
        val bar = Browsers.barIds(pkg).firstNotNullOfOrNull { id ->
            runCatching { root.findAccessibilityNodeInfosByViewId(id) }.getOrNull()?.firstOrNull()
        } ?: return emptyList()
        var top = bar
        for (i in 0 until Browsers.TOOLBAR_LEVELS) top = top.parent ?: break
        val out = ArrayList<CharSequence?>()
        val queue = ArrayDeque<Pair<AccessibilityNodeInfo, Int>>()
        queue.add(top to 0)
        var seen = 0
        while (queue.isNotEmpty() && seen < Browsers.TOOLBAR_NODES) {
            val (n, depth) = queue.removeFirst()
            seen++
            if (n.className == "android.webkit.WebView" || n.isEditable) continue
            out.add(n.contentDescription); out.add(n.text)
            if (depth < Browsers.TOOLBAR_DEPTH) for (i in 0 until n.childCount) n.getChild(i)?.let { queue.add(it to depth + 1) }
        }
        return out
    }

    // Everything here is wrapped: an uncaught exception in an accessibility callback makes Android DISABLE the
    // service (the "it keeps losing the grant" symptom). Nothing this service does is worth that.
    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        runCatching {
            val pkg = event.packageName?.toString() ?: return@runCatching
            IslandLink.saw(pkg)                 // Island link: does this service see Island apps too? (IslandSight)
            if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED && breakDismissed) trackBreakFront(pkg)
            if (pkg == packageName || pkg == "com.android.systemui" || isKeyboard(pkg)) return@runCatching
            if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
                val at = SystemClock.elapsedRealtime()
                debounce.otherFront(pkg, at)
                if (debounce.leftover(pkg, at)) return@runCatching     // the dismissed app closing, not opened again
                currentPkg = pkg
                IslandLink.islandFront = null                   // a main-profile window came to the front
                if (isBrowser(pkg)) readBar(pkg, fresh = true)
                handler.post { runCatching { step() }; runCatching { syncRemindHidden() }; speedUpSteps() }   // react immediately
            }
            if (!isBrowser(pkg)) return@runCatching

            // The address changes without a window change (a link, back): keep it fresh, keywords on or off.
            val ms = System.currentTimeMillis()
            if (ms - lastBarRead < 400) return@runCatching
            lastBarRead = ms
            val root = rootInActiveWindow      // (one fetch for the address bar and the box being typed in)
            val text = browserBarText(pkg, root)
            if (text != null) {
                // a new site in the same tab (a link, back) isn't a window change: judge it now, not on the next tick
                val newSite = Browsers.hostOf(pkg, text) != Browsers.hostOf(pkg, lastUrl[pkg])
                if (text != lastUrl[pkg]) markPrivate(pkg, root)     // (a tab switch can change private / not)
                lastUrl[pkg] = text
                if (newSite) handler.post { runCatching { step() } }
            }
            // keyword blocking: watch the address / search bar - and the box being typed in (DuckDuckGo's search /
            // Duck.ai box floats over its address bar) - and block on a match
            val cfg = Store.config
            if (cfg.keywords.enabled) {
                val hit = Keywords.hit(cfg, text) ?: Keywords.hit(cfg, typedText(pkg, root))
                if (hit != null) block(Verdict(hit, "Blocked search", "\"$hit\" is on your blocked words.", null, "back", null, site = true), pkg, "$pkg|kw")
            }
        }
    }

    private var keyboards: Set<String> = emptySet()
    private var keyboardsAt = -KEYBOARDS_MS

    /** [pkg] is an enabled keyboard: its window coming up isn't a new app in front. */
    private fun isKeyboard(pkg: String): Boolean {
        val ms = SystemClock.elapsedRealtime()
        if (ms - keyboardsAt >= KEYBOARDS_MS) {
            keyboardsAt = ms
            keyboards = runCatching { (getSystemService(INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager)
                .enabledInputMethodList.map { it.packageName }.toSet() }.getOrDefault(keyboards)
        }
        return pkg in keyboards
    }

    private fun browserBarText(pkg: String, root: AccessibilityNodeInfo? = rootInActiveWindow): String? {
        root ?: return null
        for (id in Browsers.barIds(pkg)) {
            val node = runCatching { root.findAccessibilityNodeInfosByViewId(id) }.getOrNull()?.firstOrNull() ?: continue
            if (node.isShowingHintText) continue          // an empty box reads as its hint ("Search"): try the next
            node.text?.let { return it.toString() }
        }
        return null
    }

    /** The text in [pkg]'s box being typed in, if it's [pkg]'s and not just its hint. */
    private fun typedText(pkg: String, root: AccessibilityNodeInfo? = rootInActiveWindow): String? {
        val node = runCatching { root?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) }.getOrNull() ?: return null
        if (node.packageName?.toString() != pkg || node.isShowingHintText) return null
        return node.text?.toString()
    }

    // ---------- overlay ----------

    /** The block notice. [sub] null: a quiet one (no explanation), which goes by itself once what's in front is
     *  allowed - after Home or Back that's at once. Either way OK does what the block does. */
    private fun showBlock(v: Verdict, homeOnOk: Boolean, loud: Boolean = true, sub: String? = v.sub) {
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
        if (loud && sub != null) col.addView(TextView(this).apply {
            text = sub; textSize = 16f; setTextColor(muted); setPadding(0, 24, 0, 0)
        })
        col.addView(Button(this).apply {
            text = "OK"; setTextColor(Color.parseColor("#5C1900"))
            setBackgroundColor(accent)
            setOnClickListener {
                val home = this@BlockService.homeOnOk
                shownApp?.let { debounce.dismissed(it, SystemClock.elapsedRealtime()) }
                removeOverlay(); if (home) { lastUrl.remove(currentPkg); performGlobalAction(GLOBAL_ACTION_HOME) } }
            val lp = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 130); lp.topMargin = 48
            layoutParams = lp
        })
        root.addView(col, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        addOverlay(root)
        blockUp = overlay === root
        quietUp = blockUp && !loud
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
        overlay = null; shownItemId = null; blockUp = false; quietUp = false
    }

    // ---------- reminders ----------

    /** Every few seconds: the reminders engine. "Idle" is how long the screen has been off or locked. */
    private val remindTick = object : Runnable {
        override fun run() {
            runCatching {
                val ms = SystemClock.elapsedRealtime()
                val inUse = screenInUse()
                // Unlocked after a while (the night): bedtime grayscale right away, not up to a minute later
                if (inUse && ms - lastInUse > 15_000) runCatching { Grayscale.sync(this@BlockService) }
                if (inUse) lastInUse = ms
                val dt = ((ms - lastRemindTick) / 1000.0).coerceIn(0.0, 15.0)   // (a long gap was the phone asleep)
                lastRemindTick = ms
                ReminderRunner.tick(this@BlockService, (ms - lastInUse) / 1000.0, dt)
            }
            runCatching { if (breakDismissed) refreshBreakFront() }
            runCatching { syncRemindHidden() }
            handler.postDelayed(this, Cadence.remindMs(screenOn(), (SystemClock.elapsedRealtime() - lastInUse) / 1000.0))
        }
    }

    /** The bedtime screen: Dismiss, Disable alerts (the challenge, in the app) and Emergency (N left). Also a break
     *  or an important reminder when notifications are off (its own buttons). */
    override fun show(p: RPrompt) {
        val kind = when (p.key) { "sleep" -> CoverLook.Kind.SLEEP; "break" -> CoverLook.Kind.BREAK; else -> CoverLook.Kind.REMIND }
        val built = cover.build(kind, p.title, p.text, heroFirst = true)
        val col = built.root
        val buttons = built.buttons
        // bedtime shows the clock on top, as the PC's cover (redrawn once a minute, only while it is on screen)
        if (kind == CoverLook.Kind.SLEEP) cover.ticking(built.hero, handler) {
            val t = ReminderRunner.now(this)
            "%02d:%02d".format(t.hour, t.minute) to 60_000L - (t.second * 1000L + t.nano / 1_000_000) + 20
        }
        // "Wait before Dismiss": Dismiss counts down and can't be tapped until it ends, from the start each time the
        // screen comes up (also back after a call or the lock screen). Disable alerts and Emergency don't wait.
        var readyAt = SystemClock.elapsedRealtime() + p.wait * 1000L
        var dismiss: Button? = null
        var shownLeft = -1      // the number on the button now: it is only touched (and redrawn) when it changes
        val countdown = object : Runnable {
            override fun run() {
                if (p.wait <= 0) return
                val now = SystemClock.elapsedRealtime()
                val left = RemindersEngine.waitLeft(readyAt, now)
                if (left != shownLeft) {
                    shownLeft = left
                    dismiss?.apply { text = RemindersEngine.dismissLabel(left); isEnabled = left == 0; alpha = if (left == 0) 1f else CoverLook.DISABLED_ALPHA }
                }
                handler.removeCallbacks(this)
                if (left > 0) handler.postDelayed(this, Cadence.countdownMs(readyAt, now))
            }
        }
        col.addOnAttachStateChangeListener(object : View.OnAttachStateChangeListener {
            override fun onViewAttachedToWindow(v: View) { readyAt = SystemClock.elapsedRealtime() + p.wait * 1000L; shownLeft = -1; countdown.run() }
            override fun onViewDetachedFromWindow(v: View) { handler.removeCallbacks(countdown) }
        })
        fun fill() {
            buttons.removeAllViews()
            p.buttons.forEachIndexed { i, b ->
                // as the PC's cover: Dismiss (first) filled, Disable alerts quiet, Emergency (N left) a text link
                val role = when {
                    b.action == "emergency" -> CoverLook.Role.EMERG
                    b.action == "disable" -> CoverLook.Role.GHOST
                    i == 0 -> CoverLook.Role.PRIMARY
                    else -> CoverLook.Role.PLAIN
                }
                buttons.addView(remindButton(if (b.action == "dismiss") "Dismiss" else b.label, role) {
                    when (b.action) {
                        "disable" -> {
                            val early = RemindersEngine.waitLeft(readyAt, SystemClock.elapsedRealtime()) > 0
                            closeRemind(p.key); ReminderRunner.askDisable(this, early)
                        }
                        "emergency" -> askEmergency(buttons, "Your bedtime settings stay as they are.", back = { fill() }) { tier ->
                            closeRemind(p.key)
                            ReminderRunner.answer(this, p.key, "dismiss")   // back on the escalation if it fails
                            ReminderRunner.spendEmergency(this, tier)
                        }
                        "dismiss" -> if (RemindersEngine.waitLeft(readyAt, SystemClock.elapsedRealtime()) == 0) {
                            closeRemind(p.key); ReminderRunner.answer(this, p.key, b.action)
                        }
                        else -> { closeRemind(p.key); ReminderRunner.answer(this, p.key, b.action) }
                    }
                }.also { if (b.action == "dismiss") dismiss = it })
            }
            shownLeft = -1      // new buttons: draw the number on them
            countdown.run()
        }
        fill()
        addRemind(p.key, col)
    }

    /** "Emergency": asks once (a stray tap must not spend a use), then [onPause] with the kind picked: one button
     *  per kind when there are several (a spent one greyed out). [back] redraws the buttons. */
    private fun askEmergency(buttons: LinearLayout, note: String, back: () -> Unit, onPause: (String) -> Unit) {
        val cfg = Store.config
        val now = ReminderRunner.now(this)
        val left = ReminderRunner.emergencyLeft(cfg, now)
        if (left <= 0) { Toast.makeText(this, "No emergency unlocks left.", Toast.LENGTH_LONG).show(); back(); return }
        val tiers = cfg.emergency.tierList()
        buttons.removeAllViews()
        buttons.addView(cover.note(
            if (tiers.size == 1) "Pause the bedtime and break alerts for ${tiers[0].minutes} min? Uses 1 of your " +
                "$left emergency unlock${if (left != 1) "s" else ""} left. $note"
            else "Pause the bedtime and break alerts? Pick how long - it uses 1 of that kind's emergency unlocks. $note"))
        if (tiers.size == 1) buttons.addView(remindButton("Pause ${tiers[0].minutes} min", CoverLook.Role.PRIMARY) { onPause(tiers[0].id) })
        else for (t in tiers) {
            val u = Emergency.usesLeft(cfg.allUses(), t, now, cfg.clock())
            buttons.addView(remindButton("Pause ${t.minutes} min · " +
                (if (u.left > 0) "${u.left} left" else "none left") + " ${Emergency.PERIODS[t.per]}",
                CoverLook.Role.PLAIN, enabled = u.left > 0) { onPause(t.id) })
        }
        buttons.addView(remindButton("Cancel", CoverLook.Role.GHOST) { back() })
    }

    override fun close(key: String) = closeRemind(key)

    /** A strict break: the screen is covered with "Break time" (the PC minimises every window). Dismiss is free: the
     *  cover goes and the phone goes home; it comes back only when an app that isn't allowed comes to the front
     *  (engine/BreakAllow.kt: the built-in set, the "Allowed during breaks" list, calls). Phone opens the dialer
     *  (calls always get through); an emergency use ends the break, as on the PC. */
    override fun startBreak(until: LocalDateTime) {
        val built = cover.build(CoverLook.Kind.BREAK, "Break time",
            "Step away from the screen. Back at %02d:%02d.".format(until.hour, until.minute), heroFirst = false)
        val col = built.root
        val buttons = built.buttons
        // the time left, under the title (as the PC's break): counted on the steady clock from now, each second on screen
        val endAt = SystemClock.elapsedRealtime() + java.time.Duration.between(ReminderRunner.now(this), until).toMillis()
        cover.ticking(built.hero, handler) { CoverLook.countdown(endAt) }
        fun fill() {
            buttons.removeAllViews()
            buttons.addView(remindButton("Dismiss", CoverLook.Role.PRIMARY) { dismissBreak() })
            buttons.addView(remindButton("Phone", CoverLook.Role.PLAIN) { dial() })
            val left = ReminderRunner.emergencyLeft(Store.config, ReminderRunner.now(this))
            if (left > 0) buttons.addView(remindButton("Emergency ($left left)", CoverLook.Role.EMERG) {
                askEmergency(buttons, "The break ends now.", back = { fill() }) { tier ->
                    if (ReminderRunner.spendEmergency(this, tier)) closeRemind(STRICT_BREAK) else fill()
                }
            })
        }
        fill()
        addRemind(STRICT_BREAK, col)
    }

    /** Dismiss on a strict break: the cover goes (the break keeps running) and the phone goes to the home screen. */
    private fun dismissBreak() {
        breakDismissed = true; breakCovered = false; breakFront = ""
        applyRemind()
        performGlobalAction(GLOBAL_ACTION_HOME)
    }

    /** After a dismiss: [pkg] came to the front (a window change). Lockdown's own overlay events, the status bar and the
     *  keyboard aren't an app in front, and a late event from the app that was just left isn't either (the active
     *  window says what really is). */
    private fun trackBreakFront(pkg: String) {
        if (pkg == "com.android.systemui" || isKeyboard(pkg)) return
        val active = activeApp()
        if (pkg == packageName) { if (active != packageName) return }
        else if (active != null && active != pkg && active != "com.android.systemui" && !isKeyboard(active)) return
        breakFront = pkg
        handler.post { runCatching { syncRemindHidden() } }
    }

    /** After a dismiss, on the slow tick: what the active window is now (a missed window event can't leave the break
     *  uncovered for a not-allowed app). */
    private fun refreshBreakFront() {
        val active = activeApp() ?: return
        if (active != "com.android.systemui" && !isKeyboard(active)) breakFront = active
    }

    /** The active window's package. A reminder screen that blocks taps is focusable, so while one is up it can be the
     *  active window itself: that says nothing about the app in front (null, as when it can't be told). */
    private fun activeApp(): String? = runCatching { rootInActiveWindow?.packageName?.toString() }.getOrNull()
        ?.takeUnless { it == packageName && onScreenKeys.isNotEmpty() }

    /** "Phone": the dialer, with the reminder screens out of its way while it's in front. */
    private fun dial() {
        dialing = true; dialUntil = SystemClock.elapsedRealtime() + DIAL_GRACE_MS
        syncRemindHidden()
        runCatching { startActivity(Intent(Intent.ACTION_DIAL).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
    }

    /** The bedtime screen and a strict break never cover a call: they step aside while the phone rings or a call
     *  is on, while the dialer is in front after "Phone", and on the lock screen (its emergency call stays
     *  reachable; nothing can be used there anyway). They come back after; the engine keeps them running. */
    private fun syncRemindHidden() {
        val am = getSystemService(AUDIO_SERVICE) as AudioManager
        val call = am.mode == AudioManager.MODE_RINGTONE || am.mode == AudioManager.MODE_IN_CALL ||
            am.mode == AudioManager.MODE_IN_COMMUNICATION
        val ms = SystemClock.elapsedRealtime()
        if (dialing && ms >= dialUntil && !isDialer(currentPkg)) dialing = false
        val locked = (getSystemService(KEYGUARD_SERVICE) as android.app.KeyguardManager).isKeyguardLocked
        remindHidden = call || dialing || locked
        // a dismissed break covers again only for an app that isn't allowed (a call is always allowed)
        if (breakDismissed && STRICT_BREAK in remindViews)
            breakCovered = BreakAllow.covered(true, breakFront, BreakApps.builtIn(this), Store.config.breaks.allowed, call)
        applyRemind()
    }

    /** Adds or removes each reminder view to match what it should do now ([remindHidden], the break's cover). */
    private fun applyRemind() {
        for ((key, v) in remindViews.entries.toList()) {
            val want = !remindHidden && (key != STRICT_BREAK || breakCovered)
            if (want && key !in onScreenKeys) { if (runCatching { wm?.addView(v, remindParams(Store.config.sleep.blockBehind)) }.isSuccess) onScreenKeys.add(key) }
            else if (!want && onScreenKeys.remove(key)) runCatching { wm?.removeView(v) }
        }
    }

    private fun isDialer(pkg: String): Boolean {
        val dialer = runCatching { (getSystemService(TELECOM_SERVICE) as android.telecom.TelecomManager).defaultDialerPackage }.getOrNull()
        return pkg == dialer || pkg in BreakAllow.CALL_PKGS
    }

    override fun endBreak() = closeRemind(STRICT_BREAK)

    private fun remindButton(label: String, role: CoverLook.Role, enabled: Boolean = true, onClick: () -> Unit) =
        cover.button(label, role, enabled).apply { setOnClickListener { runCatching(onClick) } }

    private fun addRemind(key: String, col: View) {
        closeRemind(key)
        val root = FrameLayout(this).apply { background = cover.backdrop() }
        // scrolls when it doesn't fit (several emergency kinds on a small screen or with a large font), centred when it does
        val scroll = ScrollView(this).apply {
            isFillViewport = true
            addView(col, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.CENTER_HORIZONTAL))
        }
        root.addView(scroll, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        remindViews[key] = root
        if (key == STRICT_BREAK) { breakDismissed = false; breakCovered = true }
        applyRemind()
        if (key !in onScreenKeys && !remindHidden && (key != STRICT_BREAK || breakCovered)) remindViews.remove(key)   // couldn't be added
    }

    /** The bedtime / strict-break screen's window. Not [block]: as it always was - not focusable, so Back and the
     *  keyboard still reach the app under it, and laid out inside the system bars, so the strip under gesture
     *  navigation (and a display cutout) is left to the app under it, taps included. [block] ("Block taps behind the
     *  bedtime screen"): focusable, so Back and keys stop here, and over everything but what can be tapped of the
     *  system bars (the status bar, 3-button navigation) - those still get their taps (not touch-modal), and the
     *  swipes of gesture navigation are the system's own, never this window's. */
    private fun remindParams(block: Boolean) = WindowManager.LayoutParams(
        WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        if (block) WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
        else WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
        PixelFormat.OPAQUE,
    ).apply {
        if (!block) return@apply
        if (Build.VERSION.SDK_INT >= 30) {
            fitInsetsTypes = WindowInsets.Type.tappableElement()
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
        } else if (Build.VERSION.SDK_INT >= 28) layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
    }

    private fun closeRemind(key: String) {
        onScreenKeys.remove(key)
        remindViews.remove(key)?.let { runCatching { wm?.removeView(it) } }   // (not on screen while hidden: fails, harmlessly)
        if (key == STRICT_BREAK) { breakDismissed = false; breakCovered = false; breakFront = "" }
    }

    // ---------- bedtime grayscale ----------

    /** Every minute: bedtime grayscale on/off (colour back within a minute of wake time, not up to 15+), and once
     *  a day dropping usage counters that can no longer matter. */
    private val bedtimeTick = object : Runnable {
        override fun run() {
            runCatching {
                val now = Enforce.ldt(TrustedTime.now(this@BlockService))
                if (prunedOn != now.toLocalDate()) { prunedOn = now.toLocalDate(); UsageStore.prune(now); History.retain(now.toLocalDate()) }
                warnPauseEnding(now)
            }
            runCatching { Grayscale.sync(this@BlockService) }
            // Island helper stopped? (screen-on time only; costs a flag check with the screen off or no link)
            runCatching { if (!IslandLink.isHelper) HelperWatcher.look(this@BlockService, screenOn()) }
            handler.postDelayed(this, Cadence.bedtimeMs(screenOn()))
        }
    }

    /** A few minutes before "Pause my blocks" ends (unless it silenced the alerts): "Your blocks are back on in 4 min".
     *  Main says it for both profiles. */
    private fun warnPauseEnding(now: LocalDateTime) {
        val p = Store.config.pause ?: return
        val until = com.husarp.lockdown.engine.Pause.until(p, now) ?: return
        val left = java.time.Duration.between(now, until).toMinutes()
        if (p.silent || IslandLink.isHelper || left >= 5 || pauseWarned == p.until) return
        pauseWarned = p.until
        Toast.makeText(this, "Your blocks are back on in ${maxOf(1, left)} min (%02d:%02d).".format(until.hour, until.minute),
            Toast.LENGTH_LONG).show()
    }

    // ---------- warnings and "block started" ----------

    /** Every few seconds (main only, for both profiles): warnings before blocks, "block started", allowance notes.
     *  Held while a pause silences the alerts; a mode that mutes lets through only what's about the thing in front. */
    private val alertTick = object : Runnable {
        override fun run() {
            runCatching { syncDnd() }
            runCatching { watchBlocks() }
            handler.postDelayed(this, Cadence.alertMs(screenOn()))
        }
    }

    /** Is a mode that mutes running (and blocking on)? Main only. */
    private fun muteWanted(now: LocalDateTime): Boolean =
        !IslandLink.isHelper && Store.config.enabled && ModesStore.current(now)?.mode?.mute == true

    /** Do Not Disturb follows a muting mode (engine/MuteDnd.kt). Only main does it (it is the whole phone's); what it
     *  changed is remembered in prefs, so a mode that ended while the app was dead still puts the user's setting back. */
    private fun syncDnd(release: Boolean = false) {
        if (IslandLink.isHelper) return
        val p = getSharedPreferences("mute-dnd", MODE_PRIVATE)
        val saved = com.husarp.lockdown.engine.MuteDnd.decode(p.getString("saved", null))
        val nm = getSystemService(android.app.NotificationManager::class.java)
        val now = Enforce.ldt(TrustedTime.now(this))
        val step = com.husarp.lockdown.engine.MuteDnd.decide(!release && muteWanted(now), nm.isNotificationPolicyAccessGranted,
            nm.currentInterruptionFilter, saved)
        step.filter?.let { nm.setInterruptionFilter(it) }
        if (step.saved != saved) p.edit().putString("saved", com.husarp.lockdown.engine.MuteDnd.encode(step.saved)).apply()
    }

    private fun watchBlocks() {
        val cfg = Store.config
        // switched off (or the helper): forget what was blocked, so switching back on doesn't announce every rule
        if (IslandLink.isHelper || !cfg.enabled) { watcher.prevBlocked = null; return }
        val now = Enforce.ldt(TrustedTime.now(this))
        val messages = watcher.check(cfg.items, cfg.groups, UsageStore.usage, cfg.clock(), now, inUseIds, cfg.alerts,
            Enforce.paused(cfg, now)) { Enforce.emergencyUntil(cfg, it, now) }
        if (messages.isEmpty() || Enforce.silenced(cfg, now)) return
        val muted = ModesStore.current(now)?.mode?.mute == true
        for (m in messages) if (!muted || m in watcher.urgent) postAlert(m, m in watcher.urgent)
    }

    private fun postAlert(text: String, urgent: Boolean) {
        val nm = getSystemService(android.app.NotificationManager::class.java)
        val channel = if (urgent) "alerts-now" else "alerts"
        if (nm.getNotificationChannel(channel) == null) nm.createNotificationChannel(android.app.NotificationChannel(channel,
            if (urgent) "Blocks on what you're using" else "Block warnings",
            if (urgent) android.app.NotificationManager.IMPORTANCE_HIGH else android.app.NotificationManager.IMPORTANCE_DEFAULT))
        if (android.os.Build.VERSION.SDK_INT >= 33 && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) !=
            android.content.pm.PackageManager.PERMISSION_GRANTED) return
        val n = androidx.core.app.NotificationCompat.Builder(this, channel)
            .setSmallIcon(com.husarp.lockdown.R.drawable.ic_launcher_foreground)
            .setContentTitle("Lockdown").setContentText(text)
            .setStyle(androidx.core.app.NotificationCompat.BigTextStyle().bigText(text))
            .setAutoCancel(true).build()
        nm.notify("alert", ALERT_ID + (alertSeq++ % 20), n)
    }

    override fun onInterrupt() {}
    override fun onUnbind(intent: Intent?): Boolean { connected = false; return super.onUnbind(intent) }
    override fun onDestroy() {
        connected = false; removeOverlay()
        runCatching { unregisterReceiver(screenReceiver) }
        runCatching { unregisterReceiver(packageReceiver) }
        runCatching { syncDnd(release = true) }        // the service going away puts Do Not Disturb back if we set it
        runCatching { Grayscale.app(this, false) }     // nothing watches what's in front any more: colour back
        // The bedtime screen goes with the service: count it as dismissed, so it comes back on its escalation.
        if ("sleep" in remindViews) runCatching { ReminderRunner.answer(this, "sleep", "dismiss") }
        for (key in remindViews.keys.toList()) closeRemind(key)
        if (ReminderRunner.screen === this) ReminderRunner.screen = null
        handler.removeCallbacksAndMessages(null); super.onDestroy()
    }

    companion object {
        private const val ALERT_ID = 60
        private const val KEYBOARDS_MS = 60_000L    // the enabled keyboards are looked up again after this long
        private const val ACT_GAP_MS = 2000L        // act on the same block at most this often (Back / Home)
        private const val PIP_CATCH_MS = 15_000L    // a PiP window this soon after its app's block is the blocked video
        private const val STRICT_BREAK = "strict-break"
        private const val DIAL_GRACE_MS = 10_000L   // after "Phone", the dialer has this long to come to the front

        /** The service really runs (bound by Android), not just ticked in Settings. Same process as its readers. */
        @Volatile var connected = false
            private set

        /** Turned on in Android's accessibility settings (full or short name); [connected] says it really runs. */
        fun isEnabled(ctx: Context): Boolean = com.husarp.lockdown.engine.PermCheck.a11yListed(
            Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES),
            ctx.packageName, BlockService::class.java.name)

        fun settingsIntent() = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)

        private fun isBrowser(pkg: String) = Browsers.isBrowser(pkg)
    }
}
