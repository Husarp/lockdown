package com.husarp.lockdown.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.husarp.lockdown.ui.theme.LockdownTheme

/**
 * A page's unsaved edits, like the PC's draft: the page shows [value], but nothing reaches the config (or the
 * blocking) until its Save. With no edit it follows [saved]. A text field keys what's typed on [resets], so
 * Discard (and Save) put the saved value back in it.
 */
@Stable
class Draft<T>(saved: T) {
    var saved by mutableStateOf(saved)
        internal set
    private var edit by mutableStateOf<T?>(null)
    var resets by mutableIntStateOf(0)
        private set
    val value: T get() = edit ?: saved
    val dirty: Boolean get() = edit.let { it != null && it != saved }
    /** An edit; one that comes back to the saved settings is no edit. */
    fun update(f: (T) -> T) { edit = f(value).takeIf { it != saved } }
    /** Back to the saved settings: Discard, or once saved. */
    fun clear() { edit = null; resets++ }
}

/** A [Draft] of [saved] (what the page edits, taken from the config on every change of it). */
@Composable
fun <T> rememberDraft(saved: T): Draft<T> = remember { Draft(saved) }.also { it.saved = saved }

/** The page open with unsaved edits, so leaving it (Back, the tabs, the top bar) asks Save / Discard first. */
object Unsaved {
    internal class Page(val save: (then: () -> Unit) -> Unit, val discard: () -> Unit)
    internal var page by mutableStateOf<Page?>(null)
    internal var leaving by mutableStateOf<(() -> Unit)?>(null)

    /** Leave with [go] now, or once the open page's unsaved edits are saved or discarded. */
    fun leave(go: () -> Unit) { if (page == null) go() else leaving = go }
}

/**
 * Marks the page's edits unsaved while [dirty]: Back ([onBack]) and leaving the page ask Save / Discard first.
 * [save] saves the whole draft - the challenge once if anything in it loosens - and then runs its argument;
 * nothing is saved (and the page stays) when the challenge is cancelled. [discard] drops the edits.
 */
@Composable
fun UnsavedChanges(dirty: Boolean, onBack: () -> Unit, save: (then: () -> Unit) -> Unit, discard: () -> Unit = {}) {
    val s by rememberUpdatedState(save)
    val d by rememberUpdatedState(discard)
    val page = remember { Unsaved.Page({ s(it) }, { d() }) }
    DisposableEffect(dirty) {
        if (dirty) Unsaved.page = page
        onDispose { if (Unsaved.page === page) { Unsaved.page = null; Unsaved.leaving = null } }   // (gone: its edits too)
    }
    BackHandler(enabled = dirty) { Unsaved.leave(onBack) }
}

/** "Save your changes?" on leaving a page with unsaved edits: Save (then leave), Discard (and leave) or Cancel. */
@Composable
fun UnsavedDialog() {
    val go = Unsaved.leaving ?: return
    AlertDialog(
        onDismissRequest = { Unsaved.leaving = null },
        title = { Text("Save your changes?") },
        text = { Text("What you changed here isn't in effect until you save it.") },
        confirmButton = {
            TextButton(onClick = {
                Unsaved.leaving = null
                val page = Unsaved.page
                if (page == null) go() else page.save { Unsaved.page = null; go() }
            }) { Text("Save") }
        },
        dismissButton = {
            Row {
                TextButton(onClick = { Unsaved.leaving = null }) { Text("Cancel") }
                TextButton(onClick = {
                    Unsaved.leaving = null
                    Unsaved.page?.discard()
                    Unsaved.page = null
                    go()
                }) { Text("Discard") }
            }
        },
    )
}

/** The bar under a page with unsaved edits (shown only then): Discard, or Save - with the challenge once if anything
 *  in the whole draft loosens. */
@Composable
fun SaveBar(dirty: Boolean, onDiscard: () -> Unit, onSave: () -> Unit) {
    if (!dirty) return
    val ex = LockdownTheme.extra
    Surface(color = ex.warningContainer, contentColor = ex.onWarningContainer, modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(start = 16.dp, end = 12.dp, top = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("Unsaved changes", style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
            TextButton(onClick = onDiscard) { Text("Discard") }
            Button(onClick = onSave) { Text("Save") }
        }
    }
}
