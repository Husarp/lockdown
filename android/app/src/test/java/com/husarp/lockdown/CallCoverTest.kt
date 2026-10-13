package com.husarp.lockdown

import com.husarp.lockdown.engine.BreakAllow
import com.husarp.lockdown.engine.CallCover
import com.husarp.lockdown.engine.CallCover.Call
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CallCoverTest {
    // AudioManager.MODE_* values (android.media.AudioManager)
    private val normal = 0; private val ringtone = 1; private val inCall = 2; private val inCommunication = 3
    private val callScreening = 4; private val callRedirect = 5; private val communicationRedirect = 6

    @Test fun audio_modes_map_to_calls() {
        assertEquals(Call.NONE, CallCover.of(normal))
        assertEquals(Call.RINGING, CallCover.of(ringtone))
        assertEquals(Call.RINGING, CallCover.of(callScreening))
        assertEquals(Call.PHONE, CallCover.of(inCall))
        assertEquals(Call.PHONE, CallCover.of(callRedirect))
        assertEquals(Call.APP, CallCover.of(inCommunication))
        assertEquals(Call.APP, CallCover.of(communicationRedirect))
        assertEquals(Call.NONE, CallCover.of(-2))      // MODE_INVALID / unknown
    }

    @Test fun an_app_voice_call_never_hides_the_screens() {
        // WhatsApp / Discord: the bedtime screen stays, whatever is in front ("you can always call back")
        assertFalse(CallCover.stepsAside(CallCover.of(inCommunication), callScreenInFront = false))
        assertFalse(CallCover.stepsAside(CallCover.of(inCommunication), callScreenInFront = true))
        assertFalse(CallCover.stepsAside(CallCover.of(communicationRedirect), callScreenInFront = false))
    }

    @Test fun the_phone_ringing_hides_them_so_the_call_can_be_answered() {
        assertTrue(CallCover.stepsAside(Call.RINGING, callScreenInFront = false))
        assertTrue(CallCover.stepsAside(Call.RINGING, callScreenInFront = true))
    }

    @Test fun a_phone_call_hides_them_only_while_its_own_screen_is_in_front() {
        assertTrue(CallCover.stepsAside(Call.PHONE, callScreenInFront = true))
        // another app over the call (keeping a call to voicemail on to use the phone): the screen is back
        assertFalse(CallCover.stepsAside(Call.PHONE, callScreenInFront = false))
    }

    @Test fun the_screens_are_back_as_soon_as_the_call_ends() {
        assertFalse(CallCover.stepsAside(CallCover.of(normal), callScreenInFront = true))
        assertFalse(CallCover.stepsAside(Call.NONE, callScreenInFront = false))
    }

    @Test fun a_dismissed_break_with_an_app_call_still_covers_a_not_allowed_app() {
        val builtIn = setOf("com.example.launcher")
        val aside = CallCover.stepsAside(CallCover.of(inCommunication), callScreenInFront = false)
        assertTrue(BreakAllow.covered(true, "com.whatsapp", builtIn, emptyList(), aside))
        // ... unless the user put it on "Allowed during breaks"
        assertFalse(BreakAllow.covered(true, "com.whatsapp", builtIn, listOf("com.whatsapp"), aside))
        // the phone ringing frees the screen whatever is in front
        assertFalse(BreakAllow.covered(true, "com.game.fun", builtIn, emptyList(), CallCover.stepsAside(Call.RINGING, false)))
        // a phone call with a game over it: covered
        assertTrue(BreakAllow.covered(true, "com.game.fun", builtIn, emptyList(), CallCover.stepsAside(Call.PHONE, false)))
    }
}
