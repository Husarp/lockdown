package com.husarp.lockdown.update

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.core.content.FileProvider
import com.husarp.lockdown.data.Store
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import java.io.File
import java.io.IOException
import java.lang.ref.WeakReference
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException
import javax.net.ssl.SSLException

/**
 * The in-app updater (APP-STANDARDS sections 2 and 3). Asks GitHub for its recent releases at launch and on
 * every return to the app (at most every 5 minutes), finds the APK by its ".apk" ending, downloads it over
 * the app's own connection into the cache with the progress shown in the app, and hands it to Android's
 * package installer. The APK is deleted once the new version runs.
 *
 * Only an APK signed with the same release key (~/.keystores/lockdownmobile.jks) installs over this app.
 */
object Updates {
    // Recent releases, not /releases/latest: a PC-only release on top would hide the phone's update.
    private const val API = "https://api.github.com/repos/Husarp/lockdown/releases?per_page=20"
    const val RELEASES_PAGE = "https://github.com/Husarp/lockdown/releases"
    private const val TIMEOUT_MS = 10_000

    /** The newest Android version on GitHub. [page] is its release page. */
    data class Latest(val version: String, val apkName: String, val apkUrl: String, val apkSize: Long, val page: String)

    sealed interface Phase {
        data object Idle : Phase
        data class Downloading(val percent: Int) : Phase
        data object NeedsPermission : Phase      // Android's "install unknown apps" screen is open
        data object Ready : Phase                // downloaded while the app was in the background; installs on return
        data object Installing : Phase           // handed to Android's installer
        data class Failed(val why: String) : Phase
    }

    data class State(
        val latest: Latest? = null,              // newest version found on GitHub (may equal the current one)
        val pageUrl: String = RELEASES_PAGE,     // the page of the release carrying the newest APK, for GITHUB
        val checking: Boolean = false,
        val message: String? = null,             // answer to a manual CHECK NOW
        val bannerHidden: Boolean = false,
        val phase: Phase = Phase.Idle,
    )

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val gate = UpdateLogic.BannerGate()
    @Volatile private var lastCheckMs = 0L
    @Volatile private var pendingApk: File? = null
    /** The app's screen while it is in front: Android's screens are opened from it (same task, so Back returns
     *  here), and never while the app is in the background (Android 10+ silently blocks that). */
    @Volatile private var front: WeakReference<Activity>? = null

    fun current(ctx: Context): String =
        runCatching { ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName ?: "?" }.getOrDefault("?")

    /** The main-screen banner. (State.bannerHidden mirrors the gate so the screen redraws when ✕ is tapped.) */
    fun bannerVisible(s: State, current: String): Boolean = gate.visible(s.latest?.version, current)

    fun available(s: State, current: String): Boolean = s.latest != null && UpdateLogic.isNewer(s.latest.version, current)

    /** A fresh start of the app (not a return, not a rotation): the banner may show again; old APKs go. */
    fun onAppStart(ctx: Context) {
        gate.onAppStart()
        _state.update { it.copy(bannerHidden = false) }
        marker(ctx).delete()   // a fresh start is not a return from the "install unknown apps" screen
        // an old failure, or an installer that was left without installing, doesn't carry over to a fresh start
        _state.update { if (it.phase is Phase.Failed || it.phase == Phase.Installing) it.copy(phase = Phase.Idle) else it }
        if (_state.value.phase is Phase.Downloading) return   // the process outlived the screen mid-download
        val cur = current(ctx)
        scope.launch {
            dir(ctx).listFiles()?.forEach { f ->
                val v = UpdateLogic.apkVersion(f.name)
                if (v == null || !UpdateLogic.isNewer(v, cur)) f.delete()   // also drops unfinished .part files
            }
        }
    }

    /** Every time the app comes to the front: carry on an install waiting on the permission, then auto-check. */
    fun onResume(activity: Activity) {
        front = WeakReference(activity)
        val app = activity.applicationContext
        val phase = _state.value.phase
        // Some phones restart the app when "install unknown apps" is switched on; the marker file survives that.
        val waiting = phase == Phase.NeedsPermission || (phase == Phase.Idle && marker(app).exists())
        when {
            waiting -> {
                val apk = pendingApk ?: runCatching { File(dir(app), marker(app).readText().trim()) }.getOrNull()
                marker(app).delete()
                if (canInstall(app) && apk != null && apk.isFile) install(app, apk)
                else fail("Lockdown isn't allowed to install apps yet. Allow \"Install unknown apps\" for Lockdown, then TRY AGAIN.")
            }
            phase == Phase.Ready -> pendingApk?.takeIf { it.isFile }?.let { install(app, it) } ?: fail("The downloaded update is gone.")
            phase == Phase.Installing -> fail("The update wasn't installed. If Android said it couldn't install it, the update " +
                "was signed with a different key than this app - get it from GitHub.")
        }
        autoCheck(app)
    }

    fun onPause() { front = null }

    fun dismissBanner() { gate.dismiss(); _state.update { it.copy(bannerHidden = true) } }

    /** Automatic check: only when switched on, at most every 5 minutes, silent when it fails. */
    fun autoCheck(ctx: Context) {
        if (!Store.config.settings.checkUpdates || _state.value.checking) return
        if (!UpdateLogic.mayCheck(System.currentTimeMillis(), lastCheckMs)) return
        check(ctx.applicationContext, manual = false)
    }

    /** CHECK NOW: gives up after ~10 seconds and says in words what is likely wrong. */
    fun checkNow(ctx: Context) {
        if (_state.value.checking) return
        check(ctx.applicationContext, manual = true)
    }

    private fun check(app: Context, manual: Boolean) {
        lastCheckMs = System.currentTimeMillis()
        _state.update { it.copy(checking = true, message = if (manual) "Checking…" else it.message) }
        val cur = current(app)
        scope.launch {
            // Not a child of this coroutine, so the 10-second limit returns even if the socket hangs in DNS.
            val job = scope.async { runCatching { fetchLatest() } }
            val res = withTimeoutOrNull(TIMEOUT_MS.toLong()) { job.await() }
                ?: Result.failure(SocketTimeoutException())
            res.onSuccess { (latest, page) ->
                _state.update {
                    it.copy(checking = false, latest = latest, pageUrl = page,
                        message = if (!manual) it.message
                        else if (latest != null && UpdateLogic.isNewer(latest.version, cur)) "Lockdown Mobile ${latest.version} is available."
                        else "You're up to date ($cur).")
                }
            }.onFailure { e ->
                _state.update { it.copy(checking = false, message = if (manual) describe(e, download = false) else it.message) }
            }
        }
    }

    private class HttpStatus(val code: Int) : IOException("HTTP $code")

    /** The newest APK among recent releases (null when none has one) and its release page. Blocking; off the main thread. */
    private fun fetchLatest(): Pair<Latest?, String> {
        val c = open(API).apply { setRequestProperty("Accept", "application/vnd.github+json") }
        try {
            if (c.responseCode != 200) throw HttpStatus(c.responseCode)
            val list = JSONArray(c.inputStream.bufferedReader().use { it.readText() })
            val releases = (0 until list.length()).map { i ->
                val j = list.getJSONObject(i)
                val arr = j.optJSONArray("assets")
                val assets = (0 until (arr?.length() ?: 0)).map { k ->
                    val a = arr!!.getJSONObject(k)
                    UpdateLogic.Asset(a.getString("name"), a.getString("browser_download_url"), a.optLong("size", -1))
                }
                UpdateLogic.Release(j.optString("html_url", RELEASES_PAGE), assets, j.optBoolean("draft"), j.optBoolean("prerelease"))
            }
            val (rel, a, v) = UpdateLogic.newestApk(releases) ?: return null to RELEASES_PAGE
            return Latest(v, a.name, a.url, a.size, rel.page) to rel.page
        } finally { c.disconnect() }
    }

    private fun open(url: String) = (URL(url).openConnection() as HttpURLConnection).apply {
        connectTimeout = TIMEOUT_MS; readTimeout = TIMEOUT_MS
        instanceFollowRedirects = true
        setRequestProperty("User-Agent", "LockdownMobile")
    }

    /** UPDATE / GET UPDATE / TRY AGAIN: download (unless already here), then install. */
    fun update(ctx: Context) {
        val app = ctx.applicationContext
        if (_state.value.phase is Phase.Downloading) return
        val latest = _state.value.latest ?: run {
            // e.g. TRY AGAIN after Android restarted the app: use the file already here, else look again
            pendingApk?.takeIf { it.isFile }?.let { install(app, it) } ?: checkNow(app)
            return
        }
        val file = File(dir(app), latest.apkName.substringAfterLast('/'))
        if (file.exists() && (latest.apkSize <= 0 || file.length() == latest.apkSize)) { install(app, file); return }
        _state.update { it.copy(phase = Phase.Downloading(0)) }
        scope.launch {
            runCatching { download(latest, file) }
                .onSuccess { withContext(Dispatchers.Main) { install(app, file) } }
                .onFailure { fail(describe(it, download = true)) }
        }
    }

    private fun download(latest: Latest, file: File) {
        val part = File(file.parentFile, file.name + ".part")
        val c = open(latest.apkUrl)
        try {
            if (c.responseCode != 200) throw HttpStatus(c.responseCode)
            val total = c.contentLengthLong.takeIf { it > 0 } ?: latest.apkSize
            var done = 0L; var shown = -1
            c.inputStream.use { inp ->
                part.outputStream().use { out ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        val n = inp.read(buf); if (n < 0) break
                        out.write(buf, 0, n); done += n
                        val pct = if (total > 0) (done * 100 / total).toInt().coerceIn(0, 100) else 0
                        if (pct != shown) { shown = pct; _state.update { it.copy(phase = Phase.Downloading(pct)) } }
                    }
                }
            }
            if (total > 0 && done != total) throw IOException("incomplete")
            if (!part.renameTo(file)) throw IOException("rename")
        } catch (e: Throwable) {
            part.delete(); throw e
        } finally { c.disconnect() }
    }

    private fun canInstall(ctx: Context) = ctx.packageManager.canRequestPackageInstalls()

    /**
     * Hands the APK to Android's installer; the first time, opens "install unknown apps" for Lockdown instead.
     * Main thread only. With the app in the background it waits (Phase.Ready) and goes on in [onResume].
     */
    private fun install(app: Context, apk: File) {
        pendingApk = apk
        val act = front?.get()
        if (act == null) { _state.update { it.copy(phase = Phase.Ready) }; return }
        if (!canInstall(app)) {
            _state.update { it.copy(phase = Phase.NeedsPermission) }
            runCatching { marker(app).writeText(apk.name) }
            runCatching {
                act.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${app.packageName}")))
            }.onFailure {
                marker(app).delete()
                fail("Couldn't open Android's \"Install unknown apps\" setting. Allow it for Lockdown in Settings, then TRY AGAIN.")
            }
            return
        }
        runCatching {
            val uri = FileProvider.getUriForFile(app, "${app.packageName}.files", apk)
            act.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION))
            _state.update { it.copy(phase = Phase.Installing) }
        }.onFailure { fail("Couldn't open Android's installer.") }
    }

    private fun fail(why: String) = _state.update { it.copy(phase = Phase.Failed(why)) }

    private fun dir(ctx: Context) = File(ctx.cacheDir, "updates").apply { mkdirs() }

    /** Names the APK waiting on "install unknown apps" (no version in its name, so the start-up cleanup drops it). */
    private fun marker(ctx: Context) = File(dir(ctx), "pending-install")

    /** What went wrong, in words (never "Failed to fetch"). */
    private fun describe(e: Throwable, download: Boolean): String = when {
        e is UnknownHostException -> "Can't reach GitHub. Check that the phone is online - or a firewall (like AFWall) may be blocking Lockdown."
        e is SocketTimeoutException -> "GitHub didn't answer within 10 seconds. The connection may be slow or blocked."
        e is SSLException -> "Couldn't open a secure connection to GitHub. The network may be intercepting it, or the phone's date is wrong."
        e is HttpStatus && (e.code == 403 || e.code == 429) -> "GitHub's limit of checks per hour was reached. Try again in a while."
        e is HttpStatus && e.code == 404 -> if (download) "The update file is no longer on GitHub." else "No release found on GitHub."
        download && e.message == "incomplete" -> "The download stopped before it finished."
        download && (e.message == "rename" || e.message?.contains("ENOSPC") == true) -> "Couldn't save the update - is the phone's storage full?"
        download -> "The download failed - the connection to GitHub dropped."
        else -> "Couldn't check for updates - the connection to GitHub failed."
    }
}
