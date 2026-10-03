package com.husarp.lockdown.block

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.media.AudioManager
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
import android.widget.Toast
import com.husarp.lockdown.data.History
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.data.UsageStore
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.engine.Active
import com.husarp.lockdown.engine.Alerts
import com.husarp.lockdown.engine.BlockWatcher
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.ModeState
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.RPrompt
import com.husarp.lockdown.engine.RemindersEngine
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.link.IslandLink
import com.husarp.lockdown.remind.Grayscale
import com.husarp.lockdown.remind.ReminderRunner
import java.time.LocalDate
import java.time.LocalDateTime

/**
 * Watches the foreground app / browser tab. It feeds the usage engine (so time and opening limits count),
 * asks the rule engine (plus the active mode) whether to block, and when it should: pauses a video that was
 * playing, leaves the app / goes back in the browser (the item's block method) and covers the screen with a
 * notice until OK. It also stops a blocked video carrying on in picture-in-picture, blocks bad keywords in
 * browsers, and runs the reminders (bedtime screen, breaks, your own reminders) and bedtime grayscale.
 */
class BlockService : AccessibilityService(), ReminderRunner.Screen {

    private var overlay: View? = null
    private var wm: WindowManager? = null
    private val handler = android.os.Handler(android.os.Looper.getMainLooper())

    @Volatile private var currentPkg = ""
    private val lastUrl = HashMap<String, String>()   // browser -> last address read (kept while the bar is hidden)
    private var prevTickPkg = ""
    private var lastBarRead = 0L
    private var lastPausePkg = ""
    private var lastPauseAt = 0L
    private val remindViews = HashMap<String, View>() // full-screen reminder prompts on screen: "sleep", "strict-break"
    private var remindHidden = false                  // they're out of the way: a call, the dialer, the lock screen
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
    private var alertSeq = 0

    override fun onServiceConnected() {
        connected = true
        wm = getSystemService(WINDOW_SERVICE) as WindowManager
        // see every window (picture-in-picture too); also set in the config, this covers a service bound before the update
        runCatching { serviceInfo = serviceInfo.apply { flags = flags or android.accessibilityservice.AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS } }
        runCatching { Protection.load(this) }   // so protection lists block in the browser even without the VPN
        handler.post(tick)
        handler.post(bedtimeTick)
        ReminderRunner.screen = this
        lastInUse = SystemClock.elapsedRealtime(); lastRemindTick = lastInUse
        handler.post(remindTick)
        lastHistMs = lastInUse
        handler.postDelayed(alertTick, ALERT_MS)
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
        val ms = SystemClock.elapsedRealtime()
        val dt = ((ms - lastHistMs) / 1000.0).coerceIn(0.0, 10.0)   // (a long gap was the phone asleep)
        lastHistMs = ms
        // Locked or screen off: nothing is being used (audio holding the phone awake used to keep counting).
        if (!screenInUse()) {
            UsageStore.record(emptyList(), now); inUseIds = emptySet()
            if (!IslandLink.isHelper) History.idle(now)
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
            if (quietUp) removeOverlay()
            return
        }

        // The address bar is hidden in a full-screen video: keep the last address read, don't lose the site.
        if (isBrowser(pkg)) readBar(pkg)
        val host = if (isBrowser(pkg)) hostOf(lastUrl[pkg]) else null
        val appItem = cfg.items.firstOrNull { it.type == ItemType.APP && it.target.equals(pkg, true) }
        val siteItem = host?.let { h -> cfg.items.firstOrNull { it.type == ItemType.SITE && !it.disabled && Enforce.hostMatches(it.target, h) } }
        inUseIds = setOfNotNull(appItem?.takeIf { !it.disabled }?.id, siteItem?.id)

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
            verdict = Verdict(host, "Blocked site", "$host is on a protection list.", null, "back", null, site = true, reason = "protection")
        val key = "$pkg|${host ?: ""}"
        // The history is what was used: not a blocked try (it's logged as one), not Lockdown itself in front.
        if (!IslandLink.isHelper && pkg.isNotEmpty()) {
            if (verdict == null && !ReminderRunner.appVisible) History.record(now, pkg, host ?: "", dt) else History.idle(now)
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
            if (pkg == packageName || pkg == "com.android.systemui" || isKeyboard(pkg)) return@runCatching
            if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
                currentPkg = pkg
                IslandLink.islandFront = null                   // a main-profile window came to the front
                if (isBrowser(pkg)) readBar(pkg)
                handler.post { runCatching { step() }; runCatching { syncRemindHidden() } }   // react immediately
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

    private fun browserBarText(): String? {
        val root = rootInActiveWindow ?: return null
        for (id in URL_BAR_IDS) {
            val nodes = runCatching { root.findAccessibilityNodeInfosByViewId(id) }.getOrNull() ?: continue
            nodes.firstOrNull()?.text?.let { return it.toString() }
        }
        return null
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
            setOnClickListener { val home = this@BlockService.homeOnOk; removeOverlay(); if (home) { lastUrl.remove(currentPkg); performGlobalAction(GLOBAL_ACTION_HOME) } }
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
                if (screenInUse()) lastInUse = ms
                val dt = ((ms - lastRemindTick) / 1000.0).coerceIn(0.0, 15.0)   // (a long gap was the phone asleep)
                lastRemindTick = ms
                ReminderRunner.tick(this@BlockService, (ms - lastInUse) / 1000.0, dt)
            }
            runCatching { syncRemindHidden() }
            handler.postDelayed(this, RemindersEngine.TICK_SEC * 1000L)
        }
    }

    /** The bedtime screen: Dismiss, Disable alerts (the challenge, in the app) and Emergency (N left). Also a break
     *  or an important reminder when notifications are off (its own buttons). */
    override fun show(p: RPrompt) {
        val col = remindColumn(p.title, p.text)
        val buttons = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        fun fill() {
            buttons.removeAllViews()
            p.buttons.forEachIndexed { i, b ->
                buttons.addView(remindButton(if (b.action == "dismiss") "Dismiss" else b.label, primary = i == 0) {
                    when (b.action) {
                        "disable" -> { closeRemind(p.key); ReminderRunner.askDisable(this) }
                        "emergency" -> askEmergency(buttons, "Your bedtime settings stay as they are.", back = { fill() }) {
                            closeRemind(p.key)
                            ReminderRunner.answer(this, p.key, "dismiss")   // back on the escalation if it fails
                            ReminderRunner.spendEmergency(this)
                        }
                        else -> { closeRemind(p.key); ReminderRunner.answer(this, p.key, b.action) }
                    }
                })
            }
        }
        fill()
        col.addView(buttons)
        addRemind(p.key, col)
    }

    /** "Emergency": asks once (a stray tap must not spend a use), then [onPause]. [back] redraws the buttons. */
    private fun askEmergency(buttons: LinearLayout, note: String, back: () -> Unit, onPause: () -> Unit) {
        val cfg = Store.config
        val left = ReminderRunner.emergencyLeft(cfg, ReminderRunner.now(this))
        if (left <= 0) { Toast.makeText(this, "No emergency unlocks left.", Toast.LENGTH_LONG).show(); back(); return }
        buttons.removeAllViews()
        buttons.addView(TextView(this).apply {
            text = "Pause the bedtime and break alerts for ${cfg.emergency.minutes} min? Uses 1 of your " +
                "$left emergency unlock${if (left != 1) "s" else ""} left. $note"
            textSize = 15f; setTextColor(Color.parseColor("#D8C2BC")); setPadding(0, 32, 0, 0)
        })
        buttons.addView(remindButton("Pause ${cfg.emergency.minutes} min", primary = true) { onPause() })
        buttons.addView(remindButton("Cancel", primary = false) { back() })
    }

    override fun close(key: String) = closeRemind(key)

    /** A strict break: the screen is covered until it's over (the PC minimises every window). Phone opens the
     *  dialer (calls always get through); an emergency use ends it, as on the PC. */
    override fun startBreak(until: LocalDateTime) {
        val col = remindColumn("Break time", "Step away from the screen. Back at %02d:%02d.".format(until.hour, until.minute))
        val buttons = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        fun fill() {
            buttons.removeAllViews()
            buttons.addView(remindButton("Phone", primary = false) { dial() })
            val left = ReminderRunner.emergencyLeft(Store.config, ReminderRunner.now(this))
            if (left > 0) buttons.addView(remindButton("Emergency ($left left)", primary = false) {
                askEmergency(buttons, "The break ends now.", back = { fill() }) {
                    if (ReminderRunner.spendEmergency(this)) closeRemind(STRICT_BREAK) else fill()
                }
            })
        }
        fill()
        col.addView(buttons)
        addRemind(STRICT_BREAK, col)
    }

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
        val hide = call || dialing || locked
        if (hide == remindHidden) return
        remindHidden = hide
        for (v in remindViews.values) runCatching { if (hide) wm?.removeView(v) else wm?.addView(v, remindParams()) }
    }

    private fun isDialer(pkg: String): Boolean {
        val dialer = runCatching { (getSystemService(TELECOM_SERVICE) as android.telecom.TelecomManager).defaultDialerPackage }.getOrNull()
        return pkg == dialer || pkg in CALL_PKGS
    }

    override fun endBreak() = closeRemind(STRICT_BREAK)

    private fun remindColumn(title: String, body: String) = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER_VERTICAL
        setPadding(72, 72, 72, 72)
        addView(TextView(this@BlockService).apply {
            text = title; textSize = 30f; setTextColor(Color.parseColor("#F1DFDA"))
            typeface = Typeface.create("sans-serif-condensed", Typeface.BOLD)
        })
        addView(TextView(this@BlockService).apply {
            text = body; textSize = 16f; setTextColor(Color.parseColor("#D8C2BC")); setPadding(0, 24, 0, 0)
        })
    }

    private fun remindButton(label: String, primary: Boolean, onClick: () -> Unit) = Button(this).apply {
        text = label
        setTextColor(Color.parseColor(if (primary) "#5C1900" else "#FFB59C"))
        setBackgroundColor(Color.parseColor(if (primary) "#FFB59C" else "#3A2A26"))
        setOnClickListener { runCatching(onClick) }
        val lp = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 130); lp.topMargin = 32
        layoutParams = lp
    }

    private fun addRemind(key: String, col: View) {
        closeRemind(key)
        val root = FrameLayout(this).apply { setBackgroundColor(Color.parseColor("#1A110F")) }
        root.addView(col, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        remindViews[key] = root
        if (!remindHidden) runCatching { wm?.addView(root, remindParams()) }.onFailure { remindViews.remove(key) }
    }

    private fun remindParams() = WindowManager.LayoutParams(
        WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY, WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE, PixelFormat.OPAQUE,
    )

    private fun closeRemind(key: String) {
        // (not on screen while hidden: removing it then just fails, harmlessly)
        remindViews.remove(key)?.let { runCatching { wm?.removeView(it) } }
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
            handler.postDelayed(this, 60_000)
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
            runCatching { watchBlocks() }
            handler.postDelayed(this, ALERT_MS)
        }
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
        // The bedtime screen goes with the service: count it as dismissed, so it comes back on its escalation.
        if ("sleep" in remindViews) runCatching { ReminderRunner.answer(this, "sleep", "dismiss") }
        for (key in remindViews.keys.toList()) closeRemind(key)
        if (ReminderRunner.screen === this) ReminderRunner.screen = null
        handler.removeCallbacksAndMessages(null); super.onDestroy()
    }

    companion object {
        private const val TICK_MS = 3000L
        private const val ALERT_MS = 5000L          // warnings / "block started" checked this often (PC WATCH_MS)
        private const val ALERT_ID = 60
        private const val KEYBOARDS_MS = 60_000L    // the enabled keyboards are looked up again after this long
        private const val ACT_GAP_MS = 2000L        // act on the same block at most this often (Back / Home)
        private const val PIP_CATCH_MS = 15_000L    // a PiP window this soon after its app's block is the blocked video
        private const val STRICT_BREAK = "strict-break"
        private const val DIAL_GRACE_MS = 10_000L   // after "Phone", the dialer has this long to come to the front
        // in-call screens and the emergency dialer, besides the default dialer app
        private val CALL_PKGS = setOf("com.android.phone", "com.android.incallui", "com.samsung.android.incallui",
            "com.android.server.telecom")

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
