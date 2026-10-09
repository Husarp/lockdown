package com.husarp.lockdown

import com.husarp.lockdown.vpn.Dns
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer

class DnsTest {

    @Test fun safe_host_maps_search_engines() {
        assertEquals("forcesafesearch.google.com", Dns.safeHost("www.google.com"))
        assertEquals("forcesafesearch.google.com", Dns.safeHost("google.com"))
        assertEquals("forcesafesearch.google.com", Dns.safeHost("google.co.uk"))
        assertEquals("strict.bing.com", Dns.safeHost("bing.com"))
        assertEquals("safe.duckduckgo.com", Dns.safeHost("duckduckgo.com"))
        assertEquals("restrictmoderate.youtube.com", Dns.safeHost("m.youtube.com"))
        assertEquals("restrictmoderate.youtube.com", Dns.safeHost("www.youtube-nocookie.com"))   // Duck Player
        assertNull(Dns.safeHost("example.com"))
        assertNull(Dns.safeHost("notgoogle.com"))
    }

    @Test fun build_query_encodes_the_name() {
        val q = Dns.buildQuery("example.com")
        // parse the QNAME back from offset 12
        var p = 12
        val sb = StringBuilder()
        while (q[p].toInt() != 0) {
            val n = q[p].toInt() and 0xFF
            if (sb.isNotEmpty()) sb.append('.')
            for (i in 1..n) sb.append((q[p + i].toInt() and 0xFF).toChar())
            p += n + 1
        }
        assertEquals("example.com", sb.toString())
        // QTYPE A right after the terminating zero
        assertEquals(1, ((q[p + 1].toInt() and 0xFF) shl 8) or (q[p + 2].toInt() and 0xFF))
    }

    @Test fun extract_a_reads_ipv4_answers() {
        val ans = byteArrayOf(
            0x12, 0x34,                                     // id
            0x81.toByte(), 0x80.toByte(),                   // flags
            0x00, 0x01,                                     // qd = 1
            0x00, 0x01,                                     // an = 1
            0x00, 0x00, 0x00, 0x00,                         // ns, ar
            // question: "a.com" A IN
            0x01, 'a'.code.toByte(), 0x03, 'c'.code.toByte(), 'o'.code.toByte(), 'm'.code.toByte(), 0x00,
            0x00, 0x01, 0x00, 0x01,
            // answer: pointer, A, IN, ttl, rdlen 4, 1.2.3.4
            0xC0.toByte(), 0x0C, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0x01, 0x2C, 0x00, 0x04,
            1, 2, 3, 4,
        )
        val ips = Dns.extractA(ans)
        assertEquals(1, ips.size)
        assertEquals(listOf(1, 2, 3, 4), ips[0].map { it.toInt() and 0xFF })
    }

    @Test fun encrypted_dns_servers_are_known_with_their_subdomains() {
        for (h in listOf("dns.google", "chrome.cloudflare-dns.com", "mozilla.cloudflare-dns.com", "one.one.one.one",
            "dns.quad9.net", "abc123.dns.nextdns.io", "family.adguard-dns.com", "doh.opendns.com", "DNS.Google.",
            "use-application-dns.net"))
            assertTrue(h, Dns.encryptedDns(h))
        for (h in listOf("google.com", "www.google.com", "cloudflare.com", "nextdns.io", "quad9.net", "example.com",
            "notdns.google", "dns.google.evil.com", "duckduckgo.com", "com", ""))
            assertFalse(h, Dns.encryptedDns(h))
    }

    /** An IPv4 packet: 20-byte header with [proto], then a transport header with [dstPort], padded to 60 bytes. */
    private fun packet(proto: Int, dstPort: Int): ByteBuffer {
        val b = ByteBuffer.allocate(60)
        b.put(0, 0x45.toByte()); b.put(9, proto.toByte())
        b.putShort(20, 40000.toShort()); b.putShort(22, dstPort.toShort())
        return b
    }

    @Test fun only_udp_53_is_a_query() {
        assertTrue(Dns.isQuery(packet(17, 53), 60))
        assertFalse(Dns.isQuery(packet(6, 853), 60))      // Android's DNS-over-TLS probe of the sentinel
        assertFalse(Dns.isQuery(packet(6, 53), 60))       // DNS over TCP
        assertFalse(Dns.isQuery(packet(17, 443), 60))     // QUIC
        assertFalse(Dns.isQuery(packet(17, 53), 30))      // too short to hold a DNS header
    }
}
