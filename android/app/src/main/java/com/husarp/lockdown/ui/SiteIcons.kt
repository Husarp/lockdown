package com.husarp.lockdown.ui

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.husarp.lockdown.block.Enforce
import com.husarp.lockdown.block.Protection
import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.IconBook
import com.husarp.lockdown.engine.ModeState
import com.husarp.lockdown.engine.SiteIconChain
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.vpn.Dns
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.ByteArrayOutputStream
import java.io.File
import java.net.Inet6Address
import java.net.InetAddress
import java.net.URI
import java.net.URL
import java.time.LocalDateTime
import java.util.concurrent.ConcurrentHashMap
import javax.net.ssl.HttpsURLConnection

/**
 * Is [host] blocked right now on this phone - the way the site filter (vpn/LockdownVpn.kt) decides it? A site that
 * is must never be asked for its icon directly: the filter would log a blocked try and Lockdown would show a notice.
 * Any block counts here (also one done only in the browser), and a mistake means "blocked", never the other way.
 */
object SiteIconGate {
    fun blockedNow(cfg: Config, host: String, now: LocalDateTime, mode: ModeState?): Boolean =
        runCatching {
            Enforce.site(cfg, host, now, mode) != null || (cfg.enabled && (Protection.blocked(host) || Dns.encryptedDns(host)))
        }.getOrDefault(true)
}

/**
 * Real icons for sites: a saved one (filesDir/site-icons/<domain>.png), else the one bundled in the app
 * (assets/sites/icons, found through the host or a parent domain), else the letter tile. For the sites on your lists
 * that have none, [watch] fetches them in the background - see [SiteIconChain] for the order and the limits.
 *
 * Lockdown's own traffic never goes through its site-filter VPN (it is excluded with addDisallowedApplication), and
 * a site that is blocked right now isn't asked directly anyway. No worker, no alarm, no loop: a fetch starts only
 * when the app comes to the front or the lists change, and stops when it leaves.
 */
object SiteIcons {
    private const val DIR = "site-icons"
    private const val ASSETS = "sites/icons"
    private const val UA = "Mozilla/5.0 (Linux; Android 14) Lockdown"
    private const val PAUSE_MS = 400L                        // between two fetches of one worker
    private const val RUN_MS = 60_000L                       // a run starts no new site after this long
    private const val WORKERS = 2                            // at most this many sites at once

    private val mem = LruCache<String, ImageBitmap>(200)     // 200 x 64 px at most ~3 MB
    private val missing: MutableSet<String> = ConcurrentHashMap.newKeySet()   // main hosts with no icon (yet)
    private val busy: MutableSet<String> = ConcurrentHashMap.newKeySet()      // being fetched now
    @Volatile private var bundledNames: Set<String>? = null
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    /** Counts up when a fetched icon is saved; a letter tile reads it and looks again. */
    var arrived by mutableIntStateOf(0)
        private set

    private fun dir(ctx: Context) = File(ctx.filesDir, DIR)
    private fun key(hosts: List<String>) = hosts.joinToString(" ")

    /** The icon if it's already in memory. */
    fun peek(hosts: List<String>): ImageBitmap? = mem.get(key(hosts))

    /** Looked for and not found (nothing to load until a fetch saves one). */
    fun isMissing(hosts: List<String>): Boolean = hosts.isEmpty() || hosts.first() in missing

    /** The icon of a site item with these [hosts]: saved, else bundled, else null. Reads files - off the main thread. */
    fun load(ctx: Context, hosts: List<String>): ImageBitmap? {
        if (hosts.isEmpty()) return null
        mem.get(key(hosts))?.let { return it }
        val app = ctx.applicationContext
        val saved = hosts.firstNotNullOfOrNull { h ->
            File(dir(app), "$h.png").takeIf { it.isFile }?.let { f ->
                decodeFile(f) ?: run { f.delete(); null }          // a broken file goes; the next run fetches it again
            }
        }
        val bmp = saved ?: SiteIconChain.bundledFor(bundled(app), hosts)?.let { decodeAsset(app, it) }
        if (bmp != null) mem.put(key(hosts), bmp) else missing.add(hosts.first())
        return bmp
    }

    private fun bundled(ctx: Context): Set<String> = bundledNames
        ?: (runCatching { ctx.assets.list(ASSETS)?.toSet() }.getOrNull() ?: emptySet()).also { bundledNames = it }

    private fun decodeFile(f: File): ImageBitmap? = runCatching { BitmapFactory.decodeFile(f.path)?.asImageBitmap() }.getOrNull()

    private fun decodeAsset(ctx: Context, name: String): ImageBitmap? =
        runCatching { ctx.assets.open("$ASSETS/$name").use { BitmapFactory.decodeStream(it) }?.asImageBitmap() }.getOrNull()

    // ---- fetching ----

    /**
     * Keeps the icons of your lists' sites coming while the app is in front (call on resume, cancel the Job on pause):
     * once shortly after, and again two seconds after the lists (or the setting) change. A site is fetched once; a
     * failure waits (10 min, then longer) for a later app open; a saved icon older than 30 days is fetched again.
     */
    fun watch(ctx: Context): Job {
        val app = ctx.applicationContext
        return scope.launch {
            combine(Store.state, ModesStore.modes) { cfg, modes -> cfg.settings.siteIcons to SiteIconChain.wanted(cfg, modes).map { it.key } }
                .distinctUntilChanged()
                .collectLatest { delay(1500); run(app) }
        }
    }

    private suspend fun run(ctx: Context) {
        val cfg = Store.config
        if (!cfg.settings.siteIcons || !online(ctx)) return
        val now = System.currentTimeMillis()
        val dir = dir(ctx).apply { mkdirs() }
        val names = bundled(ctx)
        val saved = dir.listFiles { f -> f.name.endsWith(".png") }.orEmpty().associate { it.name.removeSuffix(".png") to it.lastModified() }
        val book0 = readBook(dir)
        val todo = SiteIconChain.plan(true, SiteIconChain.wanted(cfg, ModesStore.modes.value), now, book0,
            have = { w -> SiteIconChain.Have(SiteIconChain.bundledFor(names, w.hosts) != null, saved[w.key]) }, busy = { it in busy })
        if (todo.isEmpty()) return
        val lock = Any()
        var book = book0.tried(todo.size, now)
        val queue = Channel<String>(Channel.UNLIMITED).also { q -> todo.forEach { q.trySend(it) }; q.close() }
        val started = System.nanoTime()
        try {
            coroutineScope {
                repeat(WORKERS) {
                    launch(Dispatchers.IO) {
                        val job = currentCoroutineContext()[Job]
                        val alive = { job?.isActive != false }
                        for (domain in queue) {
                            if (!alive() || (System.nanoTime() - started) / 1_000_000 > RUN_MS) break
                            val ok = fetchOne(ctx, domain, dir, alive)
                            synchronized(lock) { if (ok == true) book = book.fetched(domain) else if (ok == false) book = book.failed(domain, System.currentTimeMillis()) }
                            delay(PAUSE_MS)
                        }
                    }
                }
            }
        } finally {
            withContext(NonCancellable) {
                runCatching {
                    synchronized(lock) { File(dir, "book.json").writeText(json.encodeToString(book.pruned(System.currentTimeMillis()))) }
                    val files = dir.listFiles { f -> f.name.endsWith(".png") }.orEmpty().associate { it.name to it.lastModified() }
                    SiteIconChain.toPrune(files).forEach { File(dir, it).delete() }
                }
            }
        }
    }

    /** Fetch and save one site's icon: true saved, false none found, null stopped (not a failure). */
    private fun fetchOne(ctx: Context, domain: String, dir: File, alive: () -> Boolean): Boolean? {
        if (!busy.add(domain)) return null
        try {
            val cfg = Store.config
            fun blocked(host: String): Boolean {
                val now = Enforce.ldt(TrustedTime.now(ctx))
                return SiteIconGate.blockedNow(cfg, host, now, ModesStore.current(now))
            }
            val png = SiteIconChain.fetch(domain, blocked(domain), Http(::blocked, alive), alive) { toIcon(it) }
            if (png == null) return if (alive()) false else null
            val tmp = File(dir, "$domain.png.tmp")
            tmp.writeBytes(png)
            if (!tmp.renameTo(File(dir, "$domain.png"))) { tmp.delete(); return false }
            missing.remove(domain)
            mem.evictAll()
            synchronized(this) { arrived += 1 }
            return true
        } catch (e: Exception) {
            return false
        } finally {
            busy.remove(domain)
        }
    }

    private fun readBook(dir: File): IconBook =
        runCatching { json.decodeFromString<IconBook>(File(dir, "book.json").readText()) }.getOrDefault(IconBook())

    private fun online(ctx: Context): Boolean = runCatching {
        val cm = ctx.getSystemService(ConnectivityManager::class.java)
        cm.getNetworkCapabilities(cm.activeNetwork)?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true
    }.getOrDefault(false)

    /**
     * An answer as a small PNG (at most 64 px, the same shape), or null when it isn't a usable picture: not an image,
     * under 16 px, absurdly big, or one flat colour. Decoded with bounds and a sample size, never in full.
     */
    internal fun toIcon(bytes: ByteArray): ByteArray? = runCatching {
        val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, o)
        val w = o.outWidth; val h = o.outHeight
        if (w <= 0 || h <= 0 || w > 4096 || h > 4096) return@runCatching null
        val o2 = BitmapFactory.Options().apply { inSampleSize = SiteIconChain.sampleFor(w, h); inPreferredConfig = Bitmap.Config.ARGB_8888 }
        val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, o2) ?: return@runCatching null
        if (maxOf(bmp.width, bmp.height) < SiteIconChain.MIN_PX || flat(bmp)) return@runCatching null
        val (fw, fh) = SiteIconChain.fitTo(bmp.width, bmp.height)
        val fit = if (fw == bmp.width && fh == bmp.height) bmp else Bitmap.createScaledBitmap(bmp, fw, fh, true)
        ByteArrayOutputStream().also { fit.compress(Bitmap.CompressFormat.PNG, 100, it) }.toByteArray()
    }.getOrNull()

    /** Nothing drawn: fully see-through, or every pixel the same colour. */
    private fun flat(b: Bitmap): Boolean {
        val px = IntArray(b.width * b.height)
        b.getPixels(px, 0, b.width, 0, 0, b.width, b.height)
        return px.all { (it ushr 24) == 0 } || px.all { it == px[0] }
    }

    /** The chain's network: https only, short timeouts, at most 200 KB an answer, a few redirects that are checked too -
     *  every address it is about to ask (also a redirect's or an icon on another host) must not be blocked right now,
     *  nor a loopback / LAN one. */
    private class Http(val blocked: (String) -> Boolean, val alive: () -> Boolean) : SiteIconChain.Net {
        override fun image(url: String): ByteArray? = get(url, cut = false)?.second

        override fun page(url: String): SiteIconChain.Page? =
            get(url, cut = true)?.let { (end, bytes) -> SiteIconChain.Page(end, String(bytes, Charsets.UTF_8)) }

        private fun get(start: String, cut: Boolean): Pair<String, ByteArray>? {
            var url = start
            repeat(4) {
                if (!alive() || !askable(url)) return null
                val c = URL(url).openConnection() as HttpsURLConnection
                try {
                    c.connectTimeout = 4000; c.readTimeout = 4000
                    c.instanceFollowRedirects = false; c.useCaches = false
                    c.setRequestProperty("User-Agent", UA)
                    c.setRequestProperty("Accept", "image/*,text/html;q=0.8,*/*;q=0.5")
                    val code = c.responseCode
                    if (code in 300..399) {
                        url = SiteIconChain.redirect(url, c.getHeaderField("Location")) { !blocked(it) } ?: return null
                        return@repeat
                    }
                    if (code != 200) return null
                    if (!cut && c.contentLengthLong > SiteIconChain.MAX_BYTES) return null
                    val bytes = read(c, cut) ?: return null
                    return url to bytes
                } finally {
                    c.disconnect()
                }
            }
            return null
        }

        private fun read(c: HttpsURLConnection, cut: Boolean): ByteArray? {
            val out = ByteArrayOutputStream()
            val buf = ByteArray(8192)
            c.inputStream.use { ins ->
                while (true) {
                    val n = ins.read(buf)
                    if (n < 0) break
                    val room = SiteIconChain.MAX_BYTES - out.size()
                    if (n > room) { if (!cut) return null; out.write(buf, 0, room); break }
                    out.write(buf, 0, n)
                    if (!alive()) return null
                }
            }
            return out.toByteArray()
        }

        /** https, a public host name that isn't blocked right now and doesn't resolve to this phone or a LAN. */
        private fun askable(url: String): Boolean = runCatching {
            val uri = URI(url)
            val host = uri.host?.lowercase() ?: return false
            if (!"https".equals(uri.scheme, true) || SiteIconChain.hostOf(host) == null || blocked(host)) return false
            InetAddress.getAllByName(host).none {
                it.isLoopbackAddress || it.isAnyLocalAddress || it.isSiteLocalAddress || it.isLinkLocalAddress || it.isMulticastAddress ||
                    (it is Inet6Address && (it.address[0].toInt() and 0xFE) == 0xFC)
            }
        }.getOrDefault(false)
    }
}

/** A site's real icon (saved or bundled) as soon as it's loaded off the main thread, else null - and again when a fetch
 *  saves one. [target] is a site item's space-separated host names. */
@Composable
fun rememberSiteIcon(target: String): ImageBitmap? {
    val ctx = LocalContext.current.applicationContext
    val hosts = remember(target) { SiteIconChain.hostsOf(target) }
    var icon by remember(target) { mutableStateOf(SiteIcons.peek(hosts)) }
    val arrived = if (icon == null) SiteIcons.arrived else 0    // (only a tile without an icon looks again when one arrives)
    LaunchedEffect(target, arrived) {
        if (icon == null && !SiteIcons.isMissing(hosts)) icon = withContext(Dispatchers.IO) { SiteIcons.load(ctx, hosts) }
    }
    return icon
}

/** A site's icon on a light tile (an icon with a see-through background stays readable in dark mode). */
@Composable
fun SiteIconTile(icon: ImageBitmap, size: Dp) {
    val shape = RoundedCornerShape(size * 0.3f)
    Box(Modifier.size(size).clip(shape).background(Color.White).border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape),
        contentAlignment = Alignment.Center) {
        Image(icon, null, Modifier.size(size * 0.7f))
    }
}
