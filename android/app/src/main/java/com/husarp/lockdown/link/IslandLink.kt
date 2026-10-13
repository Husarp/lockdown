package com.husarp.lockdown.link

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.CrossProfileApps
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.os.SystemClock
import android.os.UserManager
import androidx.core.content.pm.PackageInfoCompat
import androidx.work.WorkManager
import com.husarp.lockdown.MainActivity
import com.husarp.lockdown.block.Apps
import com.husarp.lockdown.block.BlockService
import com.husarp.lockdown.block.Enforce
import com.husarp.lockdown.block.InstalledApp
import com.husarp.lockdown.block.Protection
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.data.UsageStore
import com.husarp.lockdown.engine.AppSpan
import com.husarp.lockdown.engine.AppTime
import com.husarp.lockdown.engine.IslandFront
import com.husarp.lockdown.engine.IslandHourRow
import com.husarp.lockdown.engine.IslandOpen
import com.husarp.lockdown.engine.IslandOpens
import com.husarp.lockdown.engine.IslandSight
import com.husarp.lockdown.engine.IslandUse
import com.husarp.lockdown.engine.IslandUseRow
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.remind.Grayscale
import com.husarp.lockdown.remind.Reminders
import com.husarp.lockdown.usage.Usage
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.File
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread

enum class Role { MAIN, HELPER, STANDALONE }

/**
 * Island link: Lockdown in the main profile keeps the rules; its copy in Island (the work profile) becomes a helper
 * that takes those rules, blocks Island apps by them, and sends its time back so limits are shared. The two
 * profiles share the loopback network, so main runs a tiny server on 127.0.0.1 - only while linked or pairing -
 * and the helper calls in every few seconds. Every message after pairing is signed (see [LinkProtocol]).
 * Unlinking is a loosening: it is started in main, behind the challenge; the helper can't unlink itself.
 * With no link, main opens no socket and nothing here runs.
 */
object IslandLink {
    private val PORTS = 38641..38650
    private val LOOPBACK: InetAddress = InetAddress.getByAddress(byteArrayOf(127, 0, 0, 1))
    private val json = LinkProtocol.json
    private val mainHandler = Handler(Looper.getMainLooper())
    private lateinit var app: Context
    private var isMain = true

    fun role(ctx: Context): Role = when {
        !Grayscale.otherProfile(ctx) -> Role.MAIN
        _helper.value -> Role.HELPER
        else -> Role.STANDALONE
    }

    /** This copy is a linked helper (shows the locked screen). */
    private val _helper = MutableStateFlow(false)
    val helper: StateFlow<Boolean> = _helper
    val isHelper get() = _helper.value

    fun init(ctx: Context) {
        app = ctx.applicationContext
        isMain = !Grayscale.otherProfile(app)
        if (isMain) {
            val p = prefs()
            _main.value = MainLink(p.getString("state", "") ?: "", p.getLong("lastSeen", 0), p.getLong("helperApp", 0), p.getBoolean("cfgError", false),
                canBlock = p.getBoolean("canBlock", true), linkedAt = p.getLong("linkedAt", 0), toldCovers = p.getBoolean("toldCovers", false))
            sight = IslandSight.Sight(p.getString("sight", "") ?: "", p.getBoolean("covers", false))
            if (_main.value.state.isNotEmpty()) ensureServer()
            HelperWatcher.init(app)
        } else {
            val st = readHelper() ?: return
            helperState = st
            UsageStore.base = st.base
            UsageStore.inflight = st.inflight
            coveredNow = st.covered
            _status.value = HelperStatus(lastOk = st.lastOk, mainApp = st.mainApp, covered = st.covered)
            _helper.value = true
            stopAlerts(app)
            helperStart(app)
            runCatching { HelperWatchdog.schedule(app) }
        }
    }

    /** Main sends the reminders; the helper's would come twice (and nag about its own accessibility service). */
    private fun stopAlerts(ctx: Context) = runCatching {
        WorkManager.getInstance(ctx).apply { cancelUniqueWork("lockdown-reminders"); cancelUniqueWork("lockdown-digest") }
    }

    fun versionCode(ctx: Context): Long =
        runCatching { PackageInfoCompat.getLongVersionCode(ctx.packageManager.getPackageInfo(ctx.packageName, 0)) }.getOrDefault(0)

    /** Run [block] on the main looper (where usage is recorded) and wait for it. False if it didn't run in time. */
    private fun onMain(block: () -> Unit): Boolean {
        if (Looper.myLooper() == Looper.getMainLooper()) { block(); return true }
        val done = CountDownLatch(1)
        val ok = AtomicBoolean(false)
        val state = AtomicInteger(0)          // 0 waiting, 1 running, 2 called off: it runs whole or not at all
        mainHandler.post { if (state.compareAndSet(0, 1)) { try { block(); ok.set(true) } finally { done.countDown() } } }
        if (!done.await(2, TimeUnit.SECONDS) && !state.compareAndSet(0, 2)) done.await()   // started: wait for the end
        return ok.get()
    }

    /** Opens Lockdown in the other profile (main -> Island to wake the helper; Island -> main from a block). */
    fun openOtherProfile(ctx: Context): Boolean = runCatching {
        if (Build.VERSION.SDK_INT < 28) return false
        val cpa = ctx.getSystemService(CrossProfileApps::class.java)
        val target = cpa.targetUserProfiles.firstOrNull() ?: return false
        cpa.startMainActivity(ComponentName(ctx, MainActivity::class.java), target)
        true
    }.getOrDefault(false)

    // =====================================================================================================
    //  MAIN: the server
    // =====================================================================================================

    data class MainLink(
        val state: String = "",              // "" | "linked" | "unlinking"
        val lastSeen: Long = 0,              // wall ms the helper last checked in
        val helperApp: Long = 0,             // the helper's versionCode
        val cfgError: Boolean = false,       // the helper couldn't read the rules
        val canBlock: Boolean = true,        // the helper has what it needs to see and block Island apps
        val code: String? = null,            // pairing code on show
        val codeUntil: Long = 0,
        val codeFails: Int = 0,
        val noPort: Boolean = false,         // no link port could be opened
        val linkedAt: Long = 0,              // wall ms the link was made (0: made by an older version)
        val toldCovers: Boolean = false,     // main told the helper at its last check-in that it covers Island
    )

    private val _main = MutableStateFlow(MainLink())
    val mainState: StateFlow<MainLink> = _main

    @Volatile private var pairing: PairWindow? = null
    @Volatile private var server: ServerSocket? = null
    @Volatile private var serverThread: Thread? = null
    // Bounded: any app can open connections; past the queue they are refused and closed (fds don't pile up).
    private val pool = ThreadPoolExecutor(3, 3, 0L, TimeUnit.MILLISECONDS, ArrayBlockingQueue(8))
    private var lastSeenSaved = 0L

    /** The Island app in front, as the helper last said (main's own last window is stale while it's there). */
    @Volatile var islandFront: String? = null
    @Volatile private var islandFrontAt = 0L

    fun islandInFront(): Boolean = islandFront != null && SystemClock.elapsedRealtime() - islandFrontAt < 15_000

    /** Main: when the helper last checked in (elapsed ms on this phone's monotonic clock; 0: not since this process
     *  started). What [HelperWatcher] reads: unlike the wall-clock lastSeen, setting the clock changes nothing. */
    @Volatile var heardAt = 0L
        private set

    /** Main: does its own accessibility service see Island apps? ([IslandSight]: the helper's visits checked against
     *  what the service heard.) Kept in the link's prefs, so a new link starts from nothing. */
    @Volatile private var sight = IslandSight.Sight()
    private val heardLog = IslandSight.Log()
    private val judged = HashMap<String, Long>()          // "pkg@from" -> when judged: each visit once

    /** Main's accessibility service heard from [pkg] (every event; cheap, and only while linked). */
    fun saw(pkg: String) {
        if (isMain && _main.value.state == "linked") heardLog.add(pkg, System.currentTimeMillis())
    }

    /** Main sees and blocks Island apps itself (its service runs and has been seeing them): the helper then needs no
     *  foreground service, counts nothing, and checks in only when it wakes. */
    fun mainCovers(): Boolean = isMain && IslandSight.mainCovers(_main.value.state == "linked", BlockService.connected, sight)

    @Synchronized private fun judgeFronts(fronts: List<IslandFront>) {
        val now = System.currentTimeMillis()
        val (log, since) = heardLog.snapshot()
        val results = ArrayList<Boolean>()
        for (f in IslandSight.clean(fronts)) {
            val key = "${f.pkg}@${f.from}"
            if (key in judged) continue
            val r = IslandSight.seen(f, log, since, app.packageName) ?: continue
            judged[key] = now
            results.add(r)
        }
        judged.entries.removeIf { now - it.value !in 0..2 * IslandSight.BACK_MS }
        val next = IslandSight.update(sight, results)
        if (next == sight) return
        sight = next
        prefs().edit().putString("sight", next.recent).putBoolean("covers", next.covers).apply()
    }

    @Synchronized private fun resetSight() { sight = IslandSight.Sight(); judged.clear(); heardLog.clear() }

    private fun prefs() = app.getSharedPreferences("island_link", Context.MODE_PRIVATE)

    private fun publish(f: (MainLink) -> MainLink = { it }) {
        val w = pairing
        _main.value = f(_main.value).copy(code = w?.code, codeUntil = w?.until() ?: 0, codeFails = w?.fails ?: 0)
    }

    /** Main: open a 3-minute, single-use pairing code. A link already there stays until the new one is made. */
    @Synchronized fun startPairing() {
        val w = PairWindow(LinkCrypto.newCode(), System.currentTimeMillis())
        pairing = w
        ensureServer()
        publish()
        mainHandler.postDelayed({ endPairing(w, onlyIfOver = true) }, PairWindow.TTL_MS + 1000)
    }

    @Synchronized fun cancelPairing() { pairing?.let { endPairing(it, onlyIfOver = false) } }

    @Synchronized private fun endPairing(w: PairWindow, onlyIfOver: Boolean) {
        if (pairing !== w || (onlyIfOver && w.open(System.currentTimeMillis()))) return
        if (!onlyIfOver) pairing = null      // an expired code stays on the card as "expired" until started again
        if (_main.value.state.isEmpty()) stopServer()
        publish()
    }

    /** Main: start unlinking (behind the challenge). It completes when the helper next checks in. */
    fun unlink() {
        if (_main.value.state != "linked") return
        prefs().edit().putString("state", "unlinking").commit()
        publish { it.copy(state = "unlinking") }
    }

    /** Main: drop the link now (behind the challenge). A helper still running keeps its last rules. */
    @Synchronized fun forget() {
        prefs().edit().clear().commit()
        islandFront = null
        resetSight()
        if (pairing?.open(System.currentTimeMillis()) != true) stopServer()   // an expired code needs no socket
        publish { MainLink() }
    }

    /** Main: the helper checked in lately and can block, or Island is paused (then nothing there can run). "Lately" is
     *  10 minutes, an hour while main covers Island itself or told the helper so at its last check-in (the helper then
     *  checks in only when it wakes): [IslandSight.checkedInLately]. */
    fun healthy(ctx: Context): Boolean {
        val m = _main.value
        if (checkedInLately(m) && m.canBlock) return true
        return profilePaused(ctx)
    }

    /** Main: the helper checked in lately ([IslandSight.checkedInLately]), as of [now] (wall ms). */
    fun checkedInLately(m: MainLink, now: Long = System.currentTimeMillis()) =
        IslandSight.checkedInLately(now - m.lastSeen, m.toldCovers, mainCovers())

    /** Island (the work profile) can't run anything: it is switched off, or still locked after a reboot (not running /
     *  not unlocked until you unlock it), so nothing there needs covering. A profile that is up with the helper
     *  stopped is NOT paused. isUserUnlocked(handle) needs no permission for a profile of the same user (API 24+). */
    fun profilePaused(ctx: Context): Boolean = runCatching {
        val um = ctx.getSystemService(UserManager::class.java)
        val others = um.userProfiles.filter { it != Process.myUserHandle() }
        others.isNotEmpty() && others.all { um.isQuietModeEnabled(it) || !um.isUserUnlocked(it) }
    }.getOrDefault(false)

    @Synchronized private fun ensureServer() {
        if (serverThread?.isAlive == true) return
        serverThread = thread(isDaemon = true, name = "lockdown-link") {
            // Loopback only, never 0.0.0.0. Another app on our port only moves us to the next one.
            val s = PORTS.firstNotNullOfOrNull { p -> runCatching { ServerSocket(p, 8, LOOPBACK) }.getOrNull() }
            publish { it.copy(noPort = s == null) }
            if (s == null) return@thread
            synchronized(this) {
                if (serverThread !== Thread.currentThread()) { runCatching { s.close() }; return@thread }   // stopped meanwhile
                server = s
            }
            while (!s.isClosed) {
                val c = try { s.accept() } catch (_: Exception) { if (!s.isClosed) Thread.sleep(200); continue }
                try { pool.execute { runCatching { serve(c) }; runCatching { c.close() } } } catch (_: Exception) { runCatching { c.close() } }
            }
        }
    }

    @Synchronized private fun stopServer() {
        runCatching { server?.close() }
        server = null
        serverThread = null
    }

    /** One connection: hello, one request, one answer. Nothing signed goes out before the caller proved the secret
     *  (or the code), so a stranger learns nothing. */
    private fun serve(c: Socket) {
        c.soTimeout = 5000
        val inp = DataInputStream(BufferedInputStream(c.getInputStream()))
        val out = DataOutputStream(BufferedOutputStream(c.getOutputStream()))
        val nM = LinkCrypto.nonce()
        LinkProtocol.write(out, Frame("hello", LinkProtocol.V, n = LinkCrypto.b64(nM)))
        val req = LinkProtocol.read(inp, LinkProtocol.MAIN_MAX)
        val nH = LinkProtocol.helperNonce(req) ?: return deny(out)
        when (req.t) {
            "pair" -> servePair(req, nM, nH, out)
            "sync" -> serveSync(req, nM, nH, out)
            else -> deny(out)
        }
    }

    private fun deny(out: DataOutputStream) {
        Thread.sleep(250)
        LinkProtocol.write(out, Frame("denied", LinkProtocol.V))
    }

    private fun servePair(req: Frame, nM: ByteArray, nH: ByteArray, out: DataOutputStream) {
        val s = synchronized(this) {
            val w = pairing?.takeIf { it.open(System.currentTimeMillis()) } ?: return@synchronized null
            val kp = LinkCrypto.pairKey(w.code)
            if (!LinkCrypto.same(LinkCrypto.unb64(req.proof), LinkProtocol.pairProofH(kp, nM, nH))) { w.fail(); publish(); return@synchronized null }
            w.succeed()
            pairing = null
            val s = LinkCrypto.secret(kp, nM, nH)
            val now = System.currentTimeMillis()
            prefs().edit().clear().putString("state", "linked").putString("secret", LinkCrypto.b64(s))
                .putLong("lastSeq", 0).putLong("lastSeen", now).putLong("linkedAt", now).commit()
            lastSeenSaved = now
            heardAt = SystemClock.elapsedRealtime()
            resetSight()
            publish { MainLink(state = "linked", lastSeen = now, linkedAt = now) }
            s
        } ?: return deny(out)
        LinkProtocol.write(out, Frame("paired", LinkProtocol.V, proof = LinkCrypto.b64(LinkProtocol.pairProofM(s, nM, nH))))
    }

    private fun serveSync(req: Frame, nM: ByteArray, nH: ByteArray, out: DataOutputStream) {
        val p = prefs()
        val secret = LinkCrypto.unb64(p.getString("secret", null)) ?: return deny(out)
        if (!LinkProtocol.verify(req, secret, LinkProtocol.H2M, nM, nH)) return deny(out)
        val body = json.decodeFromString<SyncBody>(req.body!!)
        heardAt = SystemClock.elapsedRealtime()        // (a signed message: the helper is running)
        HelperWatcher.heard()
        // The helper got the unlink and stands alone now: only then is the link dropped here.
        if (body.bye) {
            if (p.getString("state", "") != "unlinking") return deny(out)
            LinkProtocol.write(out, LinkProtocol.signed("bye", secret, LinkProtocol.M2H, nM, nH, "{}"))
            forget(); return
        }

        // The helper's time, once per batch. Applied, then marked: a crash between counts it twice, never zero times.
        var lastSeq = p.getLong("lastSeq", 0)
        if (LinkProtocol.shouldApply(body.seq, lastSeq)) {
            val d = LinkProtocol.cleanDeltas(body.deltas)
            if (d.isNotEmpty() && !onMain { UsageStore.addDeltas(d); UsageStore.flush() }) return   // sent again next time
            lastSeq = body.seq
            p.edit().putLong("lastSeq", lastSeq).commit()
        }
        body.fronts?.let { runCatching { judgeFronts(it) } }
        val covers = mainCovers()
        // Main sees Island apps itself while it covers: a report of the app in front would only stop it counting them.
        islandFront = if (covers) null else body.front
        islandFrontAt = SystemClock.elapsedRealtime()
        body.apps?.let { saveIslandApps(it) }
        body.use?.let { saveIslandUse(it) }
        body.useHours?.let { saveIslandHours(it) }
        body.opens?.let { saveIslandOpens(it) }
        // "Resume now" in Island: ends only the pause it names (a tightening, no challenge); never starts or lengthens one.
        body.endPause?.let { asked ->
            val before = Store.config.pause
            Store.update { it.copy(pause = com.husarp.lockdown.engine.Pause.endEarly(it.pause, asked)) }
            if (Store.config.pause != before) runCatching { Grayscale.sync(app) }
        }

        val cfg = Store.config
        val now = Enforce.ldt(TrustedTime.now(app))
        var usage: Map<String, Int> = emptyMap()
        if (!onMain { usage = LinkUsage.current(UsageStore.counter.counters, now, cfg.clock()) }) return
        val (cfgJson, cfgHash) = configJson(cfg)
        val (modesJson, modesHash) = modesJson()
        val unlinking = p.getString("state", "") == "unlinking"
        val state = StateBody(
            ack = lastSeq, usage = usage,
            cfg = cfgJson.takeIf { body.cfg != cfgHash }, cfgHash = cfgHash,
            modes = modesJson.takeIf { body.modes != modesHash }, modesHash = modesHash,
            unlink = unlinking, app = versionCode(app), covers = covers, opensMark = islandOpensMark(),
        )
        LinkProtocol.write(out, LinkProtocol.signed("state", secret, LinkProtocol.M2H, nM, nH, json.encodeToString(state)))

        if (unlinking) return       // the link and the secret stay until the helper says bye (or Forget Island now)
        val ms = System.currentTimeMillis()
        val m = _main.value
        if (ms - lastSeenSaved > 60_000 || m.helperApp != body.app || m.cfgError != body.cfgError || m.canBlock != body.canBlock || m.toldCovers != covers) {
            lastSeenSaved = ms
            p.edit().putLong("lastSeen", ms).putLong("helperApp", body.app).putBoolean("cfgError", body.cfgError)
                .putBoolean("canBlock", body.canBlock).putBoolean("toldCovers", covers).apply()
        }
        publish { it.copy(lastSeen = ms, helperApp = body.app, cfgError = body.cfgError, canBlock = body.canBlock, toldCovers = covers) }
        if (Reminders.islandFailing(app) && healthy(app)) Reminders.islandBack(app)   // its "not covered" notice goes now
    }

    // Serialised only when the config / modes change (a big imported config is several MB).
    private var cfgCache: Triple<Config, String, String>? = null
    private var modesCache: Triple<Any, String, String>? = null

    @Synchronized private fun configJson(c: Config): Pair<String, String> {
        cfgCache?.let { if (it.first === c) return it.second to it.third }
        val j = json.encodeToString(c)
        return (j to LinkCrypto.sha256(j)).also { cfgCache = Triple(c, it.first, it.second) }
    }

    @Synchronized private fun modesJson(): Pair<String, String> {
        val key = ModesStore.modes.value to ModesStore.active.value
        modesCache?.let { val k = it.first as Pair<*, *>; if (k.first === key.first && k.second === key.second) return it.second to it.third }
        val j = ModesStore.exportJson(key.first, key.second)
        return (j to LinkCrypto.sha256(j)).also { modesCache = Triple(key, it.first, it.second) }
    }

    // ---------- Island's apps, so Island-only apps can get rules in main ----------

    private fun islandAppsFile(ctx: Context) = File(ctx.filesDir, "island_apps.json")

    private fun saveIslandApps(list: List<IslandApp>) = runCatching {
        islandAppsFile(app).writeText(json.encodeToString(list.take(3000)))
    }

    private fun islandUseFile(ctx: Context) = File(ctx.filesDir, "island_use.json")

    private fun readIslandUse(ctx: Context): List<IslandUseRow> = islandUseFile(ctx).takeIf { it.exists() }?.let { f ->
        runCatching { json.decodeFromString<List<IslandUseRow>>(f.readText()) }.getOrNull()
    } ?: emptyList()

    @Synchronized private fun saveIslandUse(rows: List<IslandUseRow>) = runCatching {
        val today = java.time.LocalDate.now()
        val merged = IslandUse.merge(readIslandUse(app), IslandUse.clean(rows), today)
        islandUseFile(app).writeText(json.encodeToString(merged))
    }

    private fun islandHoursFile(ctx: Context) = File(ctx.filesDir, "island_hours.json")

    private fun readIslandHours(ctx: Context): List<IslandHourRow> = islandHoursFile(ctx).takeIf { it.exists() }?.let { f ->
        runCatching { json.decodeFromString<List<IslandHourRow>>(f.readText()) }.getOrNull()
    } ?: emptyList()

    @Synchronized private fun saveIslandHours(rows: List<IslandHourRow>) = runCatching {
        val merged = IslandUse.mergeHours(readIslandHours(app), IslandUse.cleanHours(rows), java.time.LocalDate.now())
        islandHoursFile(app).writeText(json.encodeToString(merged))
    }

    /** Main: Island's time per hour of [day] (24 values), or null when the helper sent none for that day. */
    fun islandHours(ctx: Context, day: java.time.LocalDate): List<Long>? =
        if (!isMain) null else IslandUse.hourMs(readIslandHours(ctx), day)

    /** Main: Island's time per app in [from]..[to] (for Insights); empty when not linked or nothing came yet. */
    fun islandUse(ctx: Context, from: java.time.LocalDate, to: java.time.LocalDate): Map<String, AppTime> =
        if (!isMain) emptyMap() else IslandUse.total(readIslandUse(ctx), from, to)

    /** Main: [mainMs] (main's screen time on [days]) plus Island's time on those days ([IslandUse.dayMs]); just
     *  [mainMs] when not linked or nothing came yet. */
    fun withIsland(ctx: Context, mainMs: Long, days: List<java.time.LocalDate>): Long {
        if (!isMain) return mainMs
        val rows = readIslandUse(ctx); val hours = readIslandHours(ctx)
        return mainMs + days.sumOf { IslandUse.dayMs(rows, hours, it) }
    }

    /** Main: all of Island's per-day, per-app rows that came over the link (empty when not linked). */
    fun islandRows(ctx: Context): List<IslandUseRow> = if (!isMain) emptyList() else readIslandUse(ctx)

    // ---------- Island apps opened, for the Activity log ----------

    private fun islandOpensFile(ctx: Context) = File(ctx.filesDir, "island_opens.json")

    private fun readIslandOpens(ctx: Context): List<IslandOpen> = islandOpensFile(ctx).takeIf { it.exists() }?.let { f ->
        runCatching { json.decodeFromString<List<IslandOpen>>(f.readText()) }.getOrNull()
    } ?: emptyList()

    @Volatile private var opensMark: Long? = null          // IslandOpens.mark of the file, once read (it is read at every check-in)

    @Synchronized private fun saveIslandOpens(rows: List<IslandOpen>) = runCatching {
        val now = System.currentTimeMillis()
        val merged = IslandOpens.merge(readIslandOpens(app), IslandOpens.clean(rows, now), now)
        islandOpensFile(app).writeText(json.encodeToString(merged))
        opensMark = IslandOpens.mark(merged, now)
    }

    /** Main: the latest end of an Island app visit it has; the helper sends only visits that ended after it. */
    @Synchronized private fun islandOpensMark(): Long =
        opensMark ?: IslandOpens.mark(readIslandOpens(app), System.currentTimeMillis()).also { opensMark = it }

    /** Main: Island apps opened that came over the link (empty in the Island copy, or when nothing came yet). */
    fun islandOpens(ctx: Context): List<IslandOpen> = if (!isMain) emptyList() else readIslandOpens(ctx)

    /** Main: apps installed only in Island, named "Label (Island)", for the app pickers. */
    fun islandOnly(ctx: Context, mine: Set<String>): List<InstalledApp> {
        if (!isMain) return emptyList()
        val f = islandAppsFile(ctx)
        if (!f.exists()) return emptyList()
        return runCatching { json.decodeFromString<List<IslandApp>>(f.readText()) }.getOrDefault(emptyList())
            .filter { it.pkg !in mine }.map { InstalledApp(it.pkg, "${it.label} (Island)") }
    }

    // =====================================================================================================
    //  HELPER: the client
    // =====================================================================================================

    @Serializable
    data class HelperState(
        val secret: String,
        val port: Int = 0,
        val nextSeq: Long = 1,
        val inflight: Batch? = null,
        val base: Map<String, Int> = emptyMap(),
        val appsHash: String = "",
        val useHash: String = "",
        val hoursHash: String = "",
        val lastOk: Long = 0,
        val mainApp: Long = 0,
        val linkedAt: Long = 0,               // wall ms this copy was linked (0: by an older version)
        val covered: Boolean = false,         // main said at the last check-in that it covers Island itself
        val opensMark: Long? = null,          // main's latest Island visit end at the last check-in (IslandOpens); null: not known yet, or an older main
    )

    /** When the link was made (wall ms), on either side; 0 if not linked or linked before this was kept. */
    fun linkedAt(): Long = if (isMain) _main.value.linkedAt.takeIf { _main.value.state.isNotEmpty() } ?: 0 else helperState?.linkedAt ?: 0

    data class HelperStatus(
        val lastOk: Long = 0,                 // wall ms of the last good sync
        val problem: String? = null,          // null | "unreachable" | "denied" | "version"
        val mainApp: Long = 0,
        val cfgError: Boolean = false,
        val covered: Boolean = false,         // main sees and blocks Island apps itself: no service needed here
    )

    private val _status = MutableStateFlow(HelperStatus())
    val status: StateFlow<HelperStatus> = _status

    @Volatile private var helperState: HelperState? = null
    // In memory only: a fresh process takes the whole config and modes again, so they can't drift apart.
    @Volatile private var cfgHash = ""
    @Volatile private var modesHash = ""
    @Volatile private var cfgFailed: String? = null
    @Volatile private var useCache: Pair<Long, IslandRead>? = null
    @Volatile private var appsCache: Pair<Long, List<IslandApp>>? = null
    /** "Resume now" tapped here: the pause to end, sent to main until it answers. */
    @Volatile private var endPauseAsked: com.husarp.lockdown.engine.BlockPause? = null

    /** Helper: "Resume now". Blocking comes back here at once; main is asked to end the same pause (only that one),
     *  and its rules are taken whole again on the next sync, so this copy shows what main really has. */
    fun endPause() = thread(isDaemon = true, name = "lockdown-link-resume") {
        synchronized(clientLock) {              // not in the middle of a sync, which could put the old rules back
            val p = Store.config.pause ?: return@thread
            endPauseAsked = p
            Store.update { it.copy(pause = null) }
            cfgHash = ""
        }
        if (LinkService.alive) kick() else runCatching { syncOnce(app) }     // no service while main covers Island
    }

    /** Helper: main covered Island at the last check-in that got through (false when it didn't get through). */
    @Volatile private var coveredNow = false
    @Volatile private var wokeAt = -1L

    fun helperCovered(): Boolean = coveredNow

    /**
     * Helper: bring the Island side up (process start, boot, update, opening this copy, main's "Fix"). While main
     * covered Island at the last check-in nothing needs to keep running here: one check-in in the background, then the
     * service only if main no longer covers it. Otherwise the service, as before (it stops itself once main covers).
     */
    fun helperStart(ctx: Context) {
        if (!isHelper) return
        if (coveredNow) thread(isDaemon = true, name = "lockdown-link-wake") { runCatching { helperWake(ctx) } }
        else HelperWatchdog.startOrNudge(ctx)
    }

    /** Helper, off the main thread (the 15-minute watchdog, [helperStart]): one check-in - sending Island's time,
     *  visits and apps, taking the rules - then the service runs only if main doesn't cover Island. */
    fun helperWake(ctx: Context) {
        if (!isHelper) return
        val t = SystemClock.elapsedRealtime()
        var ok = coveredNow && wokeAt >= 0 && t - wokeAt < 10_000      // just checked in (process start + its worker)
        if (!ok) {
            ok = runCatching { syncOnce(ctx) }.getOrDefault(false)
            if (!ok && isHelper) { Thread.sleep(3_000); ok = runCatching { syncOnce(ctx) }.getOrDefault(false) }
            if (ok) wokeAt = SystemClock.elapsedRealtime()
        }
        if (!isHelper) return                   // that check-in was the unlink
        if (IslandSight.helperNeedsService(ok, coveredNow)) {
            if (!LinkService.alive) HelperWatchdog.startOrNudge(ctx)
        } else {
            HelperWatchdog.clearNudge(ctx)
            if (LinkService.alive) LinkService.stop(ctx)
        }
    }

    /** The app in front in this profile (helper), from its usage events. */
    @Volatile var localFront: String? = null

    private val clientLock = Any()
    private val kickLock = Object()
    private var kicked = false

    /** Helper: something waits for main (time it hasn't acknowledged, "Resume now"): the sync keeps its quick pace
     *  until it is through ([com.husarp.lockdown.engine.Cadence.linkSyncMs]). */
    fun syncPending(): Boolean = UsageStore.inflight != null || endPauseAsked != null

    /** Helper: the last sync did not get through to main. */
    fun syncFailing(): Boolean = _status.value.problem != null

    /** Sync now (front changed, linked). */
    fun kick() = synchronized(kickLock) { kicked = true; kickLock.notifyAll() }

    fun waitKick(ms: Long) = synchronized(kickLock) { if (!kicked) kickLock.wait(ms); kicked = false }

    private fun helperFile(ctx: Context) = File(ctx.filesDir, "link.json")

    private fun readHelper(): HelperState? {
        val f = helperFile(app)
        if (!f.exists()) return null
        return runCatching { json.decodeFromString<HelperState>(f.readText()) }.getOrNull()
    }

    private fun saveHelper() {
        val st = helperState ?: return
        runCatching {
            val f = helperFile(app)
            val tmp = File(f.path + ".tmp")
            tmp.writeText(json.encodeToString(st.copy(inflight = UsageStore.inflight, base = UsageStore.base)))
            if (!tmp.renameTo(f)) f.writeText(tmp.readText())
        }
    }

    private class Reply(val frame: Frame, val port: Int)

    /** One request to main. Tries the last good port first; a port that isn't main (wrong hello, bad MAC) is skipped.
     *  Returns the reply, or the problem met. */
    private fun exchange(first: Int, request: (ByteArray, ByteArray) -> Frame, accept: (Frame, ByteArray, ByteArray) -> Boolean): Pair<Reply?, String> {
        var problem = "unreachable"
        for (p in listOf(first).filter { it in PORTS } + PORTS.filter { it != first }) {
            try {
                Socket().use { s ->
                    s.connect(InetSocketAddress(LOOPBACK, p), 500)
                    s.soTimeout = 15_000
                    val inp = DataInputStream(BufferedInputStream(s.getInputStream()))
                    val out = DataOutputStream(BufferedOutputStream(s.getOutputStream()))
                    val hello = LinkProtocol.read(inp, 4096)
                    if (hello.t != "hello") return@use
                    if (hello.v != LinkProtocol.V) { problem = "version"; return@use }
                    val nM = LinkCrypto.unb64(hello.n)?.takeIf { it.size == LinkCrypto.NONCE_LEN } ?: return@use
                    val nH = LinkCrypto.nonce()
                    LinkProtocol.write(out, request(nM, nH))
                    val r = LinkProtocol.read(inp, LinkProtocol.HELPER_MAX)
                    if (r.t == "denied") { if (problem == "unreachable") problem = "denied"; return@use }
                    if (accept(r, nM, nH)) return Reply(r, p) to ""
                }
            } catch (_: Exception) { }
        }
        return null to problem
    }

    /**
     * Link this copy to main with a code main shows (off the main thread). This copy's rules are replaced by main's.
     * Returns an error to show, or null when linked.
     */
    fun link(ctx: Context, typed: String): String? {
        val err = pair(typed)
        if (err != null) return err
        stopAlerts(ctx)
        _helper.value = true
        runCatching { syncOnce(ctx) }
        runCatching { LinkService.start(ctx) }
        runCatching { HelperWatchdog.schedule(ctx) }
        return null
    }

    private fun pair(typed: String): String? = synchronized(clientLock) {
        val code = LinkCrypto.normalize(typed)
        if (code.length != LinkCrypto.CODE_LEN || code.any { it !in LinkCrypto.ALPHABET }) return "A link code has 8 letters and digits, like K7QM-2XPA."
        val kp = LinkCrypto.pairKey(code)
        var secret: ByteArray? = null
        val (reply, problem) = exchange(helperState?.port ?: 0,
            { nM, nH -> Frame("pair", LinkProtocol.V, n = LinkCrypto.b64(nH), proof = LinkCrypto.b64(LinkProtocol.pairProofH(kp, nM, nH))) },
        ) { r, nM, nH ->
            val s = LinkCrypto.secret(kp, nM, nH)
            (r.t == "paired" && LinkCrypto.same(LinkCrypto.unb64(r.proof), LinkProtocol.pairProofM(s, nM, nH))).also { if (it) secret = s }
        }
        if (reply == null) return when (problem) {
            "denied" -> "Wrong or expired code. Check it, or start a new one in your main Lockdown."
            "version" -> "The two copies can't talk: update Lockdown in both profiles."
            else -> "Can't reach Lockdown in your main profile. Is it installed and running? If you use AFWall, allow Lockdown in both profiles."
        }
        // A new link: a batch that was out goes back into pending and seq starts again (main starts again too).
        val ok = onMain {
            LinkUsage.foldBack(UsageStore.counter.counters, UsageStore.inflight)
            UsageStore.inflight = null
            UsageStore.base = emptyMap()
            helperState = HelperState(LinkCrypto.b64(secret!!), port = reply.port, linkedAt = System.currentTimeMillis())
            cfgHash = ""; modesHash = ""; cfgFailed = null
            saveHelper()
            UsageStore.flush()
        }
        if (!ok) return "Something got in the way. Try again."
        _status.value = HelperStatus()
        null
    }

    /** Helper: one exchange with main - send time used and the app in front, take rules, modes and main's usage.
     *  True when it got through. */
    fun syncOnce(ctx: Context): Boolean = synchronized(clientLock) {
        if (helperState == null) return false
        val secret = LinkCrypto.unb64(helperState?.secret) ?: return false
        // 1. Pending time becomes the batch out (sent again unchanged until acked).
        if (!onMain {
                val st = helperState ?: return@onMain
                val (b, next) = LinkUsage.formBatch(UsageStore.counter.counters, UsageStore.inflight, st.nextSeq)
                if (b !== UsageStore.inflight) {
                    UsageStore.inflight = b
                    helperState = st.copy(nextSeq = next)
                    saveHelper()            // the batch is saved before pending is: a crash counts it twice, never loses it
                    UsageStore.flush()
                }
            }) return false
        val st = helperState ?: return false
        val batch = UsageStore.inflight
        var asked: com.husarp.lockdown.engine.BlockPause? = null
        val apps = islandApps(ctx)
        val appsHash = LinkCrypto.sha256(apps.joinToString("\n") { it.pkg + "\t" + it.label })
        val (use, hours, opensAll) = islandUseRows(ctx)
        val opens = IslandOpens.toSend(opensAll, st.opensMark)
        val hoursHash = LinkCrypto.sha256(hours.joinToString("\n") { "${it.day}\t${it.hour}\t${it.sec}" })
        val useHash = LinkCrypto.sha256(use.joinToString("\n") { "${it.day}\t${it.pkg}\t${it.sec}" })
        val body = json.encodeToString(SyncBody(
            seq = batch?.seq ?: 0, deltas = batch?.deltas ?: emptyMap(),
            cfg = cfgFailed ?: cfgHash, modes = modesHash, front = localFront, app = versionCode(ctx),
            cfgError = cfgFailed != null, apps = apps.takeIf { appsHash != st.appsHash }, canBlock = canBlock(ctx),
            use = use.takeIf { it.isNotEmpty() && useHash != st.useHash },
            useHours = hours.takeIf { it.isNotEmpty() && hoursHash != st.hoursHash },
            endPause = endPauseAsked.also { asked = it },
            fronts = recentFronts(ctx, st.lastOk).takeIf { it.isNotEmpty() },
            opens = opens.takeIf { it.isNotEmpty() },
        ))
        val (reply, problem) = exchange(st.port,
            { nM, nH -> LinkProtocol.signed("sync", secret, LinkProtocol.H2M, nM, nH, body) },
        ) { r, nM, nH -> r.t == "state" && LinkProtocol.verify(r, secret, LinkProtocol.M2H, nM, nH) }
        if (reply == null) { coveredNow = false; _status.value = _status.value.copy(problem = problem, covered = false); return false }
        val sb = json.decodeFromString<StateBody>(reply.frame.body!!)
        if (asked != null && endPauseAsked == asked) endPauseAsked = null      // main has had it (applied, or a newer pause there)
        if (sb.unlink) {
            // Say bye (signed), so main drops the link only once this copy has the unlink. If it doesn't arrive,
            // main keeps "Unlinking" and its Forget Island now.
            val bye = json.encodeToString(SyncBody(app = versionCode(ctx), bye = true))
            exchange(reply.port, { nM, nH -> LinkProtocol.signed("sync", secret, LinkProtocol.H2M, nM, nH, bye) },
            ) { r, nM, nH -> r.t == "bye" && LinkProtocol.verify(r, secret, LinkProtocol.M2H, nM, nH) }
            coveredNow = false
            unlinkHere(ctx, sb); return true
        }

        sb.cfg?.let { text ->
            val c = runCatching { json.decodeFromString<Config>(text) }.getOrNull()
            if (c == null) cfgFailed = sb.cfgHash        // keep the old rules; main isn't asked to resend the same one
            else {
                val before = Store.config
                Store.update { applyConfig(c, it) }
                cfgHash = sb.cfgHash; cfgFailed = null
                if (before.protection != c.protection) runCatching { Protection.load(ctx) }
            }
        }
        sb.modes?.let { if (ModesStore.replace(it)) modesHash = sb.modesHash }
        val now = System.currentTimeMillis()
        onMain {
            UsageStore.inflight = LinkUsage.acked(UsageStore.inflight, sb.ack)
            UsageStore.base = sb.usage          // already holds every acked batch: nothing counts twice
            val cur = helperState ?: return@onMain
            helperState = cur.copy(port = reply.port, nextSeq = maxOf(cur.nextSeq, sb.ack + 1), lastOk = now, mainApp = sb.app, appsHash = appsHash, useHash = useHash, hoursHash = hoursHash, covered = sb.covers, opensMark = sb.opensMark)
            saveHelper()
        }
        coveredNow = sb.covers
        _status.value = HelperStatus(lastOk = now, mainApp = sb.app, cfgError = cfgFailed != null, covered = sb.covers)
        true
    }

    /** Helper: Island's visits since a minute before the last good check-in (at most [IslandSight.BACK_MS] back), from
     *  its usage events, so main can check it sees them too. None without usage access. */
    private fun recentFronts(ctx: Context, lastOk: Long): List<IslandFront> = runCatching {
        if (!Usage.hasAccess(ctx)) return emptyList()
        val now = System.currentTimeMillis()
        val events = ctx.getSystemService(UsageStatsManager::class.java)
            .queryEvents(maxOf(now - IslandSight.BACK_MS, lastOk - 60_000L), now) ?: return emptyList()
        val list = ArrayList<IslandSight.Ev>()
        val e = UsageEvents.Event()
        while (events.hasNextEvent()) {
            events.getNextEvent(e)
            list.add(IslandSight.Ev(e.timeStamp, e.eventType, e.packageName ?: continue, e.className ?: ""))
        }
        IslandSight.visits(list, now, ctx.packageName)
    }.getOrDefault(emptyList())

    /** Helper: this copy can see the Island app in front and block it (its accessibility service, or usage access
     *  with the block screen). */
    private fun canBlock(ctx: Context): Boolean = BlockService.connected ||
        (Usage.hasAccess(ctx) && android.provider.Settings.canDrawOverlays(ctx))

    private data class IslandRead(val use: List<IslandUseRow> = emptyList(), val hours: List<IslandHourRow> = emptyList(), val opens: List<IslandOpen> = emptyList())

    /** The helper's per-day app time and its app visits that are over (for main's Activity log), read at most every
     *  5 minutes (needs usage access; none without it). */
    private fun islandUseRows(ctx: Context): IslandRead {
        val c = useCache
        if (c != null && SystemClock.elapsedRealtime() - c.first < 5 * 60_000L) return c.second
        val read = runCatching {
            if (!Usage.hasAccess(ctx)) return@runCatching IslandRead()
            val spans = ArrayList<AppSpan>()
            val (use, hours) = Usage.islandRows(ctx, spans)
            IslandRead(use, hours, runCatching { islandOpensFrom(ctx, spans) }.getOrDefault(emptyList()))
        }.getOrDefault(IslandRead())
        useCache = SystemClock.elapsedRealtime() to read
        return read
    }

    /** The visits in [spans] that are over, named here (main may not have the app); Lockdown itself, the system bar
     *  and the home screen left out, as in main's log. */
    private fun islandOpensFrom(ctx: Context, spans: List<AppSpan>): List<IslandOpen> {
        val pm = ctx.packageManager
        val home = runCatching {
            pm.resolveActivity(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME), 0)?.activityInfo?.packageName
        }.getOrNull()
        val leave = setOfNotNull(ctx.packageName, "com.android.systemui", home)
        val labels = HashMap<String, String>()
        return IslandOpens.finished(spans, System.currentTimeMillis(), { pkg ->
            labels.getOrPut(pkg) { runCatching { pm.getApplicationInfo(pkg, 0).loadLabel(pm).toString() }.getOrDefault(pkg) }
        }) { it in leave }
    }

    /** The helper's launchable apps, read at most every 30 minutes. */
    private fun islandApps(ctx: Context): List<IslandApp> {
        val c = appsCache
        if (c != null && SystemClock.elapsedRealtime() - c.first < 30 * 60_000L) return c.second
        val list = runCatching { Apps.launchable(ctx).map { IslandApp(it.pkg, it.label) } }.getOrDefault(emptyList())
        appsCache = SystemClock.elapsedRealtime() to list
        return list
    }

    /** Main said unlink (signed): keep every second counted - main's (as of this reply, so the batch it just acked
     *  isn't added twice), in flight and pending - then stand alone. */
    private fun unlinkHere(ctx: Context, sb: StateBody) {
        mainHandler.post {
            val all = LinkUsage.sum(sb.usage, LinkUsage.acked(UsageStore.inflight, sb.ack)?.deltas, UsageStore.counter.counters)
            UsageStore.counter.counters.clear()
            UsageStore.counter.counters.putAll(all)
            UsageStore.base = emptyMap()
            UsageStore.inflight = null
            UsageStore.flush()
            helperFile(ctx).delete()
            helperState = null
            localFront = null
            _helper.value = false
            _status.value = HelperStatus()
            Reminders.schedule(ctx)
            runCatching { HelperWatchdog.cancel(ctx) }
            LinkService.stop(ctx)
        }
    }
}
