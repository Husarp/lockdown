package com.husarp.lockdown.screens

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.widget.Toast
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.husarp.lockdown.remind.Grayscale
import kotlinx.coroutines.delay

/** Under a grayscale switch that is on: says so when Lockdown can't change the screen's colour (the one-time adb
 *  grant is missing - an uninstall + reinstall removes it - with the command and a Copy button; or this is the
 *  copy in the work profile, which never can). */
@Composable
fun GrayscaleNote(on: Boolean) {
    val ctx = LocalContext.current
    val fresh = com.husarp.lockdown.guard.rememberFresh()     // the adb grant shows without leaving the app
    val canWrite = remember(fresh) { Grayscale.canWrite(ctx) }
    if (!on) return
    val cs = MaterialTheme.colorScheme
    if (Grayscale.otherProfile(ctx)) {
        Text("This is Lockdown's copy in your work profile: it can't change the screen's colour. Bedtime grayscale " +
            "works from Lockdown in your main profile.", Modifier.padding(top = 6.dp),
            style = MaterialTheme.typography.bodySmall, color = cs.error)
        return
    }
    if (canWrite) return
    Column(Modifier.padding(top = 6.dp)) {
        Text("Grayscale can't work yet: it needs a one-time permission. With the phone connected to a computer, run:",
            style = MaterialTheme.typography.bodySmall, color = cs.error)
        Text(Grayscale.GRANT, Modifier.padding(vertical = 4.dp), style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace)
        TextButton(onClick = { copyGrant(ctx) }) { Text("Copy command") }
    }
}

fun copyGrant(ctx: Context) {
    ctx.getSystemService(ClipboardManager::class.java)?.setPrimaryClip(ClipData.newPlainText("adb", Grayscale.GRANT))
    Toast.makeText(ctx, "Copied. Run it on a computer with the phone connected (USB debugging on).", Toast.LENGTH_LONG).show()
}

/** Home: the screen is grey outside bedtime (left on, or Lockdown couldn't undo it) - one tap gives the colour back.
 *  Never shown at bedtime, so it's no way around bedtime grayscale. */
@Composable
fun GrayscaleStuck() {
    val ctx = LocalContext.current
    var tick by remember { mutableStateOf(0) }
    LaunchedEffect(Unit) { while (true) { delay(5000); tick++ } }      // goes once the colour is back
    val fresh = com.husarp.lockdown.guard.rememberFresh()               // and at once on every return to the app
    val stuck = remember(tick, fresh) { Grayscale.stuck(ctx) }
    if (!stuck) return
    val cs = MaterialTheme.colorScheme
    com.husarp.lockdown.ui.Card(color = cs.errorContainer, contentColor = cs.onErrorContainer) {
        Text("The screen is grey, but it isn't bedtime", style = MaterialTheme.typography.titleSmall)
        Text("Lockdown's grayscale should be off now.", style = MaterialTheme.typography.bodySmall)
        Button(onClick = { Grayscale.turnOffNow(ctx); tick++ }, Modifier.padding(top = 8.dp)) { Text("Turn grayscale off now") }
    }
}
