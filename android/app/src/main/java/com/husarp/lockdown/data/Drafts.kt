package com.husarp.lockdown.data

import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.AntiBypassCfg
import com.husarp.lockdown.engine.EmergencyTier

/*
 * What the Guardrails and Settings pages edit as a draft (like the PC's): shown on the page, but nothing reaches the
 * config or the blocking until Save. Save asks for the challenge once when anything in the whole draft loosens
 * (looser, against the saved settings), then writes only the page's own settings (applyTo).
 */

/** Guardrails: the challenge, the emergency unlock and the switches. */
data class GuardrailsEdit(
    val ab: AntiBypassCfg,                 // without the unlock window (that isn't a setting, and keeps running)
    val emergencyOn: Boolean,
    val tiers: List<EmergencyTier>,
    val guardrails: Guardrails,
) {
    /** Weaker than [old]: the challenge itself, the emergency unlock (AntiBypass), or a switch turned off. */
    fun looser(old: GuardrailsEdit): Boolean =
        AntiBypass.settingsLooser(old.ab, ab) ||
            AntiBypass.emergencyLooser(old.emergencyOn, old.tiers, emergencyOn, tiers) ||
            (old.guardrails.persist && !guardrails.persist) ||
            (old.guardrails.trustedTime && !guardrails.trustedTime) ||
            (old.guardrails.uninstallProtection && !guardrails.uninstallProtection) ||
            (old.guardrails.helperAlert && !guardrails.helperAlert)

    /** The Guardrails sections whose settings differ from [old] (their unsaved dot): "challenge", "emergency",
     *  "switches". */
    fun changedSections(old: GuardrailsEdit): Set<String> = buildSet {
        if (ab != old.ab) add("challenge")
        if (emergencyOn != old.emergencyOn || tiers != old.tiers) add("emergency")
        if (guardrails != old.guardrails) add("switches")
    }

    /** Why it can't be saved, or null: hours with a window of no days would never let a change through. */
    fun problem(): String? =
        if (ab.hours && (ab.windows.isEmpty() || ab.windows.any { it.days.isEmpty() })) "Pick at least one day for each time window."
        else null

    /** [c] with these settings. The unlock window the challenge opened stays, and the challenge or the emergency
     *  left as they are aren't written (an emergency from before kinds stays as it is). */
    fun applyTo(c: Config): Config {
        val was = of(c)
        return c.copy(
            antibypass = if (ab == was.ab) c.antibypass
                else ab.copy(unlockedFrom = c.antibypass.unlockedFrom, unlockedUntil = c.antibypass.unlockedUntil),
            emergency = if (emergencyOn == was.emergencyOn && tiers == was.tiers) c.emergency
                else c.emergency.copy(enabled = emergencyOn, tiers = tiers),
            guardrails = guardrails,
        )
    }

    companion object {
        fun of(c: Config) = GuardrailsEdit(AntiBypass.lock(c.antibypass), c.emergency.enabled, c.emergency.tierList(), c.guardrails)
    }
}

/** Settings: the site filter, the protection lists and the extras. Appearance and Notifications loosen nothing and
 *  keep saving at once; the reset time has its own Change. */
data class SettingsEdit(
    val siteFilter: Boolean,
    val protection: Set<String>,
    val pauseBeforeOpen: Boolean,
    val pauseSec: Int,
    val bedtimeGrayscale: Boolean,
    val forceSafeSearch: Boolean,
    val weeklyDigest: Boolean,
    val networkLog: Boolean,
    val dailyGoalMin: Int,
) {
    /** Weaker than [old]: the site filter, a protection list, the pause before opening (or a shorter one) or
     *  SafeSearch turned off. */
    fun looser(old: SettingsEdit): Boolean =
        (old.siteFilter && !siteFilter) || !protection.containsAll(old.protection) ||
            (old.pauseBeforeOpen && !pauseBeforeOpen) || pauseSec < old.pauseSec ||
            (old.forceSafeSearch && !forceSafeSearch)

    /** The Settings sections whose settings differ from [old] (their unsaved dot): "filter" (site filter, protection
     *  lists, SafeSearch, network log), "limits" (pause before opening, daily goal), "extras" (bedtime grayscale,
     *  weekly digest). */
    fun changedSections(old: SettingsEdit): Set<String> = buildSet {
        if (siteFilter != old.siteFilter || protection != old.protection || forceSafeSearch != old.forceSafeSearch ||
            networkLog != old.networkLog) add("filter")
        if (pauseBeforeOpen != old.pauseBeforeOpen || pauseSec != old.pauseSec || dailyGoalMin != old.dailyGoalMin) add("limits")
        if (bedtimeGrayscale != old.bedtimeGrayscale || weeklyDigest != old.weeklyDigest) add("extras")
    }

    /** [c] with these settings. The site filter isn't written here: starting or stopping it does that. */
    fun applyTo(c: Config): Config = c.copy(
        settings = c.settings.copy(pauseBeforeOpen = pauseBeforeOpen, pauseSec = pauseSec, bedtimeGrayscale = bedtimeGrayscale,
            forceSafeSearch = forceSafeSearch, weeklyDigest = weeklyDigest, networkLog = networkLog, dailyGoalMin = dailyGoalMin),
        protection = c.protection.copy(enabled = c.protection.enabled.filter { it in protection } +
            protection.filter { it !in c.protection.enabled }),
    )

    companion object {
        fun of(c: Config) = with(c.settings) {
            SettingsEdit(siteFilterOn, c.protection.enabled.toSet(), pauseBeforeOpen, pauseSec, bedtimeGrayscale,
                forceSafeSearch, weeklyDigest, networkLog, dailyGoalMin)
        }
    }
}

/** [new] with what was in [saved] kept in its saved place (then what's new), so an item taken out and put back is
 *  no change at all. */
fun <T> inSavedOrder(saved: List<T>, new: List<T>): List<T> {
    val now = new.toHashSet(); val was = saved.toHashSet()
    return saved.filter { it in now } + new.filter { it !in was }
}
