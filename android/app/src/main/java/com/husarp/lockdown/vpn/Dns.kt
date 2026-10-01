package com.husarp.lockdown.vpn

import java.nio.ByteBuffer

/**
 * Minimal IPv4 + UDP + DNS handling for the filter VPN. We only ever see UDP/53 packets to our
 * sentinel DNS address, so this stays small: read the queried name, and either forward the query
 * upstream or answer it with NXDOMAIN.
 */
object Dns {

    /** The domain asked about in a DNS query packet (full IPv4/UDP/DNS bytes), or null if unparseable. */
    fun queriedName(ip: ByteBuffer, len: Int): String? = runCatching {
        val ihl = (ip.get(0).toInt() and 0x0F) * 4
        val dns = ihl + 8                         // IP header + UDP header
        val sb = StringBuilder()
        var p = dns + 12                          // skip the 12-byte DNS header to QNAME
        while (p < len) {
            val n = ip.get(p).toInt() and 0xFF
            if (n == 0) break
            if (sb.isNotEmpty()) sb.append('.')
            for (i in 1..n) sb.append((ip.get(p + i).toInt() and 0xFF).toChar())
            p += n + 1
        }
        sb.toString().ifEmpty { null }
    }.getOrNull()

    /** The client's UDP source port, so the reply can be addressed back to it. */
    private fun srcPort(ip: ByteBuffer): Int {
        val ihl = (ip.get(0).toInt() and 0x0F) * 4
        return ((ip.get(ihl).toInt() and 0xFF) shl 8) or (ip.get(ihl + 1).toInt() and 0xFF)
    }

    /** Build an NXDOMAIN reply to [query], addressed back to the client (swaps IP + port, sets 53 as source). */
    fun nxdomain(query: ByteBuffer, len: Int): ByteArray {
        val ihl = (query.get(0).toInt() and 0x0F) * 4
        val dnsStart = ihl + 8
        val dnsLen = len - dnsStart
        val out = ByteBuffer.allocate(len)

        // ---- IPv4 header ----
        out.put(query.get(0))                                   // version + IHL
        out.put(0)                                              // DSCP/ECN
        out.putShort(len.toShort())                            // total length
        out.putShort(0); out.putShort(0)                       // id, flags/frag
        out.put(64)                                             // TTL
        out.put(17)                                             // protocol UDP
        out.putShort(0)                                        // checksum (filled below)
        // swap source <-> dest addresses
        for (i in 0 until 4) out.put(query.get(16 + i))       // new src = old dst
        for (i in 0 until 4) out.put(query.get(12 + i))       // new dst = old src
        val ipChecksum = checksum(out.array(), 0, ihl)
        out.putShort(10, ipChecksum)

        // ---- UDP header ----
        out.position(ihl)
        out.putShort(53)                                       // src port
        out.putShort(srcPort(query).toShort())                // dst port = client's port
        out.putShort((8 + dnsLen).toShort())                  // UDP length
        out.putShort(0)                                        // checksum 0 (optional for IPv4)

        // ---- DNS: copy the query and flip it into an NXDOMAIN answer ----
        for (i in 0 until dnsLen) out.put(query.get(dnsStart + i))
        out.putShort(dnsStart + 2, 0x8183.toShort())          // QR=1, RD, RA, RCODE=3 (NXDOMAIN)
        out.putShort(dnsStart + 6, 0)                          // ANCOUNT = 0
        return out.array()
    }

    /** Wrap an upstream DNS [answer] (raw DNS bytes) back into an IPv4/UDP packet for the client. */
    fun wrapAnswer(query: ByteBuffer, answer: ByteArray): ByteArray {
        val ihl = (query.get(0).toInt() and 0x0F) * 4
        val total = ihl + 8 + answer.size
        val out = ByteBuffer.allocate(total)
        out.put(query.get(0)); out.put(0); out.putShort(total.toShort())
        out.putShort(0); out.putShort(0); out.put(64); out.put(17); out.putShort(0)
        for (i in 0 until 4) out.put(query.get(16 + i))
        for (i in 0 until 4) out.put(query.get(12 + i))
        out.putShort(10, checksum(out.array(), 0, ihl))
        out.position(ihl)
        out.putShort(53); out.putShort(srcPort(query).toShort())
        out.putShort((8 + answer.size).toShort()); out.putShort(0)
        out.put(answer)
        return out.array()
    }

    // ---- SafeSearch: answer search engines with their "always safe" address ----

    private val GOOGLE = Regex("^(www\\.)?google\\.(com|[a-z]{2}|co\\.[a-z]{2}|com\\.[a-z]{2})$")

    /** The safe-search hostname to answer with instead of [host], or null if [host] isn't a search engine. */
    fun safeHost(host: String): String? {
        val h = host.lowercase()
        return when {
            GOOGLE.matches(h) -> "forcesafesearch.google.com"
            h == "bing.com" || h == "www.bing.com" -> "strict.bing.com"
            h == "duckduckgo.com" || h == "www.duckduckgo.com" -> "safe.duckduckgo.com"
            h == "youtube.com" || h == "www.youtube.com" || h == "m.youtube.com" -> "restrictmoderate.youtube.com"
            else -> null
        }
    }

    /** The QTYPE of a DNS query packet (1 = A, 28 = AAAA). */
    fun queryType(ip: ByteBuffer, len: Int): Int {
        val ihl = (ip.get(0).toInt() and 0x0F) * 4
        var p = ihl + 8 + 12                       // IP + UDP + DNS header -> QNAME
        while (p < len && (ip.get(p).toInt() and 0xFF) != 0) p += (ip.get(p).toInt() and 0xFF) + 1
        p += 1                                      // the zero label
        return ((ip.get(p).toInt() and 0xFF) shl 8) or (ip.get(p + 1).toInt() and 0xFF)
    }

    /** A minimal DNS query (type A) for [host], to resolve the safe address upstream. */
    fun buildQuery(host: String): ByteArray {
        val out = ByteBuffer.allocate(512)
        out.putShort(0x1234)                        // id (any)
        out.putShort(0x0100.toShort())              // flags: RD
        out.putShort(1); out.putShort(0); out.putShort(0); out.putShort(0)   // qd=1
        for (label in host.split(".")) {
            out.put(label.length.toByte())
            for (c in label) out.put(c.code.toByte())
        }
        out.put(0)                                  // end of name
        out.putShort(1); out.putShort(1)            // QTYPE A, QCLASS IN
        return out.array().copyOf(out.position())
    }

    /** The IPv4 addresses (4 bytes each) in a DNS answer's A records. */
    fun extractA(answer: ByteArray): List<ByteArray> {
        val an = ((answer[6].toInt() and 0xFF) shl 8) or (answer[7].toInt() and 0xFF)
        var p = 12
        p = skipName(answer, p) + 4                  // question: name + QTYPE + QCLASS
        val ips = ArrayList<ByteArray>()
        repeat(an) {
            p = skipName(answer, p)
            val type = ((answer[p].toInt() and 0xFF) shl 8) or (answer[p + 1].toInt() and 0xFF)
            val rdlen = ((answer[p + 8].toInt() and 0xFF) shl 8) or (answer[p + 9].toInt() and 0xFF)
            val rdata = p + 10
            if (type == 1 && rdlen == 4) ips.add(answer.copyOfRange(rdata, rdata + 4))
            p = rdata + rdlen
        }
        return ips
    }

    private fun skipName(a: ByteArray, start: Int): Int {
        var p = start
        while (p < a.size) {
            val n = a[p].toInt() and 0xFF
            if (n == 0) return p + 1
            if (n and 0xC0 == 0xC0) return p + 2     // compression pointer
            p += n + 1
        }
        return p
    }

    /** Wrap A records for the ORIGINAL name (a SafeSearch answer), addressed back to the client. */
    fun safeAnswer(query: ByteBuffer, len: Int, ips: List<ByteArray>): ByteArray =
        wrapAnswer(query, dnsAnswer(dnsPayload(query, len), 0x8180, ips))

    /** An empty NOERROR answer (NODATA) for the original name - used for AAAA so the client falls back to A. */
    fun noData(query: ByteBuffer, len: Int): ByteArray =
        wrapAnswer(query, dnsAnswer(dnsPayload(query, len), 0x8180, emptyList()))

    /** A SERVFAIL reply, so a client fails fast instead of waiting for a timeout when no resolver answered. */
    fun servfail(query: ByteBuffer, len: Int): ByteArray =
        wrapAnswer(query, dnsAnswer(dnsPayload(query, len), 0x8182, emptyList()))

    /** Turn a query's DNS bytes (header + question) into an answer with the given flags and A records. */
    private fun dnsAnswer(query: ByteArray, flags: Int, ips: List<ByteArray>): ByteArray {
        val out = ByteBuffer.allocate(query.size + ips.size * 16)
        out.put(query)
        out.putShort(2, flags.toShort())                 // flags
        out.putShort(6, ips.size.toShort())              // ANCOUNT
        out.position(query.size)
        for (ip in ips) {
            out.putShort(0xC00C.toShort())               // name -> pointer to the question name (offset 12)
            out.putShort(1); out.putShort(1)             // type A, class IN
            out.putInt(300)                              // TTL
            out.putShort(4); out.put(ip)                 // rdlength + the address
        }
        return out.array().copyOf(out.position())
    }

    /** The DNS query bytes to forward upstream (strip the IP + UDP headers). */
    fun dnsPayload(ip: ByteBuffer, len: Int): ByteArray {
        val ihl = (ip.get(0).toInt() and 0x0F) * 4
        val start = ihl + 8
        val out = ByteArray(len - start)
        for (i in out.indices) out[i] = ip.get(start + i)
        return out
    }

    private fun checksum(data: ByteArray, offset: Int, length: Int): Short {
        var sum = 0L
        var i = offset
        while (i < offset + length - 1) {
            sum += ((data[i].toInt() and 0xFF) shl 8) or (data[i + 1].toInt() and 0xFF)
            i += 2
        }
        if (length % 2 == 1) sum += (data[offset + length - 1].toInt() and 0xFF) shl 8
        while (sum shr 16 != 0L) sum = (sum and 0xFFFF) + (sum shr 16)
        return sum.inv().toShort()
    }
}
