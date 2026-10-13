package com.husarp.lockdown.engine

/**
 * The bedtime editor's parts of "Challenge before Dismiss" that need no screen: which lists there are (the general one
 * and each "Comes back" step's own), what a switch does to them, why they can't be saved and how long they take.
 * Which list the bedtime screen asks at a given moment is [RemindersEngine.challengeAt]; whether an edit loosens it is
 * [RemindersEngine.challengeLooser]. Pure and testable.
 */
object BedtimeChallenge {
    /** Turned on: the general list as in force (a first time Math Easy x2, see [Challenges.BEDTIME_DEFAULT]). */
    fun turnOn(s: SleepCfg): SleepCfg = s.copy(challenge = true, challengeSteps = s.generalChallenge)

    /** Does [t] have its own list? Empty or none = it uses the general one. */
    fun hasOwn(t: ReminderTier): Boolean = Challenges.clean(t.challengeSteps).isNotEmpty()

    /** [t] switched to "Own list": it starts as a copy of the general list [general] (same ids, so the switch itself
     *  makes nothing easier and the stricter / looser check follows each row). */
    fun useOwn(t: ReminderTier, general: List<ChallengeStep>): ReminderTier = t.copy(challengeSteps = general)

    /** [t] switched back to "Uses the general list": its own list goes. */
    fun useGeneral(t: ReminderTier): ReminderTier = t.copy(challengeSteps = null)

    /** Every list the bedtime screen can ask: the general one, then each step's own list (those that have one). */
    fun lists(s: SleepCfg): List<List<ChallengeStep>> =
        listOf(s.generalChallenge) + s.tiers.map { Challenges.clean(it.challengeSteps) }.filter { it.isNotEmpty() }

    /** Does any list have a phrase step (so the Numbers & CAPITALS and 3x3 grid extras apply)? */
    fun hasPhraseStep(s: SleepCfg): Boolean = lists(s).any { l -> l.any { it.kind == Challenges.PHRASE } }

    /** Why [s] can't be saved (a plain message), or null. Only while the challenge is on: the general list, then each
     *  step's own list ([Challenges.validate]; an empty own list isn't one - it uses the general list). */
    fun error(s: SleepCfg): String? {
        if (!s.challenge) return null
        Challenges.validate(s.challengeSteps)?.let { return "Challenge before Dismiss: $it" }
        for (t in s.tiers) {
            val own = t.challengeSteps?.takeIf { it.isNotEmpty() } ?: continue
            Challenges.validate(own)?.let { return "Challenge for the step from ${t.from}: $it" }
        }
        return null
    }

    /** "Takes about 2 min" for [steps] with the bedtime phrase extras of [s] (no own phrase at bedtime). */
    fun takes(s: SleepCfg, steps: List<ChallengeStep>): String =
        Challenges.takesText(Challenges.takesSeconds(Challenges.clean(steps), s.challengePhrase))
}
