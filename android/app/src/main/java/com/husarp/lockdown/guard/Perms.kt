package com.husarp.lockdown.guard

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.core.app.NotificationManagerCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import com.husarp.lockdown.block.BlockService
import com.husarp.lockdown.engine.Perm
import com.husarp.lockdown.engine.PermCheck
import kotlinx.coroutines.delay

/** Permission reads for the status rows (Guardrails, Settings, Lockdown in Island). Logic in engine/PermCheck.kt. */
object Perms {
    /** App blocking: really running, or only turned on in Android's settings. */
    fun a11y(ctx: Context): Perm = PermCheck.a11y(BlockService.isEnabled(ctx), BlockService.connected)

    fun notifications(ctx: Context): Boolean = PermCheck.notificationsOn(
        Build.VERSION.SDK_INT,
        Build.VERSION.SDK_INT < 33 || ctx.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED,
        NotificationManagerCompat.from(ctx).areNotificationsEnabled(),
    )

    /** Do Not Disturb access (lets a mode that mutes turn Do Not Disturb on). */
    fun dnd(ctx: Context): Boolean = ctx.getSystemService(android.app.NotificationManager::class.java).isNotificationPolicyAccessGranted

    fun dndIntent(): Intent = Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS)

    fun notificationsIntent(ctx: Context): Intent =
        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, ctx.packageName)

    /** Battery "Fix": Android's one-tap "let Lockdown always run in the background?", else its battery optimisation
     *  list (there Lockdown hides under "All apps", so allowing it was easy to miss). */
    fun openBattery(ctx: Context) {
        val ask = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${ctx.packageName}"))
        runCatching { ctx.startActivity(ask.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }.onFailure {
            runCatching { ctx.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
        }
    }
}

/**
 * A key for `remember(key) { check }`: it changes each time the screen comes back (ON_RESUME - also the return from
 * a Fix screen in Android's settings), again 1, 3 and 7 s later (Android can report a fresh grant late, the overlay
 * especially), then every 5 s while the screen is open (an adb grant comes with no return). A permission row is never
 * shown from a value read before.
 */
@Composable
fun rememberFresh(): Int {
    val owner = LocalLifecycleOwner.current
    var key by remember { mutableIntStateOf(0) }
    LaunchedEffect(owner) {
        owner.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            var wait = 1000L
            while (true) { key++; delay(wait); wait = minOf(wait * 2, 5000L) }
        }
    }
    return key
}
