package com.husarp.lockdown.block

import android.content.Context
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.view.KeyEvent

/** Stops a video / audio that is playing when its block starts (it used to carry on under the notice or in PiP). */
object Media {
    fun playing(ctx: Context): Boolean = runCatching { ctx.getSystemService(AudioManager::class.java).isMusicActive }.getOrDefault(false)

    /** Pause what's playing: the media "pause" key goes to the session that is playing, and taking the audio focus
     *  for a moment makes a player that respects it stop for good (YouTube, browsers). */
    fun pause(ctx: Context) {
        val am = ctx.getSystemService(AudioManager::class.java) ?: return
        runCatching {
            for (action in listOf(KeyEvent.ACTION_DOWN, KeyEvent.ACTION_UP))
                am.dispatchMediaKeyEvent(KeyEvent(action, KeyEvent.KEYCODE_MEDIA_PAUSE))
        }
        runCatching {
            val req = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN).build()
            if (am.requestAudioFocus(req) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED) am.abandonAudioFocusRequest(req)
        }
    }
}
