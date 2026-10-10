package com.husarp.lockdown.ui

import android.content.Context
import android.content.pm.LauncherApps
import android.graphics.drawable.Drawable
import android.os.Process
import android.util.LruCache
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.core.graphics.drawable.toBitmap
import java.util.Collections

/**
 * Installed apps' launcher icons, decoded once (at 40 dp) and kept for every list: a scrolling list used to decode
 * each icon again on the main thread every time a row came back on screen. Up to 160 icons (~7 MB worst case); an
 * app without one is remembered so it isn't tried on every scroll. Smaller uses scale the cached bitmap.
 */
object IconCache {
    private val cache = LruCache<String, ImageBitmap>(160)
    private val missing: MutableSet<String> = Collections.synchronizedSet(HashSet())

    fun peek(pkg: String): ImageBitmap? = cache.get(pkg)

    fun isMissing(pkg: String): Boolean = pkg in missing

    /** The icon, decoding it at [px] if it isn't cached (call off the main thread), or null if the app has none. An
     *  app only in Island (the work profile) gets its icon from there, with Android's work badge. */
    fun load(ctx: Context, pkg: String, px: Int): ImageBitmap? {
        cache.get(pkg)?.let { return it }
        if (pkg in missing) return null
        val icon = runCatching { ctx.packageManager.getApplicationIcon(pkg) }.getOrNull() ?: otherProfileIcon(ctx, pkg)
        val bmp = icon?.let { runCatching { it.toBitmap(px, px).asImageBitmap() }.getOrNull() }
        if (bmp != null) cache.put(pkg, bmp) else missing.add(pkg)
        return bmp
    }

    /** [pkg]'s badged launcher icon from another profile of this phone (Island), or null. */
    private fun otherProfileIcon(ctx: Context, pkg: String): Drawable? = runCatching {
        val la = ctx.getSystemService(LauncherApps::class.java) ?: return@runCatching null
        val me = Process.myUserHandle()
        la.profiles.asSequence().filter { it != me }
            .firstNotNullOfOrNull { u -> runCatching { la.getActivityList(pkg, u).firstOrNull()?.getBadgedIcon(0) }.getOrNull() }
    }.getOrNull()
}
