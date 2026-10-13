package com.husarp.lockdown

import com.husarp.lockdown.block.Protection
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The phone's protection lists: the lighter sources for the three biggest, the others as before. */
class ProtectionListsTest {
    private val hagezi = "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@main/wildcard/"
    private fun urls(key: String) = Protection.LISTS.first { it.key == key }.urls

    @Test fun the_big_three_are_the_light_versions() {
        assertEquals(listOf(hagezi + "tif.mini-onlydomains.txt"), urls("malware"))
        assertEquals(listOf(hagezi + "gambling.mini-onlydomains.txt"), urls("gambling"))
        assertEquals(listOf(hagezi + "nsfw-onlydomains.txt"), urls("adult"))
        val all = Protection.LISTS.flatMap { it.urls }
        // the full lists never come down again
        assertFalse(all.any { it.endsWith("/tif-onlydomains.txt") || it.endsWith("/gambling-onlydomains.txt") || "porn" in it })
    }

    @Test fun the_others_are_unchanged() {
        assertEquals(listOf(hagezi + "fake-onlydomains.txt"), urls("scam"))
        assertEquals(listOf(hagezi + "anti.piracy-onlydomains.txt"), urls("anime"))
        assertEquals(listOf("https://cdn.jsdelivr.net/gh/blocklistproject/Lists@master/alt-version/phishing-nl.txt",
            "https://phishing.army/download/phishing_army_blocklist.txt"), urls("phishing"))
        assertEquals(listOf("adult", "gambling", "anime", "scam", "phishing", "malware"), Protection.LISTS.map { it.key })
    }

    @Test fun a_changed_source_gets_a_new_file_so_the_old_download_is_known() {
        val file = Protection.LISTS.associate { it.key to it.file }
        // Malware / Gambling: a new name, so the full list saved as malware.bin / gambling.bin is the stale one
        assertEquals("malware.mini", file["malware"]); assertEquals("gambling.mini", file["gambling"])
        // the rest keep their file (a download made before stays in use)
        for (k in listOf("adult", "anime", "scam", "phishing")) assertEquals(k, file[k])
        assertTrue(Protection.LISTS.map { it.file }.toSet().size == Protection.LISTS.size)
    }

    /** A Context that only has a files folder (all Protection's file handling needs). */
    private fun ctx(dir: java.io.File) = object : android.content.ContextWrapper(null) { override fun getFilesDir() = dir }
    private fun write(f: java.io.File, n: Int) {
        f.parentFile!!.mkdirs()
        f.outputStream().use { com.husarp.lockdown.engine.DomainSet.of((1..n).map { "s$it.example.com" }).write(it) }
    }

    @Test fun a_stale_full_list_stays_until_the_refresh_then_goes() {
        val dir = kotlin.io.path.createTempDirectory("prot").toFile()
        try {
            val c = ctx(dir)
            val full = java.io.File(dir, "protection/malware.bin"); write(full, 50)        // the old full list
            assertEquals(50 to true, Protection.info(c, "malware"))                       // still in use meanwhile
            Protection.dropStale(c, listOf("malware"))                                    // on: kept for the refresh
            assertTrue(full.exists())
            Protection.dropStale(c, emptyList())                                          // off: not refreshed, so deleted
            assertFalse(full.exists())
            // the light download is used once there; another list's own file isn't stale
            write(java.io.File(dir, "protection/malware.mini.bin"), 20)
            write(java.io.File(dir, "protection/adult.bin"), 30)
            Protection.dropStale(c, emptyList())
            assertEquals(20 to true, Protection.info(c, "malware"))
            assertEquals(30 to true, Protection.info(c, "adult"))
            // an older version's text list becomes the stale file (old source), not the light one's
            java.io.File(dir, "protection/malware.mini.bin").delete()
            java.io.File(dir, "protection/gambling.txt").writeText("a.example.com\nb.example.com\n")
            assertEquals(2 to true, Protection.info(c, "gambling"))
            assertTrue(java.io.File(dir, "protection/gambling.bin").exists())
            assertFalse(java.io.File(dir, "protection/gambling.mini.bin").exists())
        } finally { dir.deleteRecursively() }
    }
}
