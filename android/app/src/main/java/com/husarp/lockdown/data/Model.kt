package com.husarp.lockdown.data

import com.husarp.lockdown.engine.AntiBypassCfg
import com.husarp.lockdown.engine.BreakCfg
import com.husarp.lockdown.engine.CustomCfg
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.KeywordsCfg
import com.husarp.lockdown.engine.LimitClock
import com.husarp.lockdown.engine.SleepCfg
import kotlinx.serialization.Serializable
import java.time.LocalTime

/**
 * Everything the app persists, in one JSON file - now built on the full-parity engine model (Item / Group /
 * the engine's keyword, reminder, anti-bypass configs). New fields need defaults so older files still load.
 */
@Serializable
data class Config(
    val enabled: Boolean = true,                       // master switch (Home / tile). false = nothing enforced
    val items: List<Item> = emptyList(),               // blocked apps + sites
    val groups: List<Group> = emptyList(),             // shared rule sets
    val keywords: KeywordsCfg = KeywordsCfg(),
    val sleep: SleepCfg = SleepCfg(),
    val breaks: BreakCfg = BreakCfg(),
    val customs: List<CustomCfg> = emptyList(),
    val antibypass: AntiBypassCfg = AntiBypassCfg(),
    val emergency: EmergencyCfg = EmergencyCfg(),
    val protection: ProtectionCfg = ProtectionCfg(),
    val guardrails: Guardrails = Guardrails(),
    val categories: Map<String, String> = emptyMap(),  // "app:<pkg>" / "site:<host>" -> productive|neutral|distracting
    val settings: Settings = Settings(),
    val unlocks: List<String> = emptyList(),           // emergency-unlock start times (ISO), for the weekly/daily quota
    val unlockUntil: String? = null,                   // current emergency unlock end (ISO), or null
    val unlockItems: List<String> = emptyList(),       // item ids the current unlock covers
    val alertsPausedUntil: String? = null,             // an emergency paused bedtime + break alerts until (ISO), or null
    val resetHour: Int = 0,                            // custom limit-day start (default midnight)
    val resetMin: Int = 0,
) {
    fun clock() = LimitClock(resetTime = LocalTime.of(resetHour.coerceIn(0, 23), resetMin.coerceIn(0, 59)))
}

@Serializable
data class EmergencyCfg(
    val enabled: Boolean = true,
    val minutes: Int = 20,
    val uses: Int = 3,
    val per: String = "week",                          // "day" | "week"
)

@Serializable
data class ProtectionCfg(
    val enabled: List<String> = emptyList(),           // keys of the lists that are on
    val allowed: List<String> = emptyList(),           // domains excused (false positives)
)

@Serializable
data class Guardrails(
    val uninstallProtection: Boolean = false,          // device-admin so it can't be uninstalled while on
    val persist: Boolean = true,                       // keep the service alive; restart on kill/boot
    val trustedTime: Boolean = true,                   // ignore the clock being set backwards
)

@Serializable
data class Settings(
    val theme: String = "system",                      // system / light / dark
    val checkUpdates: Boolean = true,
    val siteFilterOn: Boolean = false,                 // whether the DNS-filter VPN should run
    val pauseBeforeOpen: Boolean = false,
    val pauseSec: Int = 10,
    val openCapPerDay: Int = 0,                         // 0 = off
    val bedtimeGrayscale: Boolean = false,
    val forceSafeSearch: Boolean = true,
    val weeklyDigest: Boolean = false,
    val networkLog: Boolean = true,
    val dailyGoalMin: Int = 0,                          // daily screen-time goal in minutes (0 = off)
)
