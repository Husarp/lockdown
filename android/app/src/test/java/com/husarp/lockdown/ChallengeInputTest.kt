package com.husarp.lockdown

import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import com.husarp.lockdown.guard.challengeInputChange
import com.husarp.lockdown.guard.PuzzleSubmission
import com.husarp.lockdown.guard.returnSortPiece
import com.husarp.lockdown.guard.takeSortPiece
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ChallengeInputTest {
    @Test fun auto_submit_locks_before_callback_and_ignores_stale_events() {
        val submission = PuzzleSubmission()
        var answers = 0
        submission.submit {
            answers++
            assertTrue(submission.sent)
            submission.submit { answers++ } // a reentrant or queued final input event
        }
        submission.submit { answers++ }
        assertEquals(1, answers)
        val nextPuzzle = PuzzleSubmission()
        nextPuzzle.submit { answers++ }
        assertEquals(2, answers)
    }

    @Test fun placed_piece_returns_then_can_be_picked_again_without_reordering_others() {
        var taps = listOf(3, 0, 4)
        taps = returnSortPiece(taps, 0)
        assertEquals(listOf(3, 4), taps)
        assertEquals(listOf(3, 4), returnSortPiece(taps, 0))
        taps = takeSortPiece(taps, 0, 6)
        assertEquals(listOf(3, 4, 0), taps)
        assertEquals(taps, takeSortPiece(taps, 0, 6)) // duplicate callback
        assertEquals(taps, takeSortPiece(taps, 9, 6))
        assertEquals(listOf(0), returnSortPiece(returnSortPiece(taps, 3), 4))
    }
    @Test fun delete_removes_one_character_even_when_keyboard_requests_a_word_or_everything() {
        assertEquals("hell", challengeInputChange("hello", "hell"))
        assertEquals("hello worl", challengeInputChange("hello world", "hello "))
        assertEquals("hello worl", challengeInputChange("hello world", ""))
        assertEquals("ab c ef", challengeInputChange("ab cd ef", "ab  ef"))
        assertEquals("", challengeInputChange("", ""))
        assertEquals("a", challengeInputChange("a😀", "a"))
    }

    @Test fun held_delete_events_repeat_one_character_at_a_time_without_a_timer() {
        var text = "hello"
        val seen = mutableListOf<String>()
        repeat(5) {
            text = challengeInputChange(text, "") // IME repeats a delete-all/word request
            seen += text
        }
        assertEquals(listOf("hell", "hel", "he", "h", ""), seen)
        // No event after release: no pending task can keep deleting later input.
        text = challengeInputChange(text, "x")
        assertEquals("x", text)
    }

    @Test fun paste_is_refused_including_replacement_that_does_not_grow_the_field() {
        assertEquals("", challengeInputChange("", "hello"))
        assertEquals("he", challengeInputChange("he", "hello"))
        assertEquals("wrong", challengeInputChange("wrong", "right"))
        assertEquals("abcdef", challengeInputChange("abcdef", "abXYef"))
        assertEquals("abXef", challengeInputChange("abcdef", "abXef")) // one typed replacement
    }

    @Test fun typing_after_correction_still_completes_phrase_and_captcha_once() {
        for (answer in listOf("hello world", "aB7x")) {
            var text = ""
            var answers = 0
            val submission = PuzzleSubmission()
            fun edit(next: String) {
                if (submission.sent) return
                text = challengeInputChange(text, next)
                if (text == answer) submission.submit { answers++ }
            }
            edit(answer) // a pasted answer must not submit
            assertEquals(0, answers)
            edit("z")
            edit("")
            for (c in answer) edit(text + c)
            edit(answer) // queued duplicate event
            assertEquals(answer, text)
            assertEquals(1, answers)
        }
    }
    @Test fun normalized_delete_preserves_suffix_and_places_cursor_at_deleted_character() {
        val changed = challengeInputChange(
            TextFieldValue("hello world!", TextRange(11)),
            TextFieldValue("hello !", TextRange(6)),
        )
        assertEquals("hello worl!", changed.text)
        assertEquals(TextRange(10), changed.selection)
        val repeated = challengeInputChange(TextFieldValue("aaaa", TextRange(4)), TextFieldValue("aa", TextRange(2)))
        assertEquals("aaa", repeated.text)
        assertEquals(TextRange(3), repeated.selection)
    }

    @Test fun ordinary_cursor_movement_and_typing_keep_keyboard_selection() {
        val old = TextFieldValue("helo", TextRange(3))
        val typed = TextFieldValue("hello", TextRange(4))
        assertEquals(typed, challengeInputChange(old, typed))
        val moved = typed.copy(selection = TextRange(1))
        assertEquals(moved, challengeInputChange(typed, moved))
    }
}
