package com.husarp.lockdown.ui.theme

import androidx.compose.material3.ColorScheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/**
 * The Lockdown Mobile palette, taken straight from the designer's mockups (an orange-seeded Material 3 tonal
 * scheme, light + dark). success / warning aren't Material roles, so they ride along in [ExtraColors].
 */

val LightColors: ColorScheme = lightColorScheme(
    primary = Color(0xFFA93D17), onPrimary = Color(0xFFFFFFFF),
    primaryContainer = Color(0xFFFFDBCF), onPrimaryContainer = Color(0xFF3A0B00),
    secondary = Color(0xFF77574A), onSecondary = Color(0xFFFFFFFF),
    secondaryContainer = Color(0xFFFFDBCF), onSecondaryContainer = Color(0xFF2C160C),
    tertiary = Color(0xFF6C5D2F), onTertiary = Color(0xFFFFFFFF),
    tertiaryContainer = Color(0xFFF5E1A7), onTertiaryContainer = Color(0xFF221B00),
    background = Color(0xFFFFF8F6), onBackground = Color(0xFF231917),
    surface = Color(0xFFFFF8F6), onSurface = Color(0xFF231917),
    surfaceVariant = Color(0xFFF5E4DE), onSurfaceVariant = Color(0xFF53433F),
    surfaceContainerLowest = Color(0xFFFFFFFF),
    surfaceContainerLow = Color(0xFFFFF1EC),
    surfaceContainer = Color(0xFFFBEAE4),
    surfaceContainerHigh = Color(0xFFF5E4DE),
    surfaceContainerHighest = Color(0xFFEFDED8),
    outline = Color(0xFF85736E), outlineVariant = Color(0xFFD8C2BC),
    error = Color(0xFFBA1A1A), onError = Color(0xFFFFFFFF),
    errorContainer = Color(0xFFFFDAD6), onErrorContainer = Color(0xFF410002),
    inverseSurface = Color(0xFF392E2B), inverseOnSurface = Color(0xFFFFEDE7), inversePrimary = Color(0xFFFFB59C),
)

val DarkColors: ColorScheme = darkColorScheme(
    primary = Color(0xFFFFB59C), onPrimary = Color(0xFF5C1900),
    primaryContainer = Color(0xFF7E2B0C), onPrimaryContainer = Color(0xFFFFDBCF),
    secondary = Color(0xFFE7BDAD), onSecondary = Color(0xFF442A1F), secondaryContainer = Color(0xFF7E2B0C),
    onSecondaryContainer = Color(0xFFFFDBCF),
    tertiary = Color(0xFFD8C68D), onTertiary = Color(0xFF3A2F05), tertiaryContainer = Color(0xFF524619),
    onTertiaryContainer = Color(0xFFF5E1A7),
    background = Color(0xFF1A110F), onBackground = Color(0xFFF1DFDA),
    surface = Color(0xFF1A110F), onSurface = Color(0xFFF1DFDA),
    surfaceVariant = Color(0xFF322825), onSurfaceVariant = Color(0xFFD8C2BC),
    surfaceContainerLowest = Color(0xFF140C0A),
    surfaceContainerLow = Color(0xFF231917),
    surfaceContainer = Color(0xFF271D1B),
    surfaceContainerHigh = Color(0xFF322825),
    surfaceContainerHighest = Color(0xFF3D3230),
    outline = Color(0xFFA08C87), outlineVariant = Color(0xFF53433F),
    error = Color(0xFFFFB4AB), onError = Color(0xFF690005),
    errorContainer = Color(0xFF93000A), onErrorContainer = Color(0xFFFFDAD6),
    inverseSurface = Color(0xFFF1DFDA), inverseOnSurface = Color(0xFF392E2B), inversePrimary = Color(0xFFA93D17),
)

/** Extra semantic colors the Material scheme has no slot for. */
data class ExtraColors(
    val success: Color, val successContainer: Color, val onSuccessContainer: Color,
    val warning: Color, val warningContainer: Color, val onWarningContainer: Color,
)

val LightExtra = ExtraColors(
    success = Color(0xFF306A43), successContainer = Color(0xFFC8EED3), onSuccessContainer = Color(0xFF00391C),
    warning = Color(0xFF8A5A00), warningContainer = Color(0xFFFFDFA6), onWarningContainer = Color(0xFF2B1700),
)

val DarkExtra = ExtraColors(
    success = Color(0xFF8BD9A0), successContainer = Color(0xFF0E5128), onSuccessContainer = Color(0xFFC8EED3),
    warning = Color(0xFFF5C04E), warningContainer = Color(0xFF5E4200), onWarningContainer = Color(0xFFFFDFA6),
)

val LocalExtraColors = staticCompositionLocalOf { LightExtra }

/**
 * The colour language (redesign 2026-10): one hue per kind of block, state and section, always with an icon and a
 * word. Each hue is an accent (icons, bars, text on surfaces), a container (tiles, pills) and the text on that
 * container, made from Material's HCT tonal palettes and checked for WCAG contrast. ORANGE is the brand primary,
 * GREEN and AMBER are [ExtraColors]' success and warning. A plain enum, so the meaning tables are unit-testable.
 */
enum class Hue { RED, ORANGE, AMBER, GREEN, TEAL, BLUE, INDIGO, VIOLET, PINK, SLATE }

@Immutable
data class Tone(val accent: Color, val container: Color, val onContainer: Color)

private fun tone(a: Long, c: Long, on: Long) = Tone(Color(a), Color(c), Color(on))

/** Indexed by [Hue.ordinal]. */
val LightTones: Array<Tone> = arrayOf(
    tone(0xFFAD2F41, 0xFFFFDADA, 0xFF40000C),   // RED (crimson, away from the brand orange)
    tone(0xFFA93D17, 0xFFFFDBCF, 0xFF3A0B00),   // ORANGE (= primary)
    tone(0xFF8A5A00, 0xFFFFDFA6, 0xFF2B1700),   // AMBER (= warning)
    tone(0xFF306A43, 0xFFC8EED3, 0xFF00391C),   // GREEN (= success)
    tone(0xFF246867, 0xFFADEEEC, 0xFF00201F),   // TEAL
    tone(0xFF006496, 0xFFCCE5FF, 0xFF001E31),   // BLUE
    tone(0xFF4359A9, 0xFFDCE1FF, 0xFF001551),   // INDIGO
    tone(0xFF6E4DA1, 0xFFECDCFF, 0xFF280056),   // VIOLET
    tone(0xFF983D71, 0xFFFFD8E8, 0xFF3C0028),   // PINK
    tone(0xFF575F67, 0xFFDBE4ED, 0xFF141D23),   // SLATE
)

val DarkTones: Array<Tone> = arrayOf(
    tone(0xFFFFB3B6, 0xFF8C142C, 0xFFFFDADA),
    tone(0xFFFFB59C, 0xFF7E2B0C, 0xFFFFDBCF),
    tone(0xFFF5C04E, 0xFF5E4200, 0xFFFFDFA6),
    tone(0xFF8BD9A0, 0xFF0E5128, 0xFFC8EED3),
    tone(0xFF91D2D0, 0xFF00504F, 0xFFADEEEC),
    tone(0xFF91CDFF, 0xFF004B72, 0xFFCCE5FF),
    tone(0xFFB7C4FF, 0xFF294190, 0xFFDCE1FF),
    tone(0xFFD6BAFF, 0xFF563587, 0xFFECDCFF),
    tone(0xFFFFAFD6, 0xFF7B2559, 0xFFFFD8E8),
    tone(0xFFBFC8D0, 0xFF3F484F, 0xFFDBE4ED),
)

val LocalTones = staticCompositionLocalOf { LightTones }
