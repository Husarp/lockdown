package com.husarp.lockdown.engine

import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream

/**
 * A big set of site names kept small and fast (as the PC's protection lists, src/blocker/protection.py): each name is a
 * 64-bit hash in one sorted array - 8 bytes a site, not a ~100-byte String in a HashSet - and a lookup is a binary
 * search for the host and each parent domain (a few microseconds). A listed name blocks its subdomains too
 * (as every list on the phone always did). Two names sharing a hash is a 1-in-10^12 chance for a million-site list.
 * Pure, so tested; block/Protection.kt keeps the protection lists in these, engine/ImportedSites.kt the imported sites.
 */
class DomainSet private constructor(private val hashes: LongArray) {
    val size: Int get() = hashes.size

    /** The memory the set takes (bytes). */
    val bytes: Long get() = 8L * hashes.size

    fun hasName(name: String): Boolean = name.isNotEmpty() && java.util.Arrays.binarySearch(hashes, hash(name)) >= 0

    /** Is [host] listed, or under a listed domain? "www." and a trailing dot don't matter; [allowed] (already lower
     *  case) wins at each level as Protection always had it: an allowed host or parent frees what is under it, unless
     *  something more exact is itself listed. */
    fun matches(host: String, allowed: Set<String> = emptySet()): Boolean {
        if (hashes.isEmpty()) return false
        var d = norm(host)
        if (d.isEmpty()) return false
        while (true) {
            if (d in allowed) return false
            if (hasName(d)) return true
            val dot = d.indexOf('.'); if (dot < 0) return false
            d = d.substring(dot + 1)
        }
    }

    /** The set as a small file: a header and the sorted hashes ([read] takes it back). */
    fun write(out: OutputStream) {
        val o = DataOutputStream(out.buffered(1 shl 16))
        o.writeInt(MAGIC); o.writeInt(hashes.size)
        for (h in hashes) o.writeLong(h)
        o.flush()
    }

    /** Collects names, then [build]s the set (sorted, repeats dropped). */
    class Builder {
        private var a = LongArray(1024)
        private var n = 0
        val count: Int get() = n
        fun add(name: String): Builder {
            val d = norm(name)
            if (d.isEmpty()) return this
            if (n == a.size) a = a.copyOf(a.size * 2)
            a[n++] = hash(d)
            return this
        }
        fun build(): DomainSet {
            val s = a.copyOf(n); s.sort()
            var k = 0
            for (i in s.indices) if (k == 0 || s[i] != s[k - 1]) s[k++] = s[i]
            return DomainSet(if (k == s.size) s else s.copyOf(k))
        }
    }

    companion object {
        private const val MAGIC = 0x4C445331          // "LDS1"
        val EMPTY = DomainSet(LongArray(0))

        /** A name as it's kept: lower case, no "*." (a wildcard line), "www." or trailing dot. */
        fun norm(name: String): String = name.trim().lowercase().removePrefix("*.").trimEnd('.').removePrefix("www.")

        fun of(names: Iterable<String>): DomainSet = Builder().apply { names.forEach { add(it) } }.build()

        /** A set written by [write]; an IOException for anything else (a cut-off or foreign file). */
        fun read(input: InputStream): DomainSet {
            val i = DataInputStream(input.buffered(1 shl 16))
            if (i.readInt() != MAGIC) throw IOException("not a site set")
            val n = i.readInt()
            if (n < 0 || n > 50_000_000) throw IOException("bad size")
            val a = LongArray(n)
            for (k in 0 until n) a[k] = i.readLong()
            for (k in 1 until n) if (a[k] <= a[k - 1]) throw IOException("not sorted")
            return DomainSet(a)
        }

        /** How many names a file written by [write] holds, from its header alone (-1 when it isn't one). */
        fun countOf(input: InputStream): Int = runCatching {
            val i = DataInputStream(input)
            if (i.readInt() != MAGIC) -1 else i.readInt()
        }.getOrDefault(-1)

        // the PC's check (src/blocker/protection.py), with punycode top-level domains (xn--p1ai = .рф) let through too
        private val DOMAIN = Regex("^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9])?\\.)+(?=[a-z0-9-]*[a-z])[a-z0-9-]{2,63}$")

        /** The site name on one line of a downloaded list (a plain or "*." wildcard domain, or a hosts-file line), or
         *  null: comments, blanks, localhost, IP addresses and anything not a domain (a web page sent instead of
         *  the list) count for nothing. */
        fun listLine(line: String): String? {
            val t = line.substringBefore('#').trim().lowercase()
            if (t.isEmpty() || t[0] == '!' || t[0] == '[') return null
            val parts = t.split(' ', '\t').filter { it.isNotEmpty() }
            val name = (if (parts.size > 1 && parts[0][0].isDigit()) parts[1] else parts[0]).removePrefix("*.").trimEnd('.')
            return name.takeIf { it != "localhost" && it != "localhost.localdomain" && DOMAIN.matches(it) }
        }

        /** 64-bit FNV-1a over the name's characters, then mixed (MurmurHash3's finaliser) so close names spread out. */
        fun hash(s: String): Long {
            var h = -0x340d631b7bdddcdbL       // FNV offset basis 0xcbf29ce484222325
            for (c in s) { h = h xor c.code.toLong(); h *= 0x100000001b3L }
            h = h xor (h ushr 33); h *= -0xae502812aa7333L
            h = h xor (h ushr 33); h *= -0x3b314601e57a13adL
            return h xor (h ushr 33)
        }
    }
}
