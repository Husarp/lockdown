package com.husarp.lockdown

import com.husarp.lockdown.vpn.Dns
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DnsTest {

    @Test fun safe_host_maps_search_engines() {
        assertEquals("forcesafesearch.google.com", Dns.safeHost("www.google.com"))
        assertEquals("forcesafesearch.google.com", Dns.safeHost("google.com"))
        assertEquals("forcesafesearch.google.com", Dns.safeHost("google.co.uk"))
        assertEquals("strict.bing.com", Dns.safeHost("bing.com"))
        assertEquals("safe.duckduckgo.com", Dns.safeHost("duckduckgo.com"))
        assertEquals("restrictmoderate.youtube.com", Dns.safeHost("m.youtube.com"))
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
}
