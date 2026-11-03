package com.husarp.lockdown

import com.husarp.lockdown.engine.BlockDebounce
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BlockDebounceTest {
    @Test fun missing_root_and_launcher_never_suppress_forever() {
        val d = BlockDebounce()
        d.dismissed("game", 10_000)
        assertTrue(d.leftover("game", 19_999))
        assertFalse(d.leftover("game", 20_000))
        d.dismissed("game", 21_000)
        assertTrue(d.leftover("game", 21_001))
    }

    @Test fun window_state_fallback_can_confirm_a_return_without_a_root() {
        val d = BlockDebounce()
        d.dismissed("game", 10_000)
        d.otherFront("launcher", 10_200)
        assertFalse(d.leftover("game", 10_400, confirmedOpening = true))
    }

    @Test fun fresh_window_event_is_a_fallback_only_when_the_root_is_missing() {
        assertTrue(BlockDebounce.confirmsWindowState(null, "game", 100))
        assertFalse(BlockDebounce.confirmsWindowState(null, "game", 1001))
        assertFalse(BlockDebounce.confirmsWindowState(null, "game", -1))
        assertFalse(BlockDebounce.confirmsWindowState("launcher", "game", 100))
        assertTrue(BlockDebounce.confirmsWindowState("game", "game", 5000))
    }

    @Test fun nothing_is_ignored_before_a_dismissal() {
        assertFalse(BlockDebounce().leftover("game", 1000))
    }

    @Test fun closing_events_and_stale_ticks_are_suppressed_only_for_a_bounded_time() {
        val d = BlockDebounce()
        d.dismissed("game", 10_000)
        assertTrue(d.leftover("game", 10_100))
        d.otherFront("launcher", 10_200)
        assertTrue(d.leftover("game", 10_400))
        assertTrue(d.leftover("game", 14_000))
        assertFalse(d.leftover("game", 100_000))
        assertFalse(d.leftover("launcher", 100_000))
    }

    @Test fun confirmed_quick_reopen_after_leaving_is_judged() {
        val d = BlockDebounce()
        d.dismissed("game", 10_000)
        d.otherFront("launcher", 10_200)
        assertFalse(d.leftover("game", 10_300, confirmedOpening = true))
        assertFalse(d.leftover("game", 10_400))
    }

    @Test fun missing_confirmation_eventually_releases_even_after_launcher() {
        val d = BlockDebounce()
        d.dismissed("game", 10_000)
        d.otherFront("launcher", 10_200)
        assertFalse(d.leftover("game", 50_000))
        assertFalse(d.leftover("game", 50_100, confirmedOpening = true))
    }

    @Test fun missing_launcher_event_requires_confirmed_presence_after_settling() {
        val d = BlockDebounce()
        d.dismissed("game", 10_000)
        assertTrue(d.leftover("game", 12_999, confirmedOpening = true))
        assertTrue(d.leftover("game", 13_000))
        assertFalse(d.leftover("game", 13_000, confirmedOpening = true))
    }

    @Test fun another_app_dismissal_starts_afresh() {
        val d = BlockDebounce()
        d.dismissed("game", 0); d.otherFront("launcher", 100)
        d.dismissed("chat", 50_000)
        assertTrue(d.leftover("chat", 50_500, confirmedOpening = true))
        assertFalse(d.leftover("game", 50_500))
    }

    @Test fun ok_after_automatic_home_preserves_the_confirmed_departure() {
        val d = BlockDebounce()
        d.dismissed("game", 10_000)
        d.otherFront("launcher", 10_200)
        d.dismissed("game", 10_300)
        assertFalse(d.leftover("game", 10_400, confirmedOpening = true))
    }

    @Test fun new_block_after_a_real_reopen_is_suppressed_again() {
        val d = BlockDebounce()
        d.dismissed("game", 10_000); d.otherFront("launcher", 10_200)
        assertFalse(d.leftover("game", 10_400, confirmedOpening = true))
        d.dismissed("game", 10_500)
        assertTrue(d.leftover("game", 10_600, confirmedOpening = true))
        assertTrue(d.leftover("game", 20_000))
        assertFalse(d.leftover("game", 20_500))
    }
}
