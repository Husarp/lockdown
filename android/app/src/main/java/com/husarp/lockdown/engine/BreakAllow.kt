package com.husarp.lockdown.engine

/**
 * Which apps a strict break leaves alone (pure logic; the phone's built-in set is looked up in block/BreakApps.kt).
 *
 * A strict break covers the screen with "Break time". Its Dismiss button is free: the cover goes and the phone goes
 * home. After that the cover only comes back when an app that is NOT allowed comes to the front. Allowed: the
 * built-in set (Lockdown, the home screen, Settings, the dialer, the clock, the system pieces that must never be
 * covered, the keyboard, the emergency apps), anything while the phone rings (engine/CallCover.kt: not an app's
 * voice call), and the user's own "Allowed during breaks" list.
 */
object BreakAllow {
    /** In-call screens and the emergency dialer, besides the default dialer app. */
    val CALL_PKGS = setOf("com.android.phone", "com.android.incallui", "com.samsung.android.incallui",
        "com.android.server.telecom")

    /** System pieces that exist on (nearly) every phone under these names; the real set is also resolved from intents
     *  on the phone itself, so another vendor's names are found there. */
    val KNOWN = setOf("com.android.systemui", "com.android.settings", "com.google.android.permissioncontroller",
        "com.android.permissioncontroller", "com.android.packageinstaller", "com.google.android.packageinstaller",
        "com.android.emergency") + CALL_PKGS

    /** Is [pkg] fine to be in front during a break? An unknown front (null / empty) is allowed: nothing is known to be
     *  wrong with it. [builtIn] is the phone's resolved set, [user] the user's list, [inCall] a call the
     *  screens step aside for (CallCover.stepsAside). */
    fun allowed(pkg: String?, builtIn: Set<String>, user: Collection<String>, inCall: Boolean): Boolean =
        pkg.isNullOrEmpty() || inCall || pkg in KNOWN || pkg in builtIn || pkg in user

    /** Does the break cover the screen now? Not yet dismissed: always. Dismissed: only while [front] isn't allowed. */
    fun covered(dismissed: Boolean, front: String?, builtIn: Set<String>, user: Collection<String>, inCall: Boolean): Boolean =
        !dismissed || !allowed(front, builtIn, user, inCall)

    /** A saved list with blanks and repeats dropped (order kept). */
    fun clean(list: List<String>): List<String> = list.map { it.trim() }.filter { it.isNotEmpty() }.distinct()

    /** The package part of Android's "package/Class" input-method id, or null. */
    fun keyboardPackage(id: String?): String? = id?.substringBefore('/')?.trim()?.takeIf { it.isNotEmpty() }
}
