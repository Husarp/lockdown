package com.husarp.lockdown.screens

import android.app.TimePickerDialog
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import com.husarp.lockdown.data.BlockNow
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.guard.TrustedTime
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.format.DateTimeFormatter

/** Saves only the immediate rule; the callback rebases an open editor without saving its draft. */
@Composable
fun BlockNowButton(id: String, group: Boolean = false, onSaved: (LocalDateTime) -> Unit = {}) {
    val ctx = LocalContext.current
    var open by remember { mutableStateOf(false) }
    var custom by remember { mutableStateOf(false) }
    var selected by remember { mutableStateOf<LocalDateTime?>(null) }
    var error by remember { mutableStateOf("") }
    TextButton(onClick = { open = true; error = "" }) { Text("Block now") }
    fun pick(tomorrow: Boolean) {
        val now = TrustedTime.local(ctx)
        TimePickerDialog(ctx, { _, h, m ->
            val at = BlockNow.until(TrustedTime.local(ctx), LocalTime.of(h, m), tomorrow)
            if (at.isAfter(TrustedTime.local(ctx))) { selected = at; custom = false; error = "" }
            else error = "Choose a future time."
        }, now.hour, now.minute, true).show()
    }
    if (open) AlertDialog(
        onDismissRequest = { open = false; selected = null; custom = false },
        title = { Text("Block now") },
        text = {
            Column {
                val now = TrustedTime.local(ctx)
                Text(BlockNow.notice(Store.config, id, group, now))
                val at = selected
                if (at != null) {
                    val rules = if (group) Store.config.groups.firstOrNull { it.id == id }?.rules.orEmpty()
                        else Store.config.items.firstOrNull { it.id == id }?.rules.orEmpty()
                    Text("Save until ${maxOf(BlockNow.end(rules) ?: at, at).format(DateTimeFormatter.ofPattern("EEE d MMM, HH:mm"))}?")
                } else if (custom) {
                    TextButton(onClick = { pick(false) }) { Text("Today…") }
                    TextButton(onClick = { pick(true) }) { Text("Tomorrow…") }
                } else {
                    TextButton(onClick = { selected = BlockNow.hour(TrustedTime.local(ctx)) }) { Text("1 hour") }
                    TextButton(onClick = { selected = BlockNow.midnight(TrustedTime.local(ctx)) }) { Text("Rest of today") }
                    TextButton(onClick = { custom = true }) { Text("Until…") }
                }
                if (error.isNotEmpty()) Text(error)
            }
        },
        confirmButton = {
            selected?.let { at ->
                TextButton(onClick = {
                    val now = TrustedTime.local(ctx)
                    if (!at.isAfter(now)) { error = "Choose a future time."; selected = null }
                    else {
                        Store.update { BlockNow.apply(it, id, group, at, now) }
                        val rules = if (group) Store.config.groups.firstOrNull { it.id == id }?.rules.orEmpty()
                            else Store.config.items.firstOrNull { it.id == id }?.rules.orEmpty()
                        BlockNow.end(rules)?.let(onSaved)
                        open = false; selected = null; custom = false
                    }
                }) { Text("Save") }
            }
        },
        dismissButton = { TextButton(onClick = { open = false; selected = null; custom = false }) { Text("Cancel") } },
    )
}
