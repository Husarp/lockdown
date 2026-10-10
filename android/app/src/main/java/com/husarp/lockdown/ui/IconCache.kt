package com.husarp.lockdown.ui

import android.content.Context
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

    /** The icon, decoding it at [px] if it isn't cached (call off the main thread), or null if the app has none. */
    fun load(ctx: Context, pkg: String, px: Int): ImageBitmap? {
        cache.get(pkg)?.let { return it }
        if (pkg in missing) return null
        val bmp = runCatching { ctx.packageManager.getApplicationIcon(pkg).toBitmap(px, px).asImageBitmap() }.getOrNull()
        if (bmp != null) cache.put(pkg, bmp) else missing.add(pkg)
        return bmp
    }
}
