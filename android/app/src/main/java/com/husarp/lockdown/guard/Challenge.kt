package com.husarp.lockdown.guard

import android.os.SystemClock
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.AntiBypass
import kotlinx.coroutines.delay

/**
 * Returns `guard(loosening, onPass)`: for a tightening change it runs at once; for a loosening one it runs the
 * Anti-Bypass challenge first (the step list: math, memory grid, sorting, typed phrase; wait out a cool-off, or only inside allowed hours). Tightening is
 * never gated - coming back to your rules is never the thing to stand in the way of. [onCancel] runs when the
 * challenge is closed without passing.
 */
@Composable
fun rememberGuard(onCancel: () -> Unit = {}): (loosening: Boolean, onPass: () -> Unit) -> Unit {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    val ab = cfg.antibypass
    var pending by remember { mutableStateOf<(() -> Unit)?>(null) }

    pending?.let { onPass ->
        ChallengeDialog(onPass = {
            // Passing opens a short unlock window (like the PC), so a run of loosening edits doesn't re-challenge each one.
            // In trusted time: setting the clock back can't stretch it.
            val now = TrustedTime.local(ctx)
            Store.update { it.copy(antibypass = it.antibypass.copy(unlockedFrom = now.toString(), unlockedUntil = now.plusMinutes(AntiBypass.UNLOCK_MIN).toString())) }
            com.husarp.lockdown.data.BypassLog.record(ctx, com.husarp.lockdown.engine.Bypass.CHALLENGE)
            pending = null; onPass()
        }, onCancel = { pending = null; onCancel() })
    }
    return { loosening, onPass ->
        // "free" is true when no challenge is set OR the unlock window is open — either way, run it now.
        if (!loosening || AntiBypass.status(ab, TrustedTime.local(ctx)) == "free") onPass() else pending = onPass
    }
}

@Composable
private fun ChallengeDialog(onPass: () -> Unit, onCancel: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val cs = MaterialTheme.colorScheme
    val ab = cfg.antibypass
    val now = TrustedTime.local(LocalContext.current)

    // The whole challenge list, run by the engine (the same one the bedtime cover uses); a blank own phrase falls back to a
    // random one there, so a phrase step can never pass by typing nothing. Dropping the run (Cancel) starts over next time.
    val run = remember { AntiBypass.newRun(ab, SystemClock.elapsedRealtime()) }
    var cleared by remember { mutableStateOf(!ab.phrase) }       // the list is passed (nothing to ask when only hours / a wait are set)
    var waitLeft by remember { mutableIntStateOf(ab.waitMin * 60) }
    var done by remember { mutableStateOf(false) }

    val waitOk = ab.waitMin == 0 || waitLeft <= 0
    val hoursOk = !ab.hours || AntiBypass.inHours(ab, now)

    // The cool-off starts once the list is passed (like the PC), not while it's being done; it holds if the dialog is
    // recomposed. When it runs out, the change goes through by itself.
    if (ab.waitMin > 0) LaunchedEffect(cleared && hoursOk) { if (cleared && hoursOk) while (waitLeft > 0) { delay(1000); waitLeft-- } }
    LaunchedEffect(cleared, hoursOk, waitOk) { if (cleared && hoursOk && waitOk && !done) { done = true; onPass() } }

    Dialog(onDismissRequest = onCancel) {
        Surface(shape = MaterialTheme.shapes.extraLarge, color = cs.surfaceContainerHigh, tonalElevation = 6.dp) {
            Column(Modifier.padding(24.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("A quick pause first", style = MaterialTheme.typography.headlineSmall)
                if (!hoursOk) {
                    Text("Loosening is only allowed inside your chosen hours. Try again then.", style = MaterialTheme.typography.bodyMedium)
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) { TextButton(onClick = onCancel) { Text("Cancel") } }
                } else if (!cleared) {
                    Text("Tightening saves instantly. Loosening asks for this first — take your time.", style = MaterialTheme.typography.bodyMedium)
                    ChallengeRunPanel(run, onPassed = { cleared = true }, onCancel = onCancel)
                } else {
                    // passed; only the cool-off is left (with no cool-off the change has gone through already)
                    Text("Cool-off: ${waitLeft}s — the change goes through when it ends.", style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) { TextButton(onClick = onCancel) { Text("Cancel") } }
                }
            }
        }
    }
}
