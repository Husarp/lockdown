package com.husarp.lockdown.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.husarp.lockdown.ui.theme.LockdownTheme
import com.husarp.lockdown.ui.theme.Numerals

/** A small uppercase section label, like the mockups' "RUNNING LOW". */
@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text.uppercase(), modifier = modifier,
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/** A rounded card on the low surface tint. */
@Composable
fun Card(
    modifier: Modifier = Modifier,
    color: Color = MaterialTheme.colorScheme.surfaceContainerLow,
    contentColor: Color = MaterialTheme.colorScheme.onSurface,
    shape: androidx.compose.ui.graphics.Shape = RoundedCornerShape(20.dp),
    padding: PaddingValues = PaddingValues(16.dp),
    onClick: (() -> Unit)? = null,
    content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit,
) {
    val base = Modifier.fillMaxWidth().let { if (onClick != null) it.clickable { onClick() } else it }
    Surface(modifier = modifier.then(base), color = color, contentColor = contentColor, shape = shape) {
        Column(Modifier.padding(padding), content = content)
    }
}

/** The big condensed numeral used for screen time / limits. */
@Composable
fun Numeral(text: String, sizeSp: Int = 44, color: Color = MaterialTheme.colorScheme.onSurface) {
    Text(text, style = Numerals.copy(fontSize = sizeSp.sp, lineHeight = (sizeSp + 2).sp), color = color)
}

/** Status pill: blocked / soon / allowed / paused. */
enum class Status { BLOCKED, SOON, ALLOWED, PAUSED }

@Composable
fun StatusChip(text: String, status: Status) {
    val cs = MaterialTheme.colorScheme
    val ex = LockdownTheme.extra
    val (bg, fg) = when (status) {
        Status.BLOCKED -> cs.errorContainer to cs.onErrorContainer
        Status.SOON -> ex.warningContainer to ex.onWarningContainer
        Status.ALLOWED -> ex.successContainer to ex.onSuccessContainer
        Status.PAUSED -> cs.surfaceContainerHigh to cs.onSurfaceVariant
    }
    Box(Modifier.background(bg, RoundedCornerShape(6.dp)).padding(horizontal = 8.dp, vertical = 3.dp)) {
        Text(text, style = MaterialTheme.typography.labelMedium, color = fg, fontWeight = FontWeight.SemiBold)
    }
}

/** A small selectable chip (rule type, mode category, filter). */
@Composable
fun Chip(text: String, selected: Boolean, modifier: Modifier = Modifier, icon: ImageVector? = null, onClick: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    val bg = if (selected) cs.primaryContainer else cs.surface
    val fg = if (selected) cs.onPrimaryContainer else cs.onSurface
    val border = if (selected) cs.primaryContainer else cs.outlineVariant
    Surface(
        modifier = modifier.clickable { onClick() },
        color = bg, contentColor = fg, shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, border),
    ) {
        Row(Modifier.padding(horizontal = 12.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            if (icon != null) Icon(icon, null, Modifier.size(16.dp))
            Text(text, style = MaterialTheme.typography.labelLarge)
        }
    }
}

/** A rounded-square initial avatar (Barlow), used on rule / app rows. */
@Composable
fun Avatar(letter: String, modifier: Modifier = Modifier, bg: Color = MaterialTheme.colorScheme.surfaceContainerHigh, fg: Color = MaterialTheme.colorScheme.onSurface) {
    Box(modifier.size(40.dp).background(bg, RoundedCornerShape(12.dp)), contentAlignment = Alignment.Center) {
        Text(letter.take(1).uppercase(), style = MaterialTheme.typography.titleMedium.copy(fontFamily = com.husarp.lockdown.ui.theme.Barlow, fontWeight = FontWeight.Bold), color = fg)
    }
}

/** A list row with an avatar, title + subtitle, and a trailing slot (status chip, switch, chevron). */
@Composable
fun ListRow(
    title: String,
    subtitle: String? = null,
    leading: (@Composable () -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null,
    onClick: (() -> Unit)? = null,
) {
    val base = Modifier.fillMaxWidth().let { if (onClick != null) it.clickable { onClick() } else it }
    Row(base.padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
        if (leading != null) leading()
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (subtitle != null) Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        if (trailing != null) trailing()
    }
}

/** A titled row with a switch on the right. */
@Composable
fun SwitchRow(title: String, subtitle: String? = null, checked: Boolean, onChange: (Boolean) -> Unit) {
    ListRow(title = title, subtitle = subtitle, trailing = { Switch(checked = checked, onCheckedChange = onChange) })
}

/** A horizontal segmented control (Day/Week/Month, By hand/Schedule). */
@Composable
fun Segmented(options: List<String>, selected: Int, modifier: Modifier = Modifier, onSelect: (Int) -> Unit) {
    val cs = MaterialTheme.colorScheme
    Surface(modifier = modifier.fillMaxWidth().height(40.dp), shape = RoundedCornerShape(20.dp), border = BorderStroke(1.dp, cs.outline), color = cs.surface) {
        Row {
            options.forEachIndexed { i, label ->
                val on = i == selected
                Box(
                    Modifier.weight(1f).fillMaxHeight()
                        .background(if (on) cs.primaryContainer else Color.Transparent)
                        .clickable { onSelect(i) },
                    contentAlignment = Alignment.Center,
                ) {
                    Text(label, style = MaterialTheme.typography.labelLarge, color = if (on) cs.onPrimaryContainer else cs.onSurface)
                }
            }
        }
    }
}

/** A thin divider matching the design's hairlines. */
@Composable
fun HairlineSpacer() { Spacer(Modifier.height(1.dp).fillMaxWidth().background(MaterialTheme.colorScheme.outlineVariant)) }
