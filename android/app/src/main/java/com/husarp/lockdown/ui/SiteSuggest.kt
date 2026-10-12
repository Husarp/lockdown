package com.husarp.lockdown.ui

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.ListedSite
import com.husarp.lockdown.engine.SiteIndex
import com.husarp.lockdown.engine.SiteList
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

/** The bundled site list, loaded off the main thread (null until it's there). */
@Composable
fun rememberSiteIndex(): SiteIndex? {
    val ctx = LocalContext.current.applicationContext
    var index by remember { mutableStateOf(SiteList.peek()) }
    LaunchedEffect(Unit) { if (index == null) index = SiteList.get(ctx) }
    return index
}

/** Up to 8 sites that fit [query], worked out off the main thread a moment after the last keystroke (no query: none). */
@Composable
fun rememberSiteSuggestions(query: String): List<ListedSite> {
    val index = rememberSiteIndex()
    var found by remember { mutableStateOf(emptyList<ListedSite>()) }
    LaunchedEffect(query, index) {
        if (index == null || query.isBlank()) { found = emptyList(); return@LaunchedEffect }
        delay(120)
        found = withContext(Dispatchers.Default) { index.suggest(query, 8) }
    }
    return if (query.isBlank()) emptyList() else found
}

/** The suggestions under an "add a site" box: each with its icon, name, main address and small category. Tap one to pick it. */
@Composable
fun SiteSuggestions(sites: List<ListedSite>, onPick: (ListedSite) -> Unit) {
    if (sites.isEmpty()) return
    Card(color = MaterialTheme.colorScheme.surfaceContainerLow, padding = PaddingValues(vertical = 4.dp)) {
        sites.forEach { s ->
            ListRow(title = s.name, subtitle = s.main,
                leading = { ItemIcon(ItemType.SITE, s.main, s.name, 36.dp) },
                trailing = { Text(s.category, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1) },
                onClick = { onPick(s) })
        }
    }
}
