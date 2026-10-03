package com.husarp.lockdown.vpn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.DnsResolver
import android.net.Network
import android.net.NetworkCapabilities
import android.net.VpnService
import android.os.Build
import android.os.CancellationSignal
import android.os.ParcelFileDescriptor
import android.util.Log
import java.util.concurrent.CountDownLatch
import com.husarp.lockdown.MainActivity
import com.husarp.lockdown.R
import com.husarp.lockdown.block.Enforce
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import java.io.FileInputStream
import java.io.FileOutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.URL
import java.nio.ByteBuffer
import javax.net.ssl.HttpsURLConnection
import java.util.concurrent.Executors
import kotlin.concurrent.thread

/**
 * A local DNS-filter VPN. Android sends every app's DNS query to our sentinel address; we read the
 * name, block it (NXDOMAIN) or forward it upstream. It never leaves the phone and forwards nothing
 * but DNS. Android allows only one VPN at a time, and Private DNS must be off.
 */
class LockdownVpn : VpnService() {

    private var tun: ParcelFileDescriptor? = null
    @Volatile private var running = false
    private val pool = Executors.newCachedThreadPool()
    private lateinit var out: FileOutputStream
    private val writeLock = Any()
    @Volatile private var upstream: InetAddress = InetAddress.getByName(UPSTREAM)
    @Volatile private var underlying: Network? = null
    private var netCallback: ConnectivityManager.NetworkCallback? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == STOP) { stop(); return START_NOT_STICKY }
        startForeground(NOTIF_ID, notification())
        if (!running) start()
        return START_STICKY
    }

    private fun start() {
        pickUpstream()      // forward to the network's own resolver; some networks block public DNS like 1.1.1.1
        com.husarp.lockdown.block.Protection.load(this)   // load the enabled bundled blocklists into memory
        val fd = Builder()
            .setSession("Lockdown")
            .addAddress(SELF, 32)
            .addDnsServer(SENTINEL)
            .addRoute(SENTINEL, 32)
            .setMtu(1500)
            .setBlocking(true)
            .also { runCatching { it.addDisallowedApplication(packageName) } }  // our own traffic bypasses the tun
            .establish() ?: return
        tun = fd
        out = FileOutputStream(fd.fileDescriptor)
        running = true
        active = true
        watchNetwork()
        Store.update { it.copy(settings = it.settings.copy(siteFilterOn = true)) }
        thread(name = "lockdown-vpn") { loop(FileInputStream(fd.fileDescriptor)) }
    }

    private fun loop(input: FileInputStream) {
        val buf = ByteArray(32767)
        while (running) {
            val n = try { input.read(buf) } catch (e: Exception) { break }
            if (n <= 0) continue
            val packet = ByteBuffer.wrap(buf.copyOf(n))
            pool.execute { handle(packet, n) }
        }
    }

    private fun handle(packet: ByteBuffer, len: Int) {
        if ((packet.get(0).toInt() ushr 4) != 4) return          // IPv4 only
        val cfg = Store.config
        val host = Dns.queriedName(packet, len) ?: return
        val nowLdt = Enforce.ldt(com.husarp.lockdown.guard.TrustedTime.now(this))
        val mode = ModesStore.current(nowLdt)
        // A blocked site is cut off here only when its block method includes "cut the connection" ("dns", the
        // default); "go back" / "close" alone are done by the accessibility service in the browser.
        val verdict = Enforce.site(cfg, host, nowLdt, mode)
        val blocked = (verdict != null && "dns" in Enforce.siteFlags(verdict.blockType)) ||
            (cfg.enabled && com.husarp.lockdown.block.Protection.blocked(host))
        if (cfg.settings.networkLog) NetLog.add(host, blocked)
        if (blocked) {
            write(Dns.nxdomain(packet, len))
            return
        }

        // SafeSearch: answer search engines with their safe address (fall through to normal on any error)
        if (cfg.settings.forceSafeSearch) {
            val safe = Dns.safeHost(host)
            if (safe != null) when (Dns.queryType(packet, len)) {
                28 -> { write(Dns.noData(packet, len)); return }          // AAAA -> NODATA, pushes client to IPv4
                1 -> {
                    val ans = resolve(Dns.buildQuery(safe))
                    val ips = if (ans != null) Dns.extractA(ans) else emptyList()
                    if (ips.isNotEmpty()) { write(Dns.safeAnswer(packet, len, ips)); return }
                }
            }
        }

        // forward the query and hand the answer back to the app
        val ans = resolve(Dns.dnsPayload(packet, len))
        if (ans != null) write(Dns.wrapAnswer(packet, ans))
        else { write(Dns.servfail(packet, len)); Log.w("LockdownVpn", "no resolver reached for $host") }
    }

    /**
     * Resolve a DNS query: first the network's own resolver over UDP (fast), then DNS-over-HTTPS as a
     * fallback - which works when a firewall (e.g. AFWall) or a restrictive network blocks direct DNS,
     * because it's just HTTPS on 443 over the real network. Returns the DNS answer bytes, or null.
     */
    private fun resolve(query: ByteArray): ByteArray? {
        // 1) the OS resolver (netd) - works even behind an app firewall like AFWall, which allows netd's DNS
        resolveViaSystem(query)?.let { return it }
        // 2) direct UDP to the network's resolver (fast on unrestricted networks)
        runCatching {
            DatagramSocket().use { s ->
                protect(s); runCatching { underlying?.bindSocket(s) }
                s.soTimeout = 3000
                s.connect(InetSocketAddress(upstream, 53))
                s.send(DatagramPacket(query, query.size))
                val b = ByteArray(1500); val dp = DatagramPacket(b, b.size); s.receive(dp)
                return b.copyOf(dp.length)
            }
        }
        // 3) DNS-over-HTTPS (works when direct DNS is blocked but 443 is open)
        val net = underlying ?: getSystemService(ConnectivityManager::class.java)?.activeNetwork ?: return null
        Log.i("LockdownVpn", "falling back to DoH on $net (upstream was $upstream, underlying=$underlying)")
        for (host in DOH) runCatching {
            val conn = net.openConnection(URL("https://$host/dns-query")) as HttpsURLConnection
            conn.requestMethod = "POST"; conn.doOutput = true
            conn.connectTimeout = 5000; conn.readTimeout = 5000
            conn.setRequestProperty("Content-Type", "application/dns-message")
            conn.setRequestProperty("Accept", "application/dns-message")
            conn.outputStream.use { it.write(query) }
            if (conn.responseCode == 200) return conn.inputStream.use { it.readBytes() }
        }
        return null
    }

    /** Resolve via Android's own resolver (netd). Our app is excluded from the tun (addDisallowedApplication),
     *  so its default network is the real one; a null network here means "the process default". */
    private fun resolveViaSystem(query: ByteArray): ByteArray? {
        if (Build.VERSION.SDK_INT < 29) return null
        val out = arrayOfNulls<ByteArray>(1)
        val latch = CountDownLatch(1)
        runCatching {
            DnsResolver.getInstance().rawQuery(
                null, query, DnsResolver.FLAG_EMPTY, { it.run() }, CancellationSignal(),
                object : DnsResolver.Callback<ByteArray> {
                    override fun onAnswer(answer: ByteArray, rcode: Int) { out[0] = answer; latch.countDown() }
                    override fun onError(error: DnsResolver.DnsException) {
                        Log.w("LockdownVpn", "system resolver error: $error"); latch.countDown()
                    }
                },
            )
            latch.await(5, java.util.concurrent.TimeUnit.SECONDS)
        }
        return out[0]
    }

    private fun write(bytes: ByteArray) {
        synchronized(writeLock) { runCatching { out.write(bytes) } }
    }

    /** Pick the phone's real DNS server (and the network it's on) to forward allowed queries to. The active
     *  (current physical) network first, then any other non-VPN network with a DNS server. */
    private fun pickUpstream() {
        val cm = getSystemService(ConnectivityManager::class.java) ?: return
        val ordered = (listOfNotNull(cm.activeNetwork) + cm.allNetworks.toList()).distinct()
        for (n in ordered) {
            val caps = cm.getNetworkCapabilities(n) ?: continue
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) continue
            if (!caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) continue
            val dns = cm.getLinkProperties(n)?.dnsServers?.firstOrNull { it is Inet4Address } ?: continue
            upstream = dns
            underlying = n
            runCatching { setUnderlyingNetworks(arrayOf(n)) }
            Log.i("LockdownVpn", "upstream=$dns underlying=$n")
            return
        }
        Log.w("LockdownVpn", "no network DNS found; using default $upstream")
    }

    /** Re-pick the upstream when the phone switches network (Wi-Fi <-> mobile), so it never goes stale. */
    private fun watchNetwork() {
        val cm = getSystemService(ConnectivityManager::class.java) ?: return
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) = pickUpstream()
            override fun onLost(network: Network) = pickUpstream()
            override fun onLinkPropertiesChanged(network: Network, lp: android.net.LinkProperties) = pickUpstream()
        }
        runCatching { cm.registerDefaultNetworkCallback(cb); netCallback = cb }
    }

    private fun unwatchNetwork() {
        val cb = netCallback ?: return
        runCatching { getSystemService(ConnectivityManager::class.java)?.unregisterNetworkCallback(cb) }
        netCallback = null
    }

    private fun stop() {
        running = false
        active = false
        unwatchNetwork()
        Store.update { it.copy(settings = it.settings.copy(siteFilterOn = false)) }
        runCatching { tun?.close() }
        tun = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        running = false
        active = false
        unwatchNetwork()
        pool.shutdownNow()
        runCatching { tun?.close() }
        super.onDestroy()
    }

    override fun onRevoke() { stop() }

    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, "Site filter", NotificationManager.IMPORTANCE_LOW).apply {
                    description = "Keeps the blocked-site filter running."
                },
            )
        }
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(this, CHANNEL)
            .setContentTitle("Lockdown is filtering sites")
            .setContentText("Blocked-site protection is on.")
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val SELF = "10.111.0.2"
        private const val SENTINEL = "10.111.0.3"
        private const val UPSTREAM = "1.1.1.1"
        private val DOH = listOf("1.1.1.1", "8.8.8.8", "9.9.9.9")   // DoH endpoints (valid TLS certs for these IPs)
        private const val CHANNEL = "site_filter"
        private const val NOTIF_ID = 42
        const val STOP = "com.husarp.lockdown.vpn.STOP"

        @Volatile var active = false
            private set

        fun start(ctx: Context) = ctx.startService(Intent(ctx, LockdownVpn::class.java))
        fun stop(ctx: Context) = ctx.startService(Intent(ctx, LockdownVpn::class.java).setAction(STOP))

        /** Returns an intent to ask the user's consent, or null if already granted. */
        fun prepare(ctx: Context): Intent? = VpnService.prepare(ctx)
    }
}
