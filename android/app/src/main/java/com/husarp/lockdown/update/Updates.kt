package com.husarp.lockdown.update

import android.content.Context
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Checks the GitHub releases for a newer version. Download/install is left to the release page. */
object Updates {
    private const val API = "https://api.github.com/repos/Husarp/lockdown/releases/latest"

    data class Release(val tag: String, val url: String)

    fun current(ctx: Context): String =
        runCatching { ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName ?: "?" }.getOrDefault("?")

    /** Latest release from GitHub, or null on any failure. Call off the main thread. */
    fun latest(): Release? = runCatching {
        val c = (URL(API).openConnection() as HttpURLConnection).apply {
            connectTimeout = 8000; readTimeout = 8000
            setRequestProperty("Accept", "application/vnd.github+json")
        }
        c.inputStream.bufferedReader().use {
            val j = JSONObject(it.readText())
            Release(j.getString("tag_name").removePrefix("v"), j.getString("html_url"))
        }
    }.getOrNull()

    /** True if [tag] is a higher version than [current] (numeric dot compare). */
    fun isNewer(tag: String, current: String): Boolean {
        fun parts(s: String) = s.filter { it.isDigit() || it == '.' }.split(".").mapNotNull { it.toIntOrNull() }
        val a = parts(tag); val b = parts(current)
        for (i in 0 until maxOf(a.size, b.size)) {
            val x = a.getOrElse(i) { 0 }; val y = b.getOrElse(i) { 0 }
            if (x != y) return x > y
        }
        return false
    }
}
