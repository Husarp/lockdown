package com.husarp.lockdown.engine

/** Listed items in the foreground app and its current browser host, before checking whether use is blocked. */
internal fun foregroundItems(items: List<Item>, pkg: String, host: String?): List<Item> {
    val index = ItemIndex.of(items)
    val app = index.app(pkg)?.takeUnless { it.disabled }
    val sites = host?.let { h -> index.sites(h).filter { it.type == ItemType.SITE && !it.disabled } }.orEmpty()
    return listOfNotNull(app) + sites
}
