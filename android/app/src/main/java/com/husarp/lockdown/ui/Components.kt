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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.border
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material3.TextButton
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.sp
import com.husarp.lockdown.ui.theme.Hue
import com.husarp.lockdown.ui.theme.tone
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
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
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

/** Status pill v2: blocked (red, or slate when only greyed) and running low are filled; allowed and paused are just
 *  coloured text with their icon, so a long list isn't a wall of green. */
@Composable
fun StatusChip(text: String, status: Status, grey: Boolean = false) {
    val look = Kinds.status(status, grey)
    val t = tone(look.hue)
    val filled = status == Status.BLOCKED || status == Status.SOON
    Row(
        Modifier.widthIn(max = 120.dp).then(if (filled) Modifier.background(t.container, RoundedCornerShape(6.dp)) else Modifier)
            .padding(horizontal = if (filled) 8.dp else 2.dp, vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Icon(look.icon, null, Modifier.size(14.dp), tint = t.accent)
        Text(text, style = MaterialTheme.typography.labelMedium, color = if (filled) t.onContainer else t.accent,
            fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

/** A small selectable chip (rule type, mode category, filter). [hue]: selected, it takes that hue's container (and
 *  its icon the accent) instead of the brand's. [iconTint]: the icon's colour while not selected. */
@Composable
fun Chip(text: String, selected: Boolean, modifier: Modifier = Modifier, icon: ImageVector? = null, hue: Hue? = null,
         iconTint: Color? = null, onClick: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    val t = hue?.let { tone(it) }
    val bg = if (!selected) cs.surface else t?.container ?: cs.primaryContainer
    val fg = if (!selected) cs.onSurface else t?.onContainer ?: cs.onPrimaryContainer
    val border = if (selected) bg else cs.outlineVariant
    Surface(
        modifier = modifier.clickable { onClick() },
        color = bg, contentColor = fg, shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, border),
    ) {
        Row(Modifier.padding(horizontal = 12.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            if (icon != null) Icon(icon, null, Modifier.size(16.dp), tint = if (selected) t?.accent ?: fg else iconTint ?: fg)
            Text(text, style = MaterialTheme.typography.labelLarge)
        }
    }
}

/** A rounded-square initial avatar (Barlow), used on rule / app rows. */
@Composable
fun Avatar(letter: String, modifier: Modifier = Modifier, bg: Color = MaterialTheme.colorScheme.surfaceContainerHigh,
           fg: Color = MaterialTheme.colorScheme.onSurface, size: Dp = 40.dp) {
    Box(modifier.size(size).background(bg, RoundedCornerShape(size * 0.3f)), contentAlignment = Alignment.Center) {
        Text(letter.take(1).uppercase(), style = MaterialTheme.typography.titleMedium.copy(fontFamily = com.husarp.lockdown.ui.theme.Barlow,
            fontWeight = FontWeight.Bold, fontSize = (size.value * 0.45f).sp), color = fg)
    }
}

/** A rounded square in a hue's container with the icon in its accent (12 dp corners at 40, 10 at 32, 8 at 28).
 *  [on] false: grey, so something that's off looks off. */
@Composable
fun IconTile(icon: ImageVector, hue: Hue, size: Dp = 40.dp, on: Boolean = true, modifier: Modifier = Modifier) {
    val cs = MaterialTheme.colorScheme
    val t = tone(hue)
    Box(modifier.size(size).background(if (on) t.container else cs.surfaceContainerHigh, RoundedCornerShape(size * 0.3f)),
        contentAlignment = Alignment.Center) {
        Icon(icon, null, Modifier.size(size * 0.55f), tint = if (on) t.accent else cs.onSurfaceVariant)
    }
}

/** A small label: filled (the hue's container) or outlined (a hairline, no colour). One line, at most 140 dp. */
@Composable
fun Pill(text: String, hue: Hue?, icon: ImageVector? = null, outlined: Boolean = false, modifier: Modifier = Modifier) {
    val cs = MaterialTheme.colorScheme
    val t = hue?.takeIf { !outlined }?.let { tone(it) }
    Row(
        modifier.height(22.dp).widthIn(max = 140.dp)
            .then(if (t != null) Modifier.background(t.container, RoundedCornerShape(6.dp))
                  else Modifier.border(1.dp, cs.outlineVariant, RoundedCornerShape(6.dp)))
            .padding(horizontal = 7.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        if (icon != null) Icon(icon, null, Modifier.size(12.dp), tint = t?.accent ?: cs.onSurfaceVariant)
        Text(text, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.SemiBold, maxLines = 1,
            overflow = TextOverflow.Ellipsis, color = t?.onContainer ?: cs.onSurfaceVariant)
    }
}

@Composable
fun Pill(spec: PillSpec) = Pill(spec.text, spec.hue, spec.icon, spec.outlined)

/** Pills on one line: of the first [max], those that fit whole, then "+N" for the rest - never a pill squeezed to
 *  a sliver, never the "+N" lost. When not even the first fits whole, it takes the room left, cut short. */
@Composable
fun PillRow(pills: List<PillSpec>, max: Int = 2, modifier: Modifier = Modifier) {
    if (pills.isEmpty()) return
    val shown = pills.take(max)
    val folds = (pills.size - shown.size..pills.size).filter { it > 0 }     // every "+N" it may come to
    Layout(content = {
        shown.forEach { Pill(it) }
        folds.forEach { Pill("+$it", null, outlined = true) }
        Pill(pills[0])                                                       // the first one, cut to the room left
    }, modifier = modifier) { ms, c ->
        val gap = 4.dp.roundToPx()
        val loose = c.copy(minWidth = 0)
        val p = ms.take(shown.size).map { it.measure(loose) }
        val plus = folds.indices.associate { folds[it] to ms[shown.size + it].measure(loose) }
        fun width(k: Int) = p.take(k).sumOf { it.width } + gap * (k - 1).coerceAtLeast(0) +
            (plus[pills.size - k]?.let { it.width + if (k > 0) gap else 0 } ?: 0)
        val k = (shown.size downTo 0).first { it == 0 || width(it) <= c.maxWidth }
        val placed = ArrayList<androidx.compose.ui.layout.Placeable>(p.take(k))
        if (k == 0) {
            val rest = plus[pills.size - 1]
            val room = c.maxWidth - (rest?.let { it.width + gap } ?: 0)
            if (room >= 48.dp.roundToPx()) { placed.add(ms.last().measure(loose.copy(maxWidth = room))); rest?.let { placed.add(it) } }
            else plus[pills.size]?.takeIf { it.width <= c.maxWidth }?.let { placed.add(it) }
        } else plus[pills.size - k]?.let { placed.add(it) }
        val w = placed.sumOf { it.width } + gap * (placed.size - 1).coerceAtLeast(0)
        val h = placed.maxOfOrNull { it.height } ?: 0
        layout(w.coerceIn(c.minWidth, c.maxWidth), h.coerceIn(c.minHeight, c.maxHeight)) {
            var x = 0
            placed.forEach { it.place(x, (h - it.height) / 2); x += it.width + gap }
        }
    }
}

/** An index card that opens a section: a tile, the title, a one-line live summary (red when [alert]) and a chevron
 *  (or [trailing]). [dot]: a small dot on the tile - red "needs a fix", amber "unsaved". */
@Composable
fun SectionCard(icon: ImageVector, hue: Hue, title: String, summary: String, alert: Boolean = false, dot: Color? = null,
                trailing: (@Composable () -> Unit)? = null, onClick: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    Card(shape = RoundedCornerShape(16.dp), padding = PaddingValues(horizontal = 14.dp, vertical = 12.dp), onClick = onClick) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            Box {
                IconTile(icon, hue)
                if (dot != null) Box(Modifier.align(Alignment.TopEnd).offset(x = 3.dp, y = (-3).dp).size(10.dp)
                    .background(cs.surfaceContainerLow, CircleShape).padding(1.dp).background(dot, CircleShape))
            }
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleSmall)
                Text(summary, style = MaterialTheme.typography.bodySmall, color = if (alert) cs.error else cs.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            if (trailing != null) trailing() else Icon(Icons.Filled.ChevronRight, null, tint = cs.onSurfaceVariant)
        }
    }
}

/** A Home card: a small tile and an uppercase title in the hue's accent, an optional "See all", then [content]. */
@Composable
fun HomePanel(title: String, icon: ImageVector?, hue: Hue?, action: String? = null, onAction: () -> Unit = {},
              onClick: (() -> Unit)? = null, color: Color = MaterialTheme.colorScheme.surfaceContainerLow,
              contentColor: Color = MaterialTheme.colorScheme.onSurface, content: @Composable ColumnScope.() -> Unit) {
    val cs = MaterialTheme.colorScheme
    Card(color = color, contentColor = contentColor, padding = PaddingValues(start = 16.dp, end = 8.dp, top = 10.dp, bottom = 12.dp), onClick = onClick) {
        Row(Modifier.fillMaxWidth().heightIn(min = 36.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            if (icon != null && hue != null) IconTile(icon, hue, 28.dp)
            Text(title.uppercase(), style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold,
                color = hue?.let { tone(it).accent } ?: cs.onSurfaceVariant, modifier = Modifier.weight(1f))
            if (action != null) TextButton(onClick = onAction) { Text(action) }
        }
        Column(Modifier.padding(end = 8.dp)) { content() }
    }
}

/** The first [max] members' icons, overlapping with a ring, then "+N". */
@Composable
fun MemberStack(items: List<com.husarp.lockdown.engine.Item>, max: Int = 3) {
    if (items.isEmpty()) return
    val cs = MaterialTheme.colorScheme
    val shown = items.take(max)
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.width((26 + 16 * (shown.size - 1)).dp).height(26.dp)) {
            shown.forEachIndexed { i, it ->
                Box(Modifier.offset(x = (16 * i).dp).size(26.dp).background(cs.surfaceContainerLow, RoundedCornerShape(9.dp)).padding(2.dp)) {
                    ItemIcon(it.type, it.target, it.name, 22.dp)
                }
            }
        }
        if (items.size > max) Text("+${items.size - max}", Modifier.padding(start = 4.dp), style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant)
    }
}

/** A list row with an avatar, title + subtitle, and a trailing slot (status chip, switch, chevron). [subtitlePill]: the
 *  subtitle as a small outlined label ("App · not on your list"). */
@Composable
fun ListRow(
    title: String,
    subtitle: String? = null,
    leading: (@Composable () -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null,
    subtitlePill: Boolean = false,
    onClick: (() -> Unit)? = null,
) {
    val base = Modifier.fillMaxWidth().let { if (onClick != null) it.clickable { onClick() } else it }
    Row(base.padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
        if (leading != null) leading()
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (subtitle != null && subtitlePill) Pill(subtitle, null, outlined = true, modifier = Modifier.padding(top = 3.dp))
            else if (subtitle != null) Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
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
