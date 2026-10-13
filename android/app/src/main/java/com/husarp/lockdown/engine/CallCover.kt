package com.husarp.lockdown.engine

/**
 * Calls and the bedtime / strict-break screens (pure logic; block/BlockService.kt reads the phone's audio mode and the
 * app in front). Adam, 2026-10-13: "Voice calls shouldn't hide the bedtime screen - you can always call back."
 *
 * - The phone ringing: the screens step aside, so the call can be answered (and an emergency call always gets through).
 * - A phone (cellular) call on: they step aside only while the call's own screen (the dialer / in-call screen) is in
 *   front - talk, speaker, keypad, hang up. Any other app over the call brings them back, and so does the end of the
 *   call, so a call (to voicemail, a free number...) can't be used to keep them away.
 * - An app's voice or video call (WhatsApp, Discord, Messenger...: Android's "in communication" audio mode): nothing.
 */
object CallCover {
    enum class Call { NONE, RINGING, PHONE, APP }

    /** The call [mode] (AudioManager.MODE_*, as numbers so this stays plain Kotlin) stands for. Only the phone itself
     *  can set the in-call mode (MODIFY_PHONE_STATE), so an app can't pass its call off as a phone call. */
    fun of(mode: Int): Call = when (mode) {
        1, 4 -> Call.RINGING          // MODE_RINGTONE, MODE_CALL_SCREENING (the phone screening an incoming call)
        2, 5 -> Call.PHONE            // MODE_IN_CALL, MODE_CALL_REDIRECT (a phone call's audio sent elsewhere)
        3, 6 -> Call.APP              // MODE_IN_COMMUNICATION, MODE_COMMUNICATION_REDIRECT
        else -> Call.NONE
    }

    /** Do the bedtime / strict-break screens step aside for [call]? [callScreenInFront]: the app in front is the
     *  default dialer or an in-call screen. */
    fun stepsAside(call: Call, callScreenInFront: Boolean): Boolean = when (call) {
        Call.RINGING -> true
        Call.PHONE -> callScreenInFront
        Call.APP, Call.NONE -> false
    }
}
