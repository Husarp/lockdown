package com.husarp.lockdown.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.BusinessCenter
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.ui.theme.tone
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** An app's icon from [IconCache], loaded off the main thread (null until it's there, or if it has none). */
@Composable
fun rememberAppIcon(pkg: String): ImageBitmap? {
    val ctx = LocalContext.current.applicationContext
    val px = with(LocalDensity.current) { 40.dp.roundToPx() }
    var icon by remember(pkg) { mutableStateOf(IconCache.peek(pkg)) }
    LaunchedEffect(pkg) {
        if (icon == null && !IconCache.isMissing(pkg)) icon = withContext(Dispatchers.IO) { IconCache.load(ctx, pkg, px) }
    }
    return icon
}

/** An installed app's real launcher icon. Until it's loaded (or if it has none) a letter tile of the same size, so
 *  nothing jumps; an app only in Island ("Name (Island)") gets a small work-profile badge on it. */
@Composable
fun AppIcon(pkg: String, fallbackName: String, size: Dp = 40.dp) {
    val icon = rememberAppIcon(pkg)
    if (icon != null) Image(icon, null, Modifier.size(size).clip(RoundedCornerShape(size * 0.3f)))
    else Box {
        Avatar(fallbackName, size = size)
        if (fallbackName.endsWith("(Island)") && size >= 32.dp) {
            val cs = MaterialTheme.colorScheme
            Icon(Icons.Filled.BusinessCenter, "Island", Modifier.align(Alignment.BottomEnd).offset(x = 3.dp, y = 3.dp).size(16.dp)
                .background(cs.surface, CircleShape).padding(2.dp), tint = cs.onSurfaceVariant)
        }
    }
}

/** The right icon for a blocklist item: a real app icon for apps; for sites a letter tile in a colour that stays the
 *  same for the site (never red or orange - no favicons, nothing is fetched). */
@Composable
fun ItemIcon(type: ItemType, target: String, name: String, size: Dp = 40.dp) {
    if (type == ItemType.APP) AppIcon(target, name, size)
    else {
        val t = tone(Kinds.siteHue(target))
        Avatar(name, bg = t.container, fg = t.onContainer, size = size)
    }
}
