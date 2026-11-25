package com.husarp.lockdown.screens

import android.content.ClipData
import android.content.ClipboardManager
import android.os.PersistableBundle
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.husarp.lockdown.engine.SupervisorCodes as Codes
import com.husarp.lockdown.guard.SupervisorCodeField
import com.husarp.lockdown.guard.SupervisorStore
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.guard.rememberGuard
import com.husarp.lockdown.guard.rememberTick
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.Chip

@Composable
internal fun SupervisorCodesCard() {
    val ctx = LocalContext.current
    val store = remember(ctx) { SupervisorStore.local(ctx) }
    var authorizingConfirmation by remember { mutableStateOf(false) }
    val guard = rememberGuard(onCancel = { authorizingConfirmation = false })
    val tick = rememberTick(1000)
    val tickTime = remember(tick) { TrustedTime.now(ctx) / 1000 }
    var copiedAt by remember(tick) { mutableStateOf<Long?>(null) }
    val now = copiedAt ?: tickTime
    var setup by remember { mutableStateOf<SupervisorStore.Setup?>(null) }
    var editing by remember { mutableStateOf(false) }
    var selected by remember { mutableStateOf<SupervisorStore.Row?>(null) }
    var deleting by remember { mutableStateOf<SupervisorStore.Row?>(null) }
    var error by remember { mutableStateOf("") }
    var revision by remember { mutableIntStateOf(0) }
    val period = remember(revision) { runCatching(store::activePeriod) }
    val rows = remember(now, revision, setup) { runCatching { store.rows(now) } }
    val clipboard = ctx.getSystemService(ClipboardManager::class.java)

    fun write(copy: String, label: String) {
        val clip = ClipData.newPlainText(label, copy)
        clip.description.extras = PersistableBundle().apply { putBoolean("android.content.extra.IS_SENSITIVE", true) }
        checkNotNull(clipboard).setPrimaryClip(clip)
    }

    fun change(operation: () -> Unit) {
        runCatching(operation).onSuccess {
            error = ""
            revision++
        }.onFailure { error = "Couldn't update supervisor codes. Try again." }
    }

    Card(color = MaterialTheme.colorScheme.surfaceContainerLow) {
        Text("Supervisor codes", style = MaterialTheme.typography.titleMedium)
        val active = period.getOrNull()
        Text(when {
            period.isFailure -> "Supervisor storage is unavailable."
            active == Codes.LEGACY_PERIOD -> "Paired · 30-second codes. Re-pair for 40-second codes."
            active != null -> "Paired · $active-second codes."
            else -> "Pair with a trusted supervisor who can approve individual changes."
        })
        Row {
            TextButton(enabled = period.isSuccess, onClick = {
                guard(true) { change { setup = store.begin() } }
            }) { Text(if (active == null) "Set up" else "Replace") }
            if (active != null) TextButton(onClick = {
                guard(true) { change { store.remove(); setup = null } }
            }) { Text("Remove") }
        }
        if (error.isNotEmpty()) Text(error, color = MaterialTheme.colorScheme.error)
    }

    Card(color = MaterialTheme.colorScheme.surfaceContainerLow) {
        Text("Codes I supervise", style = MaterialTheme.typography.titleMedium)
        TextButton(enabled = rows.isSuccess, onClick = { selected = null; editing = true }) { Text("Add a key") }
        val current = rows.getOrNull()
        when {
            current == null -> Text("Couldn't load codes.")
            current.isEmpty() -> Text("No supervised keys yet.")
            else -> for (row in current) key(row.id) {
                Column(Modifier.padding(vertical = 6.dp)) {
                    Text(row.name, style = MaterialTheme.typography.titleSmall)
                    Text(row.display.code, fontFamily = FontFamily.Monospace)
                    Text("${row.period}s period · Changes in ${row.display.remaining}s · Expires in ${row.display.expiresIn}s",
                        style = MaterialTheme.typography.bodySmall)
                    if (row.period == Codes.LEGACY_PERIOD) Text("Re-pair for 40-second codes.",
                        style = MaterialTheme.typography.bodySmall)
                    Row {
                        TextButton(onClick = {
                            runCatching {
                                val freshAt = TrustedTime.now(ctx) / 1000
                                val code = store.rows(freshAt).first { it.id == row.id }.display.code
                                write(code, "Supervisor code")
                                copiedAt = freshAt
                            }.onSuccess { error = "" }.onFailure { error = "Couldn't copy the code." }
                        }) { Text("Copy") }
                        TextButton(onClick = { selected = row; editing = true }) { Text("Rename") }
                        TextButton(onClick = { deleting = row }) { Text("Remove") }
                    }
                }
            }
        }
    }

    setup?.let { pending ->
        DisposableEffect(pending) { onDispose { store.cancel(pending) } }
        if (!authorizingConfirmation) key(pending) {
            PairingDialog(pending.pairing,
                onCopy = { write(Codes.uri(pending.pairing, "Phone supervisor"), "Supervisor pairing") },
                onDismiss = { store.cancel(pending); setup = null }, onConfirm = { code ->
                    val verified = store.verifySetup(pending, code, TrustedTime.now(ctx) / 1000)
                    authorizingConfirmation = true
                    guard(true) {
                        change {
                            store.confirm(verified)
                            setup = null
                        }
                        authorizingConfirmation = false
                    }
                })
        }
    }

    if (editing) {
        val row = selected
        KeyEditor(row?.name, onDismiss = { editing = false }, onSave = { name, value, rawPeriod ->
            if (row == null) store.add(name, value, rawPeriod) else store.rename(row.id, name)
            revision++
            editing = false
        })
    }

    deleting?.let { row ->
        AlertDialog(onDismissRequest = { deleting = null }, title = { Text("Remove supervised key?") },
            text = { Text("You'll need ${row.name}'s key to generate more codes.") },
            dismissButton = { TextButton(onClick = { deleting = null }) { Text("Cancel") } },
            confirmButton = { TextButton(onClick = { change { store.delete(row.id); deleting = null } }) { Text("Remove") } })
    }
}

@Composable
private fun PairingDialog(pairing: Codes.Pairing, onCopy: () -> Unit, onDismiss: () -> Unit, onConfirm: (String) -> Unit) {
    var code by remember { mutableStateOf("") }
    var error by remember { mutableStateOf("") }
    AlertDialog(onDismissRequest = onDismiss, title = { Text("Set up supervisor codes") }, text = {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Share the pairing link with your supervisor. Lockdown on PC and phone supports 40-second codes. Authenticators fixed at 30 seconds work with existing 30-second pairings.")
            Text("40-second base32 key", style = MaterialTheme.typography.labelMedium)
            Text(pairing.secret.chunked(4).joinToString(" "), fontFamily = FontFamily.Monospace)
            TextButton(onClick = {
                runCatching(onCopy).onFailure { error = "Couldn't copy the pairing link." }
            }) { Text("Copy pairing link") }
            Text("Enter the code your supervisor generates to confirm the pairing.")
            SupervisorCodeField(code) { code = it; error = "" }
            if (error.isNotEmpty()) Text(error, color = MaterialTheme.colorScheme.error)
        }
    }, dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
        confirmButton = { TextButton(enabled = Codes.validCode(code), onClick = {
            runCatching { onConfirm(code) }.onFailure {
                error = if (it is IllegalArgumentException) it.message.orEmpty() else "Couldn't confirm the pairing."
            }
        }) { Text("Confirm") } })
}

@Composable
private fun KeyEditor(existing: String?, onDismiss: () -> Unit, onSave: (String, String, Int) -> Unit) {
    var name by remember { mutableStateOf(existing.orEmpty()) }
    var value by remember { mutableStateOf("") }
    var period by remember { mutableIntStateOf(Codes.NEW_PERIOD) }
    var error by remember { mutableStateOf("") }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(if (existing == null) "Add a supervisor key" else "Rename supervised key") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(name, { name = it }, label = { Text("Name") }, singleLine = true)
                if (existing == null) {
                    OutlinedTextField(value, { value = it }, label = { Text("Pairing link or base32 key") }, singleLine = true)
                    Text("Period for a base32 key")
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Chip("40 seconds", period == Codes.NEW_PERIOD) { period = Codes.NEW_PERIOD }
                        Chip("30 seconds (legacy)", period == Codes.LEGACY_PERIOD) { period = Codes.LEGACY_PERIOD }
                    }
                }
                if (error.isNotEmpty()) Text(error, color = MaterialTheme.colorScheme.error)
            }
        }, dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
        confirmButton = { TextButton(onClick = {
            runCatching { onSave(name, value, period) }.onFailure {
                error = if (it is IllegalArgumentException) it.message.orEmpty() else "Couldn't save the key."
            }
        }) { Text("Save") } })
}
