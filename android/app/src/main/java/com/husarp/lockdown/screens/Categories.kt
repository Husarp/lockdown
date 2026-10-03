package com.husarp.lockdown.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
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
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Modes
import com.husarp.lockdown.guard.rememberGuard
import com.husarp.lockdown.ui.Chip

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
        CATEGORY_NAMES.forEach { (key, label) -> Chip(label, key == current) { if (key != current) set(categoryKeys(item), current, key) } }
    }
    Text("Work, Study, Focus and Do Not Disturb block what's Distracting. Saved at once.",
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

/** A small "Distracting ▾" menu for an app in Insights (an app on your list, or any other app). */
@Composable
fun CategoryMenu(pkg: String) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val set = rememberCategorySetter()
    var open by remember { mutableStateOf(false) }
    val listed = cfg.items.firstOrNull { it.type == ItemType.APP && it.target.equals(pkg, true) }
    // a listed app is Distracting until chosen otherwise; another app is in no category until chosen
    val current = listed?.let { Modes.itemCategory(it, cfg.categories) } ?: cfg.categories["app:${pkg.lowercase()}"]
    Box {
        TextButton(onClick = { open = true }) {
            Text((CATEGORY_NAMES.firstOrNull { it.first == current }?.second ?: "Category") + " ▾", style = MaterialTheme.typography.labelMedium)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            CATEGORY_NAMES.forEach { (key, label) ->
                DropdownMenuItem(text = { Text(label) }, onClick = {
                    open = false
                    if (key != current) set(listOf("app:${pkg.lowercase()}"), current, key)
                })
            }
        }
    }
}
