package com.husarp.lockdown.guard

import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue

/** Shared by auto-submit inputs: stale input events must never answer the next puzzle. */
internal class PuzzleSubmission {
    var sent = false
        private set

    fun submit(answer: () -> Unit) {
        if (sent) return
        sent = true
        answer()
    }
}

/** Stable item IDs; taking a tile back preserves every other tile's order. */
internal fun takeSortPiece(taps: List<Int>, id: Int, count: Int): List<Int> =
    if (id !in 0 until count || id in taps) taps else taps + id

internal fun returnSortPiece(taps: List<Int>, id: Int): List<Int> = taps - id

/** One keyboard edit: reject inserted chunks and turn IME word/line deletes into one character.
 * Repeat remains the keyboard's job, so releasing Delete stops immediately (no queued timer).
 */
internal fun challengeInputChange(old: String, next: String): String {
    var start = 0
    while (start < old.length && start < next.length && old[start] == next[start]) start++
    var oldEnd = old.length
    var nextEnd = next.length
    while (oldEnd > start && nextEnd > start && old[oldEnd - 1] == next[nextEnd - 1]) {
        oldEnd--; nextEnd--
    }
    val inserted = nextEnd - start
    if (inserted > 1) return old // includes a paste replacing selected text
    if (inserted == 0 && oldEnd > start) {
        val deleteStart = old.offsetByCodePoints(oldEnd, -1)
        return old.removeRange(deleteStart, oldEnd)
    }
    return next
}

/** Keep the IME cursor for normal edits; a word-delete becomes one backspace at that range's end. */
internal fun challengeInputChange(old: TextFieldValue, next: TextFieldValue): TextFieldValue {
    val text = challengeInputChange(old.text, next.text)
    if (text == next.text) return next
    if (text == old.text) return old
    var start = 0
    while (start < old.text.length && start < next.text.length && old.text[start] == next.text[start]) start++
    var end = old.text.length
    var nextEnd = next.text.length
    while (end > start && nextEnd > start && old.text[end - 1] == next.text[nextEnd - 1]) {
        end--; nextEnd--
    }
    return TextFieldValue(text, TextRange(old.text.offsetByCodePoints(end, -1)))
}
