package com.husarp.lockdown.ui

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.automirrored.filled.MenuBook
import androidx.compose.material.icons.filled.Bedtime
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.Casino
import androidx.compose.material.icons.filled.Coffee
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.FitnessCenter
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Groups
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.Newspaper
import androidx.compose.material.icons.filled.Payments
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.Public
import androidx.compose.material.icons.filled.Restaurant
import androidx.compose.material.icons.filled.School
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material.icons.filled.SmartDisplay
import androidx.compose.material.icons.filled.Smartphone
import androidx.compose.material.icons.filled.SportsEsports
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.Work
import androidx.compose.ui.graphics.vector.ImageVector
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.ui.theme.Hue

/**
 * A group's icon and colour: the one picked ([Group.icon] / [Group.color]), or else chosen from its name and id.
 * Only looks - nothing here changes what a group blocks, so picking them is always free.
 */
object GroupLook {
    /** The pickable icons, key -> icon, in the picker's order. */
    val KEYS = listOf("moon", "phone", "game", "social", "video", "chat", "music", "news", "web", "photo", "shopping", "money",
        "casino", "food", "sport", "work", "school", "book", "focus", "coffee", "heart", "star", "bolt", "folder")

    fun vector(key: String): ImageVector = when (key) {
        "moon" -> Icons.Filled.Bedtime
        "phone" -> Icons.Filled.Smartphone
        "game" -> Icons.Filled.SportsEsports
        "social" -> Icons.Filled.Groups
        "video" -> Icons.Filled.SmartDisplay
        "chat" -> Icons.AutoMirrored.Filled.Chat
        "music" -> Icons.Filled.MusicNote
        "news" -> Icons.Filled.Newspaper
        "web" -> Icons.Filled.Public
        "photo" -> Icons.Filled.PhotoCamera
        "shopping" -> Icons.Filled.ShoppingCart
        "money" -> Icons.Filled.Payments
        "casino" -> Icons.Filled.Casino
        "food" -> Icons.Filled.Restaurant
        "sport" -> Icons.Filled.FitnessCenter
        "work" -> Icons.Filled.Work
        "school" -> Icons.Filled.School
        "book" -> Icons.AutoMirrored.Filled.MenuBook
        "focus" -> Icons.Filled.Psychology
        "coffee" -> Icons.Filled.Coffee
        "heart" -> Icons.Filled.Favorite
        "star" -> Icons.Filled.Star
        "bolt" -> Icons.Filled.Bolt
        else -> Icons.Filled.Folder
    }

    /** Name words (English and Polish stems) -> icon, first row that matches wins. A stem under 4 letters (and
     *  [WHOLE]) only matches the whole word: "bet" isn't "Better", "sen" isn't "Senior", "gra" isn't "Grafika". */
    private val WORDS = listOf(
        "moon" to listOf("night", "bed", "bedtime", "sleep", "evening", "noc", "nocn", "sen"),
        "game" to listOf("game", "gaming", "play", "gry", "gra"),
        "social" to listOf("social", "insta", "tiktok", "facebook", "snap", "społ"),
        "video" to listOf("video", "youtube", "netflix", "tv", "film", "movie", "stream", "wideo"),
        "chat" to listOf("chat", "messag", "whatsapp", "discord", "czat"),
        "music" to listOf("music", "spotify", "muzyk"),
        "news" to listOf("news", "wiadomo"),
        "shopping" to listOf("shop", "amazon", "allegro", "zakup"),
        "casino" to listOf("bet", "betting", "casino", "gambl", "hazard"),
        "work" to listOf("work", "praca"),
        "school" to listOf("study", "school", "learn", "nauka", "szkoł"),
        "book" to listOf("read", "book"),
    )

    /** Stems of 4 letters or more that still only match the whole word ("read" isn't "Ready"). */
    private val WHOLE = setOf("read")

    /** The icon a name suggests: a word of it (lower case) that starts with one of [WORDS] - by word start, not
     *  anywhere, so "Instagram" isn't a game ("gra"); a short stem only as the whole word. Anything else: folder. */
    fun autoIcon(name: String): String {
        val words = name.lowercase().split(Regex("[^\\p{L}\\p{N}]+")).filter { it.isNotEmpty() }
        fun hit(w: String, stem: String) = if (stem.length < 4 || stem in WHOLE) w == stem else w.startsWith(stem)
        return WORDS.firstOrNull { (_, stems) -> words.any { w -> stems.any { hit(w, it) } } }?.first ?: "folder"
    }

    /** The automatic colours: never RED or SLATE, so a group doesn't look blocked or paused unless picked so. */
    private val AUTO_HUES = listOf(Hue.ORANGE, Hue.AMBER, Hue.GREEN, Hue.TEAL, Hue.BLUE, Hue.INDIGO, Hue.VIOLET, Hue.PINK)

    fun autoHue(id: String): Hue = AUTO_HUES[Math.floorMod(id.hashCode(), AUTO_HUES.size)]

    /** The group's icon key: the picked one, or from its name (an unknown key, from a newer copy, too). */
    fun iconKey(g: Group): String = g.icon?.takeIf { it in KEYS } ?: autoIcon(g.name)

    fun icon(g: Group): ImageVector = vector(iconKey(g))

    /** The group's hue: the picked one, or from its id (an unknown name too). */
    fun hue(g: Group): Hue = g.color?.let { c -> Hue.entries.firstOrNull { it.name == c } } ?: autoHue(g.id)
}
