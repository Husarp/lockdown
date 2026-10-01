package com.husarp.lockdown.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.painter.BitmapPainter
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.graphics.drawable.toBitmap
import com.husarp.lockdown.engine.ItemType

/** An installed app's real launcher icon, falling back to an initial avatar if it can't be loaded. */
@Composable
fun AppIcon(pkg: String, fallbackName: String) {
    val ctx = LocalContext.current
    val painter = remember(pkg) {
        runCatching {
            BitmapPainter(ctx.packageManager.getApplicationIcon(pkg).toBitmap(96, 96).asImageBitmap())
        }.getOrNull()
    }
    if (painter != null) Image(painter, null, Modifier.size(40.dp).clip(RoundedCornerShape(12.dp)))
    else Avatar(fallbackName)
}

/** The right icon for a blocklist item: a real app icon for apps, an initial for sites. */
@Composable
fun ItemIcon(type: ItemType, target: String, name: String) {
    if (type == ItemType.APP) AppIcon(target, name) else Avatar(name)
}
