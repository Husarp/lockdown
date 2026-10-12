package com.husarp.lockdown.data

import com.husarp.lockdown.engine.AlertsCfg
import com.husarp.lockdown.engine.AntiBypassCfg
import com.husarp.lockdown.engine.BlockPause
import com.husarp.lockdown.engine.BreakCfg
import com.husarp.lockdown.engine.CustomCfg
import com.husarp.lockdown.engine.Emergency
import com.husarp.lockdown.engine.EmergencyTier
import com.husarp.lockdown.engine.EmergencyUse
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.KeywordsCfg
import com.husarp.lockdown.engine.LimitClock
import com.husarp.lockdown.engine.ResetCarry
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.SleepCfg
import kotlinx.serialization.Serializable
import kotlinx.serialization.Transient
import java.time.LocalDateTime
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
    val alerts: AlertsCfg = AlertsCfg(),               // block notices, warnings, "block started" (engine/Alerts.kt)
    val emergencyUses: List<EmergencyUse> = emptyList(),  // one record per emergency use (engine/Emergency.kt)
    // The single emergency from before kinds (0.5.14 and older): read through allUses(), never written again.
    val unlocks: List<String> = emptyList(),           // emergency-unlock start times (ISO), for the weekly/daily quota
    val unlockUntil: String? = null,                   // current emergency unlock end (ISO), or null
    val unlockItems: List<String> = emptyList(),       // item ids the current unlock covers
    val alertsPausedUntil: String? = null,             // an emergency paused bedtime + break alerts until (ISO), or null
    val pause: BlockPause? = null,                     // "Pause my blocks" (engine/Pause.kt), or null. Not carried by an import
    val resetHour: Int = 0,                            // custom limit-day start (default midnight)
    val resetMin: Int = 0,
    val resetCarry: ResetCarry? = null,                // what the last change of the reset time keeps running
) {
    fun resetTime(): LocalTime = LocalTime.of(resetHour.coerceIn(0, 23), resetMin.coerceIn(0, 59))
    fun clock() = LimitClock.of(resetTime(), resetCarry)

    /** Every emergency use: [emergencyUses] plus the ones from before kinds - each start in [unlocks] a counted use
     *  of the migrated kind, and a still-set [unlockUntil] / [alertsPausedUntil] a running use of no kind, begun
     *  the old length before its end (so the old cap holds). Worked out once per config (blocking asks it often). */
    fun allUses(): List<EmergencyUse> = everyUse ?: collectUses().also { everyUse = it }

    @Transient private var everyUse: List<EmergencyUse>? = null     // (not a lazy: kotlinx decoding breaks its lambda)

    private fun collectUses(): List<EmergencyUse> {
        val old = unlocks.map { EmergencyUse(it, Emergency.LEGACY_TIER, it) }.toMutableList()
        fun running(until: String?, items: List<String>, alerts: Boolean) {
            val end = until?.let { runCatching { LocalDateTime.parse(it) }.getOrNull() } ?: return
            old.add(EmergencyUse(end.minusMinutes(emergency.minutes.toLong()).toString(), "", until, items, alerts))
        }
        running(unlockUntil, unlockItems, false)
        running(alertsPausedUntil, emptyList(), true)
        return if (old.isEmpty()) emergencyUses else old + emergencyUses
    }

    /** A use of emergency kind [tierId] at [now] (trusted time), checked against this config (PC emergency.unlock). */
    fun startEmergency(tierId: String, picked: List<String>, alerts: Boolean, now: LocalDateTime): Emergency.Started =
        Emergency.start(emergency.enabled, emergency.tierList(), allUses(), tierId, picked, alerts, now, clock()) { id ->
            items.firstOrNull { it.id == id }?.let { Rules.permanent(Rules.effectiveRules(it, groups)) } == true
        }

    /** [startEmergency], saved: the config with the use added (unchanged if refused), and the result. */
    fun withEmergency(tierId: String, picked: List<String>, alerts: Boolean, now: LocalDateTime): Pair<Config, Emergency.Started> {
        val r = startEmergency(tierId, picked, alerts, now)
        return (r.use?.let { copy(emergencyUses = emergencyUses + it) } ?: this) to r
    }
}

@Serializable
data class EmergencyCfg(
    val enabled: Boolean = true,                       // off: nothing can be used (the kinds are kept)
    val minutes: Int = 20,                             // the single kind from before kinds: read only while tiers is null
    val uses: Int = 3,
    val per: String = "week",                          // "day" | "week"
    val tiers: List<EmergencyTier>? = null,            // the kinds; null = not migrated (the one above), [] = all removed
) {
    fun tierList(): List<EmergencyTier> = Emergency.clean(tiers ?: listOf(EmergencyTier(Emergency.LEGACY_TIER, uses, per, minutes)))
}

@Serializable
data class ProtectionCfg(
    val enabled: List<String> = emptyList(),           // keys of the lists that are on
    val allowed: List<String> = emptyList(),           // domains excused (false positives)
)

@Serializable
data class Guardrails(
    val uninstallProtection: Boolean = false,          // device-admin so it can't be uninstalled while on
    val persist: Boolean = true,                       // keep the service alive; restart on kill/boot
    val trustedTime: Boolean = true,                   // ignore the clock being changed; a new time zone after 24 h
    val helperAlert: Boolean = true,                   // linked Island helper silent for 2 min of screen time: loud repeating alert + counted
)

@Serializable
data class Settings(
    val theme: String = "system",                      // system / light / dark
    val checkUpdates: Boolean = true,
    val siteFilterOn: Boolean = false,                 // whether the DNS-filter VPN should run
    val pauseBeforeOpen: Boolean = false,
    val pauseSec: Int = 10,
    val bedtimeGrayscale: Boolean = false,
    val forceSafeSearch: Boolean = true,
    val weeklyDigest: Boolean = false,
    val networkLog: Boolean = true,
    val dailyGoalMin: Int = 0,                          // daily screen-time goal in minutes (0 = off)
    val sitesInsteadOfBrowsers: Boolean = true,         // Insights' top apps and the CSV list sites, not one row per browser
    val siteIcons: Boolean = true,                      // download real icons for the sites on your lists (ui/SiteIcons.kt); off = bundled + saved + letter only. Free both ways
    val home: List<HomeCard>? = null,                   // Home's cards, in order (data/HomeLayout.kt); null = the default
)

/** One card on Home and whether it shows. Looks only: saved at once, never a challenge. */
@Serializable
data class HomeCard(val id: String, val on: Boolean = true)
