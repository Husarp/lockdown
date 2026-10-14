package com.husarp.lockdown.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Modes
import com.husarp.lockdown.guard.rememberGuard
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.Kinds
import com.husarp.lockdown.ui.theme.tone

/*
 * Productive / Neutral / Distracting for apps and sites (PC: the Screen Time page). Modes block by these: Work,
 * Study, Focus and Do Not Disturb block what's Distracting. Marking something less blocked than a mode blocks it
 * (Distracting -> Productive) asks for the challenge; the other way is free. Saved at once, in the config, so the
 * Island copy gets it too.
 */

private val CATEGORY_NAMES = listOf("productive" to "Productive", "neutral" to "Neutral", "distracting" to "Distracting")

/** The keys an item's category is kept under: its app, or each of its hostnames. */
private fun categoryKeys(item: Item): List<String> =
    if (item.type == ItemType.APP) listOf("app:${item.target.lowercase()}")
    else item.target.lowercase().split(" ").filter { it.isNotEmpty() }.map { "site:$it" }

/** set(keys, old category or null, new category), through the challenge when it loosens a mode. */
@Composable
private fun rememberCategorySetter(): (List<String>, String?, String) -> Unit {
    val guard = rememberGuard()
    val modes by ModesStore.modes.collectAsStateWithLifecycle()
    return { keys, old, new ->
        guard(Modes.categoryLooser(modes, old, new)) {
            Store.update { c -> c.copy(categories = c.categories + keys.associateWith { new }) }
        }
    }
}

/** The item editor's "In modes it counts as" chips. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun CategoryChips(item: Item) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val set = rememberCategorySetter()
    val current = Modes.itemCategory(item, cfg.categories)
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        CATEGORY_NAMES.forEach { (key, label) ->
            val look = Kinds.category(key)
            Chip(label, key == current, icon = look.icon, hue = look.hue) { if (key != current) set(categoryKeys(item), current, key) }
        }
    }
    Text("Work, Study, Focus and Do Not Disturb block what's Distracting. Saved at once.",
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

/** An app's category: a listed app is Distracting until chosen otherwise; another app is in no category (null)
 *  until chosen. */
fun appCategory(cfg: com.husarp.lockdown.data.Config, pkg: String): String? {
    val listed = cfg.items.firstOrNull { it.type == ItemType.APP && it.target.equals(pkg, true) }
    return listed?.let { Modes.itemCategory(it, cfg.categories) } ?: cfg.categories["app:${pkg.lowercase()}"]
}

/** A site's category: a listed site follows its item (Distracting unless chosen otherwise); another site is in no
 *  category (null) until chosen. */
fun siteCategory(cfg: com.husarp.lockdown.data.Config, host: String): String? {
    val listed = cfg.items.firstOrNull { it.type == ItemType.SITE && com.husarp.lockdown.block.Enforce.hostMatches(it.target, host) }
    return listed?.let { Modes.itemCategory(it, cfg.categories) } ?: cfg.categories["site:${host.lowercase()}"]
}

/** A small "Distracting ▾" menu for an app in Insights (an app on your list, or any other app) - or, with [site], for
 *  a site (the item on your list it belongs to, or the site itself). */
@Composable
fun CategoryMenu(pkg: String, site: String? = null, modifier: Modifier = Modifier) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val set = rememberCategorySetter()
    var open by remember { mutableStateOf(false) }
    val current = if (site != null) siteCategory(cfg, site) else appCategory(cfg, pkg)
    val keys = if (site != null) {
        cfg.items.firstOrNull { it.type == ItemType.SITE && com.husarp.lockdown.block.Enforce.hostMatches(it.target, site) }
            ?.let(::categoryKeys) ?: listOf("site:${site.lowercase()}")
    } else listOf("app:${pkg.lowercase()}")
    Box(modifier, contentAlignment = Alignment.CenterEnd) {
        // As wide as its widest label whatever it says now, so the Top apps rows all keep the same room for the name
        // (a "Distracting" row's was shorter than a "Neutral" one's).
        (CATEGORY_NAMES.map { it.second } + "Category").forEach {
            Text("$it ▾", Modifier.padding(horizontal = 12.dp).alpha(0f).clearAndSetSemantics {},
                style = MaterialTheme.typography.labelMedium)
        }
        // a low button, so it sits on the name's line (the rows put it there, aligned by the text's baseline)
        SmallTextButton(onClick = { open = true }) {
            Text((CATEGORY_NAMES.firstOrNull { it.first == current }?.second ?: "Category") + " ▾", style = MaterialTheme.typography.labelMedium,
                color = tone(Kinds.category(current).hue).accent)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            CATEGORY_NAMES.forEach { (key, label) ->
                DropdownMenuItem(text = { Text(label) }, leadingIcon = {
                    androidx.compose.material3.Icon(Kinds.category(key).icon, null, tint = tone(Kinds.category(key).hue).accent)
                }, onClick = {
                    open = false
                    if (key != current) set(keys, current, key)
                })
            }
        }
    }
}

/** A text button only 32 dp tall (no 48 dp touch frame), for a menu that sits on a line of text. */
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
internal fun SmallTextButton(onClick: () -> Unit, modifier: Modifier = Modifier, content: @Composable androidx.compose.foundation.layout.RowScope.() -> Unit) {
    androidx.compose.runtime.CompositionLocalProvider(androidx.compose.material3.LocalMinimumInteractiveComponentEnforcement provides false) {
        TextButton(onClick = onClick, modifier = modifier.height(32.dp),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 12.dp, vertical = 0.dp), content = content)
    }
}
