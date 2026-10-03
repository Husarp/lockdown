package com.husarp.lockdown.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.guard.rememberGuard
import com.husarp.lockdown.link.IslandLink
import com.husarp.lockdown.link.LinkCrypto
import com.husarp.lockdown.link.PairWindow
import com.husarp.lockdown.link.Role
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** Settings: link Lockdown in Island to this one (main), or this copy in Island to the main one (standalone). */
@Composable
fun IslandLinkCard() {
    val ctx = LocalContext.current
    when (IslandLink.role(ctx)) {
        Role.MAIN -> MainLinkCard()
        Role.STANDALONE -> Card {
            Column(Modifier.padding(16.dp)) {
                Text("Link to your main Lockdown", style = MaterialTheme.typography.titleSmall)
                Text("This is Lockdown's copy in Island. Linked, it follows the rules of Lockdown in your main profile: " +
                    "Island apps share the same limits, and rules are changed only there. Linking replaces this copy's own rules, " +
                    "so it asks for the challenge.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                LinkCodeField(challenge = true)
            }
        }
        Role.HELPER -> {}
    }
}

@Composable
private fun MainLinkCard() {
    val ctx = LocalContext.current
    val st by IslandLink.mainState.collectAsStateWithLifecycle()
    val guard = rememberGuard()
    val cs = MaterialTheme.colorScheme
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) { while (true) { now = System.currentTimeMillis(); delay(1000) } }
    val mine = remember { IslandLink.versionCode(ctx) }

    Card {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Island (work profile)", style = MaterialTheme.typography.titleSmall)
            when (st.state) {
                "" -> Text("Use Lockdown in Island too: install it there, then link it here. Island apps then follow these " +
                    "rules and share their limits.", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                "linked" -> {
                    val ago = (now - st.lastSeen) / 1000
                    val recent = ago in 0 until 10 * 60          // negative: the clock was set back
                    val text = when {
                        recent && !st.canBlock -> "Linked · Lockdown in Island can't block: open it there and grant what it asks for"
                        recent -> "Linked · last heard from Island ${agoText(ago)}"
                        IslandLink.healthy(ctx) -> "Linked · Island apps are paused"
                        else -> "Linked · not heard from since ${hhmm(st.lastSeen)}: open Lockdown in Island"
                    }
                    Text(text, style = MaterialTheme.typography.bodyMedium, color = if (recent && st.canBlock) cs.onSurface else cs.error)
                    if (st.cfgError) Text("Lockdown in Island couldn't read your rules: update it there.", style = MaterialTheme.typography.bodySmall, color = cs.error)
                    else if (st.helperApp in 1 until mine) Text("Lockdown in Island is older than this one: update it there, so it knows every rule.", style = MaterialTheme.typography.bodySmall, color = cs.error)
                    else if (st.helperApp > mine) Text("Lockdown in Island is newer: update this one.", style = MaterialTheme.typography.bodySmall, color = cs.error)
                    OutlinedButton(onClick = { guard(true) { IslandLink.unlink() } }) { Text("Unlink") }
                }
                "unlinking" -> {
                    Text("Unlinking: finishes when Lockdown in Island next checks in (open it there).", style = MaterialTheme.typography.bodyMedium)
                    Text("If Lockdown in Island is gone, forget it here. A copy still running there keeps blocking with its last rules.",
                        style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                    TextButton(onClick = { guard(true) { IslandLink.forget() } }) { Text("Forget Island now") }
                }
            }
            if (st.noPort) Text("Couldn't open the link on this phone. Restart the phone and try again.", style = MaterialTheme.typography.bodySmall, color = cs.error)

            val code = st.code
            val pairingOpen = code != null && now < st.codeUntil && st.codeFails < PairWindow.MAX_FAILS
            if (pairingOpen) {
                val left = ((st.codeUntil - now) / 1000).coerceAtLeast(0)
                Text(LinkCrypto.display(code!!), fontFamily = FontFamily.Monospace, fontSize = 34.sp, style = MaterialTheme.typography.headlineMedium)
                Text("Open Lockdown in Island > Settings > Link to your main Lockdown, and type this code. %d:%02d left.".format(left / 60, left % 60),
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                TextButton(onClick = { IslandLink.cancelPairing() }) { Text("Cancel") }
            } else if (st.state != "unlinking") {
                if (code != null) Text(
                    if (st.codeFails >= PairWindow.MAX_FAILS) "Too many wrong codes. Start again." else "The code expired. Start again.",
                    style = MaterialTheme.typography.bodySmall, color = cs.error)
                if (st.state == "") Button(onClick = { IslandLink.startPairing() }) { Text("Link Island") }
                // A new copy replaces the working link (a loosening): behind the challenge.
                else TextButton(onClick = { guard(true) { IslandLink.startPairing() } }) { Text("Link a new copy in Island") }
            }
        }
    }
}

/** Type the code main shows. [challenge]: this copy's own rules get replaced, so it asks first (the helper too:
 *  any app could pose as main on the link port; its challenge is main's, copied in with the rules). */
@Composable
fun LinkCodeField(challenge: Boolean) {
    val ctx = LocalContext.current
    val guard = rememberGuard()
    val scope = rememberCoroutineScope()
    var code by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf<String?>(null) }
    val ready = LinkCrypto.normalize(code).length == LinkCrypto.CODE_LEN

    fun go() {
        busy = true; msg = "Linking…"
        scope.launch {
            msg = withContext(Dispatchers.IO) { IslandLink.link(ctx.applicationContext, code) }
            busy = false
        }
    }
    Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(value = code, onValueChange = { code = it.take(12); msg = null }, singleLine = true,
            label = { Text("Link code") }, placeholder = { Text("K7QM-2XPA") }, modifier = Modifier.weight(1f))
        Button(onClick = { if (challenge) guard(true) { go() } else go() }, enabled = ready && !busy,
            modifier = Modifier.padding(top = 8.dp)) { Text("Link") }
    }
    msg?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = if (busy) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.error) }
}

internal fun agoText(sec: Long) = when {
    sec < 60 -> "${sec.coerceAtLeast(0)} s ago"
    sec < 3600 -> "${sec / 60} min ago"
    else -> "${sec / 3600} h ago"
}

private val HHMM = DateTimeFormatter.ofPattern("HH:mm")
internal fun hhmm(ms: Long): String = Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()).format(HHMM)
