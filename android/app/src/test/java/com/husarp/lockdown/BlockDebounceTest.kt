package com.husarp.lockdown

import com.husarp.lockdown.engine.BlockDebounce
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BlockDebounceTest {
    @Test fun nothing_is_ignored_before_a_dismissal() {
        assertFalse(BlockDebounce().leftover("game", 1000))
    }

    @Test fun leftover_events_and_the_launcher_transition_are_ignored() {
        val d = BlockDebounce()
        d.dismissed("game", 10_000)
        assertTrue(d.leftover("game", 10_100))          // the closing app's events
        d.otherFront("launcher", 10_200)
        assertTrue(d.leftover("game", 10_400))          // ... also after the launcher came up
        assertTrue(d.leftover("game", 11_000))
        assertFalse(d.leftover("launcher", 10_300))     // another app is never ignored
    }

    @Test fun a_real_reopen_after_leaving_is_judged() {
        val d = BlockDebounce()
        d.dismissed("game", 10_000)
        d.otherFront("launcher", 10_200)
        assertFalse(d.leftover("game", 11_800))         // launcher was up 1.6 s: a reopen
        assertFalse(d.leftover("game", 11_900))         // and it stays judged
    }

    @Test fun without_a_launcher_event_the_app_is_judged_again_after_the_settle_time() {
        val d = BlockDebounce()
        d.dismissed("game", 10_000)
        assertTrue(d.leftover("game", 12_900))
        assertFalse(d.leftover("game", 13_000))         // still in front 3 s after OK: block it again
    }

    @Test fun another_dismissal_starts_afresh() {
        val d = BlockDebounce()
        d.dismissed("game", 0); d.otherFront("launcher", 100)
        d.dismissed("chat", 50_000)
        assertTrue(d.leftover("chat", 50_500))
        assertFalse(d.leftover("game", 50_500))
    }
}
