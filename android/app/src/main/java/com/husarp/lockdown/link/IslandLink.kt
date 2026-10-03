package com.husarp.lockdown.link

import android.content.ComponentName
import android.content.Context
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
                canBlock = p.getBoolean("canBlock", true))
            if (_main.value.state.isNotEmpty()) ensureServer()
        } else {
            val st = readHelper() ?: return
            helperState = st
            UsageStore.base = st.base
            UsageStore.inflight = st.inflight
            _status.value = HelperStatus(lastOk = st.lastOk, mainApp = st.mainApp)
            _helper.value = true
            stopAlerts(app)
            runCatching { LinkService.start(app) }
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
        if (pairing?.open(System.currentTimeMillis()) != true) stopServer()   // an expired code needs no socket
        publish { MainLink() }
    }

    /** Main: the helper checked in lately and can block, or Island is paused (then nothing there can run). */
    fun healthy(ctx: Context): Boolean {
        val m = _main.value
        // A negative age is a clock set back, not a fresh check-in.
        if (System.currentTimeMillis() - m.lastSeen in 0 until 10 * 60_000L && m.canBlock) return true
        return runCatching {
            val um = ctx.getSystemService(UserManager::class.java)
            val others = um.userProfiles.filter { it != Process.myUserHandle() }
            others.isNotEmpty() && others.all { um.isQuietModeEnabled(it) }
        }.getOrDefault(false)
    }

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
                .putLong("lastSeq", 0).putLong("lastSeen", now).commit()
            lastSeenSaved = now
            publish { MainLink(state = "linked", lastSeen = now) }
            s
        } ?: return deny(out)
        LinkProtocol.write(out, Frame("paired", LinkProtocol.V, proof = LinkCrypto.b64(LinkProtocol.pairProofM(s, nM, nH))))
    }

    private fun serveSync(req: Frame, nM: ByteArray, nH: ByteArray, out: DataOutputStream) {
        val p = prefs()
        val secret = LinkCrypto.unb64(p.getString("secret", null)) ?: return deny(out)
        if (!LinkProtocol.verify(req, secret, LinkProtocol.H2M, nM, nH)) return deny(out)
        val body = json.decodeFromString<SyncBody>(req.body!!)
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
        islandFront = body.front
        islandFrontAt = SystemClock.elapsedRealtime()
        body.apps?.let { saveIslandApps(it) }

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
            unlink = unlinking, app = versionCode(app),
        )
        LinkProtocol.write(out, LinkProtocol.signed("state", secret, LinkProtocol.M2H, nM, nH, json.encodeToString(state)))

        if (unlinking) return       // the link and the secret stay until the helper says bye (or Forget Island now)
        val ms = System.currentTimeMillis()
        val m = _main.value
        if (ms - lastSeenSaved > 60_000 || m.helperApp != body.app || m.cfgError != body.cfgError || m.canBlock != body.canBlock) {
            lastSeenSaved = ms
            p.edit().putLong("lastSeen", ms).putLong("helperApp", body.app).putBoolean("cfgError", body.cfgError)
                .putBoolean("canBlock", body.canBlock).apply()
        }
        publish { it.copy(lastSeen = ms, helperApp = body.app, cfgError = body.cfgError, canBlock = body.canBlock) }
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
        val lastOk: Long = 0,
        val mainApp: Long = 0,
    )

    data class HelperStatus(
        val lastOk: Long = 0,                 // wall ms of the last good sync
        val problem: String? = null,          // null | "unreachable" | "denied" | "version"
        val mainApp: Long = 0,
        val cfgError: Boolean = false,
    )

    private val _status = MutableStateFlow(HelperStatus())
    val status: StateFlow<HelperStatus> = _status

    @Volatile private var helperState: HelperState? = null
    // In memory only: a fresh process takes the whole config and modes again, so they can't drift apart.
    @Volatile private var cfgHash = ""
    @Volatile private var modesHash = ""
    @Volatile private var cfgFailed: String? = null
    @Volatile private var appsCache: Pair<Long, List<IslandApp>>? = null

    /** The app in front in this profile (helper), from its usage events. */
    @Volatile var localFront: String? = null

    private val clientLock = Any()
    private val kickLock = Object()
    private var kicked = false

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
            helperState = HelperState(LinkCrypto.b64(secret!!), port = reply.port)
            cfgHash = ""; modesHash = ""; cfgFailed = null
            saveHelper()
            UsageStore.flush()
        }
        if (!ok) return "Something got in the way. Try again."
        _status.value = HelperStatus()
        null
    }

    /** Helper: one exchange with main - send time used and the app in front, take rules, modes and main's usage. */
    fun syncOnce(ctx: Context): Unit = synchronized(clientLock) {
        if (helperState == null) return
        val secret = LinkCrypto.unb64(helperState?.secret) ?: return
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
            }) return
        val st = helperState ?: return
        val batch = UsageStore.inflight
        val apps = islandApps(ctx)
        val appsHash = LinkCrypto.sha256(apps.joinToString("\n") { it.pkg + "\t" + it.label })
        val body = json.encodeToString(SyncBody(
            seq = batch?.seq ?: 0, deltas = batch?.deltas ?: emptyMap(),
            cfg = cfgFailed ?: cfgHash, modes = modesHash, front = localFront, app = versionCode(ctx),
            cfgError = cfgFailed != null, apps = apps.takeIf { appsHash != st.appsHash }, canBlock = canBlock(ctx),
        ))
        val (reply, problem) = exchange(st.port,
            { nM, nH -> LinkProtocol.signed("sync", secret, LinkProtocol.H2M, nM, nH, body) },
        ) { r, nM, nH -> r.t == "state" && LinkProtocol.verify(r, secret, LinkProtocol.M2H, nM, nH) }
        if (reply == null) { _status.value = _status.value.copy(problem = problem); return }
        val sb = json.decodeFromString<StateBody>(reply.frame.body!!)
        if (sb.unlink) {
            // Say bye (signed), so main drops the link only once this copy has the unlink. If it doesn't arrive,
            // main keeps "Unlinking" and its Forget Island now.
            val bye = json.encodeToString(SyncBody(app = versionCode(ctx), bye = true))
            exchange(reply.port, { nM, nH -> LinkProtocol.signed("sync", secret, LinkProtocol.H2M, nM, nH, bye) },
            ) { r, nM, nH -> r.t == "bye" && LinkProtocol.verify(r, secret, LinkProtocol.M2H, nM, nH) }
            unlinkHere(ctx, sb); return
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
            helperState = cur.copy(port = reply.port, nextSeq = maxOf(cur.nextSeq, sb.ack + 1), lastOk = now, mainApp = sb.app, appsHash = appsHash)
            saveHelper()
        }
        _status.value = HelperStatus(lastOk = now, mainApp = sb.app, cfgError = cfgFailed != null)
    }

    /** Helper: this copy can see the Island app in front and block it (its accessibility service, or usage access
     *  with the block screen). */
    private fun canBlock(ctx: Context): Boolean = BlockService.connected ||
        (Usage.hasAccess(ctx) && android.provider.Settings.canDrawOverlays(ctx))

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
            LinkService.stop(ctx)
        }
    }
}
