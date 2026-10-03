package com.husarp.lockdown.block

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager

data class InstalledApp(val pkg: String, val label: String)

object Apps {
    /** Launchable apps (things with a home-screen icon), minus ourselves, sorted by name. In main, with Island
     *  linked, apps installed only in Island are added too ("Name (Island)"), so they can get rules. */
    fun launchable(ctx: Context): List<InstalledApp> {
        val pm = ctx.packageManager
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val mine = pm.queryIntentActivities(intent, 0)
            .mapNotNull { it.activityInfo?.packageName }
            .distinct()
            .filter { it != ctx.packageName }
            .map { InstalledApp(it, label(pm, it)) }
        return (mine + com.husarp.lockdown.link.IslandLink.islandOnly(ctx, mine.mapTo(HashSet()) { it.pkg }))
            .sortedBy { it.label.lowercase() }
    }

    fun label(pm: PackageManager, pkg: String): String =
        runCatching { pm.getApplicationInfo(pkg, 0).loadLabel(pm).toString() }.getOrDefault(pkg)
}
