package com.husarp.lockdown.guard

import android.os.SystemClock
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.SupervisorCodes
import kotlinx.coroutines.delay

/**
 * Returns `guard(loosening, onPass)`: for a tightening change it runs at once; for a loosening one it runs the
 * Anti-Bypass challenge first (the step list: math, memory grid, sorting, typed phrase; wait out a cool-off, or only inside allowed hours). Tightening is
 * never gated - coming back to your rules is never the thing to stand in the way of. [onCancel] runs when the
 * challenge is closed without passing.
 */
@Composable
fun rememberGuard(allowSupervisor: Boolean = true, onCancel: () -> Unit = {}): (loosening: Boolean, onPass: () -> Unit) -> Unit {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    val ab = cfg.antibypass
    var pending by remember { mutableStateOf<PendingGuardAction?>(null) }

    pending?.let { request ->
        key(request) {
            ChallengeDialog(onPass = {
                request.complete {
                    pending = null
                    // Normal challenge success opens the editing window in trusted time.
                    val now = TrustedTime.local(ctx)
                    Store.update { it.copy(antibypass = it.antibypass.copy(unlockedFrom = now.toString(),
                        unlockedUntil = now.plusMinutes(AntiBypass.UNLOCK_MIN).toString())) }
                    com.husarp.lockdown.data.BypassLog.record(ctx, com.husarp.lockdown.engine.Bypass.CHALLENGE)
                }
            }, onSupervisorPass = {
                request.complete { pending = null }
            }, onCancel = {
                if (request.cancel()) {
                    pending = null
                    onCancel()
                }
            }, isPending = { pending === request }, allowSupervisor = allowSupervisor)
        }
    }
    return { loosening, onPass ->
        // "free" is true when no challenge is set OR the unlock window is open — either way, run it now.
        if (!loosening || AntiBypass.status(ab, TrustedTime.local(ctx)) == "free") onPass()
        else {
            pending?.cancel()
            pending = PendingGuardAction(onPass)
        }
    }
}

internal class PendingGuardAction(private var action: (() -> Unit)?) {
    fun complete(before: () -> Unit) {
        val apply = action ?: return
        action = null
        before()
        apply()
    }

    fun cancel(): Boolean {
        val open = action != null
        action = null
        return open
    }
}

@Composable
private fun ChallengeDialog(
    onPass: () -> Unit, onSupervisorPass: () -> Unit, onCancel: () -> Unit, isPending: () -> Boolean,
    allowSupervisor: Boolean,
) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val cs = MaterialTheme.colorScheme
    val ab = cfg.antibypass
    val ctx = LocalContext.current
    val now = TrustedTime.local(ctx)
    val supervisor = remember(ctx) { SupervisorStore.local(ctx) }
    val hasSupervisor = remember(supervisor, allowSupervisor) {
        allowSupervisor && runCatching { supervisor.activePeriod() != null }.getOrDefault(false)
    }

    // The whole challenge list, run by the engine (the same one the bedtime cover uses); a blank own phrase falls back to a
    // random one there, so a phrase step can never pass by typing nothing. Dropping the run (Cancel) starts over next time.
    val run = remember { AntiBypass.newRun(ab, SystemClock.elapsedRealtime()) }
    var cleared by remember { mutableStateOf(!ab.phrase) }       // the list is passed (nothing to ask when only hours / a wait are set)
    var waitLeft by remember { mutableIntStateOf(ab.waitMin * 60) }
    var done by remember { mutableStateOf(false) }
    var useSupervisor by remember { mutableStateOf(false) }
    var code by remember { mutableStateOf("") }
    var error by remember { mutableStateOf("") }

    val waitOk = ab.waitMin == 0 || waitLeft <= 0
    val hoursOk = !ab.hours || AntiBypass.inHours(ab, now)

    // The cool-off starts once the list is passed (like the PC), not while it's being done; it holds if the dialog is
    // recomposed. When it runs out, the change goes through by itself.
    if (ab.waitMin > 0) LaunchedEffect(cleared && hoursOk) { if (cleared && hoursOk) while (waitLeft > 0) { delay(1000); waitLeft-- } }
    LaunchedEffect(cleared, hoursOk, waitOk, useSupervisor) {
        if (cleared && hoursOk && waitOk && !done && !useSupervisor) {
            done = true
            onPass()
        }
    }

    Dialog(onDismissRequest = onCancel) {
        Surface(shape = MaterialTheme.shapes.extraLarge, color = cs.surfaceContainerHigh, tonalElevation = 6.dp) {
            Column(Modifier.padding(24.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(if (useSupervisor) "Supervisor code" else "A quick pause first",
                    style = MaterialTheme.typography.headlineSmall)
                if (useSupervisor) {
                    Text("Ask your supervisor for a code to approve this change.")
                    SupervisorCodeField(code) { code = it; error = "" }
                    if (error.isNotEmpty()) Text(error, color = cs.error)
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                        TextButton(onClick = onCancel) { Text("Cancel") }
                        TextButton(onClick = { useSupervisor = false; code = ""; error = "" }) { Text("Back") }
                        TextButton(enabled = SupervisorCodes.validCode(code) && !done, onClick = {
                            if (!done && isPending()) {
                                val approval = runCatching { supervisor.consume(code, TrustedTime.now(ctx) / 1000) }
                                when (approval.getOrNull()) {
                                    SupervisorStore.Approval.ACCEPTED -> {
                                        done = true
                                        code = ""
                                        onSupervisorPass()
                                    }
                                    SupervisorStore.Approval.WAIT -> error = "Too many wrong tries. Wait, then try again."
                                    SupervisorStore.Approval.REFUSED -> error = "That code is incorrect or already used."
                                    null -> error = "Couldn't save the approval. Try again."
                                }
                            }
                        }) { Text("Use code") }
                    }
                } else if (!hoursOk) {
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
                if (hasSupervisor && !useSupervisor && !done) {
                    TextButton(onClick = { useSupervisor = true }) { Text("Supervisor code") }
                }
            }
        }
    }
}

@Composable
internal fun SupervisorCodeField(code: String, onCode: (String) -> Unit) {
    OutlinedTextField(value = code, onValueChange = onCode, label = { Text("Supervisor code") },
        singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
}
