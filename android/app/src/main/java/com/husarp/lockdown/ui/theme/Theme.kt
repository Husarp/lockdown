package com.husarp.lockdown.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider

/**
 * The Lockdown look: the designer's orange-seeded Material 3 scheme (light + dark), Barlow Condensed / Inter
 * type, and sharp corners. The brand palette is used as-is (not Material You) so the app matches the mockups.
 * [pref] is "system", "light" or "dark".
 */
@Composable
fun LockdownTheme(pref: String = "system", content: @Composable () -> Unit) {
    val dark = when (pref) {
        "light" -> false
        "dark" -> true
        else -> isSystemInDarkTheme()
    }
    CompositionLocalProvider(LocalExtraColors provides if (dark) DarkExtra else LightExtra) {
        MaterialTheme(
            colorScheme = if (dark) DarkColors else LightColors,
            typography = LockdownTypography,
            shapes = LockdownShapes,
            content = content,
        )
    }
}

/** Shortcut to the extra (success / warning) colors from anywhere in the theme. */
object LockdownTheme {
    val extra: ExtraColors
        @Composable get() = LocalExtraColors.current
}
