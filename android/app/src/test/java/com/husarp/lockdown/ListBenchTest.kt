package com.husarp.lockdown

import com.husarp.lockdown.engine.DomainSet
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * A measurement, not a check: the real protection lists (downloaded by hand into /tmp/ld-lists, never run) made into
 * site sets - how many sites, the memory, the build time and 100,000 lookups. Skipped when the folder isn't there.
 * Results go to /tmp/ld-bench.txt.
 */
class ListBenchTest {
    @Test fun measure_the_real_lists() {
        val dir = File(System.getProperty("ldLists") ?: "/tmp/ld-lists")
        assumeTrue(dir.isDirectory)
        val out = StringBuilder()
        var totalSites = 0L; var totalBytes = 0L
        val files = dir.listFiles()!!.filter { it.isFile }.sortedBy { it.name }
        // lighter versions of the big lists, measured on their own (not in the total)
        for (f in File(dir, "variants").listFiles()?.sortedBy { it.name } ?: emptyList()) {
            val set = DomainSet.Builder().apply { f.bufferedReader().useLines { seq -> seq.forEach { l -> DomainSet.listLine(l)?.let(::add) } } }.build()
            out.append("variant %-30s %9d sites  %7.2f MB in memory  %7.2f MB text\n".format(f.name, set.size, set.bytes / 1e6, f.length() / 1e6))
        }
        val sets = ArrayList<DomainSet>()
        val sample = ArrayList<String>()
        for (f in files) {
            val t0 = System.nanoTime()
            val b = DomainSet.Builder()
            var lines = 0
            f.bufferedReader().useLines { seq -> seq.forEach { l -> lines++; DomainSet.listLine(l)?.let { b.add(it); if (sample.size < 50_000 && lines % 17 == 0) sample.add(it) } } }
            val set = b.build()
            val ms = (System.nanoTime() - t0) / 1_000_000
            sets.add(set)
            totalSites += set.size; totalBytes += set.bytes
            val gz = java.io.ByteArrayOutputStream().also { o -> java.util.zip.GZIPOutputStream(o).use { z -> f.inputStream().use { it.copyTo(z) } } }.size()
            out.append("%-70s %9d sites  %7.2f MB in memory  %7.2f MB text  %6.2f MB gzip  built in %d ms\n"
                .format(f.relativeTo(dir).path, set.size, set.bytes / 1e6, f.length() / 1e6, gz / 1e6, ms))
        }
        out.append("TOTAL %d sites, %.1f MB as site sets\n".format(totalSites, totalBytes / 1e6))

        // the phone's own lists (block/Protection.kt LISTS), as the weekly refresh downloads them: phone/<key>-<n>.txt is
        // the list's n-th url, all of a list's sources in one set
        File(dir, "phone").takeIf { it.isDirectory }?.let { ph ->
            var sites = 0L; var bytes = 0L
            for (def in com.husarp.lockdown.block.Protection.LISTS) {
                val b = DomainSet.Builder()
                def.urls.indices.map { File(ph, "${def.key}-$it.txt") }.filter { it.isFile }
                    .forEach { f -> f.bufferedReader().useLines { seq -> seq.forEach { l -> DomainSet.listLine(l)?.let(b::add) } } }
                val set = b.build()
                sites += set.size; bytes += set.bytes
                out.append("phone %-10s %9d sites  %7.2f MB in memory  %s\n".format(def.key, set.size, set.bytes / 1e6, def.urls.joinToString(" + ")))
            }
            out.append("PHONE TOTAL %d sites, %.2f MB as site sets\n".format(sites, bytes / 1e6))
        }

        // 100,000 lookups against every set at once (as Protection.blocked with all lists on): half listed sites under a
        // subdomain, half made-up hosts
        val rnd = java.util.Random(1)
        val hosts = List(100_000) { i -> if (i % 2 == 0 && sample.isNotEmpty()) "www.cdn${i % 7}." + sample[rnd.nextInt(sample.size)] else "host$i.example-${rnd.nextInt(1000)}.com" }
        repeat(2) { for (h in hosts) sets.any { it.matches(h) } }     // warm-up
        val t0 = System.nanoTime()
        var hits = 0
        for (h in hosts) if (sets.any { it.matches(h) }) hits++
        val ns = System.nanoTime() - t0
        out.append("100000 lookups over %d sets: %d ms (%.2f µs each), %d blocked\n".format(sets.size, ns / 1_000_000, ns / 1e3 / hosts.size, hits))

        // the config with 6,000 imported sites: one item each (before) and the compact list (now)
        val json = kotlinx.serialization.json.Json { ignoreUnknownKeys = true; encodeDefaults = true }
        val items = (1..6000).map { val h = "site$it.example-domain.com"
            com.husarp.lockdown.engine.Item(java.util.UUID.randomUUID().toString().take(8), h, h, com.husarp.lockdown.engine.ItemType.SITE,
                rules = listOf(com.husarp.lockdown.engine.Rule(com.husarp.lockdown.engine.RuleType.PERMANENT))) }
        val old = com.husarp.lockdown.data.Config(items = items)
        val oldLen = json.encodeToString(com.husarp.lockdown.data.Config.serializer(), old).length
        val t1 = System.nanoTime()
        val new = com.husarp.lockdown.data.ImportedSites.migrate(old)
        val migMs = (System.nanoTime() - t1) / 1_000_000
        val newLen = json.encodeToString(com.husarp.lockdown.data.Config.serializer(), new).length
        out.append("config with 6000 imported sites: %.2f MB as items, %.2f MB compact (migrated in %d ms)\n".format(oldLen / 1e6, newLen / 1e6, migMs))
        File("/tmp/ld-bench.txt").writeText(out.toString())
    }
}
