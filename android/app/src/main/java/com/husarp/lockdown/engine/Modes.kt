package com.husarp.lockdown.engine

import kotlinx.serialization.Serializable
import java.time.LocalDateTime

/**
 * Modes, ported from the PC modes.py. While a mode is on it blocks a category (e.g. everything marked
 * Distracting) plus picked items / groups / ad-hoc targets, on top of the normal blockers. One mode at a time;
 * started by hand (for a while / until a time / until stopped, optionally locked) or by its schedule. A Focus
 * mode is a Pomodoro: it blocks in the focus rounds only. Pure logic - persistence lives in ModesStore.
 */

@Serializable
data class Pomodoro(val work: Int = 25, val brk: Int = 5, val rounds: Int = 4, val long: Int = 15)

/** An ad-hoc target added straight to a mode (not on the blocklist). */
@Serializable
data class ModeExtra(val name: String, val kind: ItemType, val targets: List<String>, val blockType: String = "")

@Serializable
data class Mode(
    val id: String,
    val name: String,
    val builtin: Boolean = false,
    val categories: List<String> = emptyList(),
    val items: List<String> = emptyList(),       // item ids
    val groups: List<String> = emptyList(),      // group ids
    val extra: List<ModeExtra> = emptyList(),
    val mute: Boolean = false,                   // silence notifications while on
    val pomodoro: Pomodoro? = null,
    val schedule: Schedule? = null,              // the windows are when the mode is on
)

/** The saved "a mode is running" state (started by hand). */
@Serializable
data class ModeActive(val id: String, val started: String, val until: String? = null, val locked: Boolean = false)

/** The mode that's on right now, resolved. [phase] is (name, end, round) for a Pomodoro, else null. */
data class ModeState(
    val mode: Mode,
    val started: LocalDateTime,
    val until: LocalDateTime?,
    val locked: Boolean,
    val scheduled: Boolean,
    val phase: Triple<String, LocalDateTime, Int>?,
)

/** One thing a mode blocks (an existing item, or a made-up one for a category member / extra with itemId null). */
data class ModeTarget(val name: String, val target: String, val type: ItemType, val blockType: String, val itemId: String? = null)

object Modes {
    val DEFAULT_POMODORO = Pomodoro()

    val DEFAULT_MODES = listOf(
        Mode("work", "Work", builtin = true, categories = listOf("distracting")),
        Mode("study", "Study", builtin = true, categories = listOf("distracting")),
        Mode("focus", "Focus", builtin = true, categories = listOf("distracting"), pomodoro = DEFAULT_POMODORO),
        Mode("dnd", "Do Not Disturb", builtin = true, categories = listOf("distracting"), mute = true),
        Mode("relax", "Relax", builtin = true),
    )

    /** Merge saved edits over the built-ins (kept flagged builtin), then append custom modes. */
    fun merge(saved: List<Mode>): List<Mode> {
        val byId = saved.associateBy { it.id }
        val defaultIds = DEFAULT_MODES.mapTo(HashSet()) { it.id }
        val builtins = DEFAULT_MODES.map { d -> byId[d.id]?.copy(builtin = true) ?: d }
        return builtins + saved.filter { it.id !in defaultIds }
    }

    fun newId(modes: List<Mode>): String {
        var n = 1
        val ids = modes.mapTo(HashSet()) { it.id }
        while ("custom$n" in ids) n++
        return "custom$n"
    }

    // ---------- which mode is on ----------

    fun pomodoroLength(p: Pomodoro): Long =
        (p.rounds * p.work + (p.rounds - 1) * p.brk + p.long).toLong()   // minutes

    /** (phase name, phase end, round) for the Pomodoro at [now], or null once every round is done. With [repeat]
     *  (a scheduled window) the cycle starts again from round 1 when the rounds are done, so it never ends. */
    fun pomodoroPhase(p: Pomodoro, started: LocalDateTime, now: LocalDateTime, repeat: Boolean = false): Triple<String, LocalDateTime, Int>? {
        var t = started
        if (repeat && !now.isBefore(started)) {
            val len = pomodoroLength(p) * 60
            t = started.plusSeconds(java.time.Duration.between(started, now).seconds / len * len)
        }
        for (rnd in 1..p.rounds) {
            val phases = listOf(
                "focus" to p.work,
                (if (rnd < p.rounds) "break" else "long break") to (if (rnd < p.rounds) p.brk else p.long),
            )
            for ((name, minutes) in phases) {
                val end = t.plusMinutes(minutes.toLong())
                if (now.isBefore(end)) return Triple(name, end, rnd)
                t = end
            }
        }
        return null
    }

    /** The mode that's on: one started by hand wins over a scheduled one. */
    fun active(now: LocalDateTime, modes: List<Mode>, state: ModeActive?): ModeState? {
        val byId = modes.associateBy { it.id }
        val chosen = state?.id?.let { byId[it] }
        if (state != null && chosen != null) {
            val started = LocalDateTime.parse(state.started)
            var until = state.until?.let { LocalDateTime.parse(it) }
            val pom = chosen.pomodoro?.let { clamp(it) }     // odd values from old configs / sync can't make a negative length
            pom?.let { p ->
                val pEnd = started.plusMinutes(pomodoroLength(p))
                until = until?.let { minOf(it, pEnd) } ?: pEnd
            }
            if (!started.isAfter(now) && (until == null || now.isBefore(until))) {
                val phase = pom?.let { pomodoroPhase(it, started, now) }
                return ModeState(chosen, started, until, state.locked, scheduled = false, phase = phase)
            }
        }
        for (mode in modes) {
            val sched = mode.schedule ?: continue
            val until = Rules.scheduleUntil(sched, now) ?: continue
            // A Pomodoro mode cycles inside its window, counted from the window start (block windows only); the
            // last phase is cut at the window end. Never locked, so a break gives no way to edit or stop anything.
            val winStart = if (sched.mode == SchedMode.BLOCK && mode.pomodoro != null)
                sched.windows.filter { Rules.windowUntil(it, now) == until }.firstNotNullOfOrNull { Rules.windowStart(it, now) } else null
            val phase = winStart?.let { pomodoroPhase(clamp(mode.pomodoro!!), it, now, repeat = true) }
                ?.let { if (it.second.isAfter(until)) it.copy(second = until) else it }
            return ModeState(mode, winStart ?: now, if (until == LocalDateTime.MAX) null else until,
                locked = false, scheduled = true, phase = phase)
        }
        return null
    }

    /** Does the active mode block right now? (Not during Pomodoro breaks; Relax blocks nothing extra.) */
    fun blocking(state: ModeState?): Boolean {
        if (state == null) return false
        val phase = state.phase
        return !(phase != null && phase.first != "focus")
    }

    /** Whether a hand-started mode may be stopped now (locked ones need the challenge → force). */
    fun canStop(state: ModeState?, force: Boolean): Boolean =
        force || state == null || !state.locked || state.scheduled

    // ---------- Pomodoro lengths, "until a time", schedule text, looseness ----------

    /** Bounds of the editable Pomodoro lengths (minutes / rounds). */
    val WORK_RANGE = 1..180
    val BREAK_RANGE = 1..60
    val ROUNDS_RANGE = 1..12
    val LONG_RANGE = 1..120

    fun clamp(p: Pomodoro) = Pomodoro(p.work.coerceIn(WORK_RANGE), p.brk.coerceIn(BREAK_RANGE),
        p.rounds.coerceIn(ROUNDS_RANGE), p.long.coerceIn(LONG_RANGE))

    /** "Until [hhmm]": today at that time, or tomorrow when it isn't after [now] (a time already gone means the next one). */
    fun untilTime(now: LocalDateTime, hhmm: String): LocalDateTime {
        val t = Rules.parseHhmm(hhmm)
        val today = now.toLocalDate().atTime(t)
        return if (today.isAfter(now)) today else today.plusDays(1)
    }

    private val DAY_NAMES = listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

    private fun daysText(days: List<Int>): String {
        val d = days.distinct().sorted()
        return when {
            d.size == 7 -> "every day"
            d.isEmpty() -> "no days"
            d == listOf(0, 1, 2, 3, 4) -> "Mon\u2013Fri"
            d == listOf(5, 6) -> "Sat\u2013Sun"
            d.size > 2 && d.last() - d.first() == d.size - 1 -> DAY_NAMES[d.first()] + "\u2013" + DAY_NAMES[d.last()]
            else -> d.joinToString(", ") { DAY_NAMES[it] }
        }
    }

    /** "Runs 09:00–17:00 Mon–Fri" (one part per window), or null with no schedule. */
    fun scheduleText(s: Schedule?): String? =
        s?.windows?.takeIf { it.isNotEmpty() }?.joinToString("; ", prefix = "Runs ") { "${it.start}\u2013${it.end} ${daysText(it.days)}" }

    /** When the schedule next switches the mode on (null if it is on now, or never). */
    fun nextStart(mode: Mode, now: LocalDateTime): LocalDateTime? {
        val s = mode.schedule ?: return null
        if (Rules.scheduleUntil(s, now) != null) return null
        return Rules.nextWindowStart(s.windows, now)
    }

    /** Is [id] held right now: hand-started locked, or its schedule window is running (a standing commitment)? */
    fun holdsNow(now: LocalDateTime, modes: List<Mode>, state: ModeActive?, id: String): Boolean {
        val cur = active(now, modes, state)
        if (cur != null && cur.mode.id == id && cur.locked && !cur.scheduled) return true
        val s = modes.firstOrNull { it.id == id }?.schedule ?: return false
        return Rules.scheduleUntil(s, now) != null
    }

    /** Would saving [new] over [old] loosen it? A schedule that runs fewer minutes always does (it is a standing
     *  commitment), and so does a shorter Pomodoro work / fewer rounds / longer breaks on a scheduled mode (the
     *  lengths now set its cycle inside the window); while the mode is held [lockedNow] (locked or its window
     *  running) blocking less does too. Longer, more, or added is free. */
    fun modeLooser(old: Mode, new: Mode, lockedNow: Boolean): Boolean {
        if (old.schedule != null && AntiBypass.scheduleLooser(old.schedule, new.schedule)) return true
        if (!lockedNow && old.schedule == null) return false
        val op = old.pomodoro
        val np = new.pomodoro
        if (op != null && (np == null || np.work < op.work || np.rounds < op.rounds || np.brk > op.brk || np.long > op.long)) return true
        if (!lockedNow) return false
        return !new.categories.containsAll(old.categories) || !new.items.containsAll(old.items) ||
            !new.groups.containsAll(old.groups) || !new.extra.containsAll(old.extra)
    }

    /** Would starting [newId] until [newUntil] ([newLocked]) over the running [current] loosen? Only a hand-started
     *  locked mode holds: another mode, an unlocked start or an earlier end replaces it and needs the challenge. */
    fun startLooser(current: ModeState?, newId: String, newUntil: LocalDateTime?, newLocked: Boolean): Boolean {
        if (current == null || !current.locked || current.scheduled) return false
        val end = current.until ?: return false
        if (current.mode.id != newId || !newLocked) return true
        return newUntil == null || newUntil.isBefore(end)
    }

    /** Does "Start now" for [draft] ([saved] = what is stored, null for a new mode) need the challenge? One check
     *  covers: the draft loosening the saved mode, replacing a locked running mode, and starting a different mode
     *  while a scheduled one is in its window (a standing commitment that Stop can't end). */
    fun startNeedsChallenge(now: LocalDateTime, modes: List<Mode>, state: ModeActive?, saved: Mode?, draft: Mode,
                            newUntil: LocalDateTime?, newLocked: Boolean): Boolean {
        val cur = active(now, modes, state)
        val lockedNow = holdsNow(now, modes, state, draft.id)
        if (saved != null && modeLooser(saved, draft, lockedNow)) return true
        if (startLooser(cur, draft.id, newUntil, newLocked)) return true
        val sched = active(now, modes, null)       // the scheduled mode in its window, even when a hand-started one hides it
        return sched != null && sched.mode.id != draft.id
    }

    // ---------- what it blocks ----------

    /** An item's category: chosen for its app / one of its own hostnames, else Distracting. Keys: "app:<pkg>" /
     *  "site:<host>". Distracting wins (PC 0.84.1): music.youtube.com under Neutral used to make the whole YouTube
     *  item Neutral (the first match won), so Work / Focus stopped blocking it. The main hostname is Distracting
     *  unless chosen otherwise; the item's other hostnames only count where something was chosen for them. */
    fun itemCategory(item: Item, categories: Map<String, String>): String {
        if (item.type == ItemType.APP) return categories["app:${item.target.lowercase()}"] ?: "distracting"
        val hosts = item.target.lowercase().split(" ").filter { it.isNotEmpty() }
        if (hosts.isEmpty()) return "distracting"
        val chosen = listOf(categories["site:${hosts[0]}"] ?: "distracting") + hosts.drop(1).mapNotNull { categories["site:$it"] }
        return if ("distracting" in chosen) "distracting" else chosen[0]
    }

    /** Re-marking something as [new] loosens when a mode blocks its [old] category (null = none chosen) but not
     *  [new] - e.g. Distracting -> Productive while Work blocks Distracting. More blocked is free. */
    fun categoryLooser(modes: List<Mode>, old: String?, new: String): Boolean =
        old != null && modes.any { old in it.categories && new !in it.categories }

    private fun toTarget(item: Item): ModeTarget {
        val bt = item.blockType.ifEmpty { if (item.type == ItemType.APP) "close" else "" }
        return ModeTarget(item.name, item.target, item.type, bt, item.id)
    }

    /** The things a mode blocks: category members + explicitly-picked items / groups + ad-hoc extras. */
    fun targets(mode: Mode, items: List<Item>, groups: List<Group>, categories: Map<String, String>): List<ModeTarget> {
        val cats = mode.categories.toSet()
        val out = ArrayList<ModeTarget>()
        val seen = HashSet<String>()
        fun add(t: ModeTarget) { if (seen.add(t.target.lowercase())) out.add(t) }

        val pickedGroups = groups.filter { it.id in mode.groups }
        for (item in items) {
            val inCat = itemCategory(item, categories) in cats
            val inGroups = pickedGroups.any { item.id in it.memberIds }
            if (inCat || item.id in mode.items || inGroups) add(toTarget(item))
        }
        for (t in categoryMembers(cats, items, categories, blocklist = false)) add(t)
        for (extra in mode.extra) {
            val bt = extra.blockType.ifEmpty { if (extra.kind == ItemType.APP) "close" else "" }
            add(ModeTarget(extra.name, extra.targets.joinToString(" "), extra.kind, bt))
        }
        return out
    }

    /** Everything in [cats]: blocklist items counted into it (when [blocklist]) plus the category's own members. */
    fun categoryMembers(cats: Set<String>, items: List<Item>, categories: Map<String, String>,
                        blocklist: Boolean = true, blockType: String = ""): List<ModeTarget> {
        val out = ArrayList<ModeTarget>()
        val seen = HashSet<Pair<ItemType, String>>()
        fun add(t: ModeTarget) { if (seen.add(t.type to t.target.lowercase())) out.add(t) }

        if (blocklist) for (item in items) if (itemCategory(item, categories) in cats) add(toTarget(item))
        for ((k, cat) in categories) if (cat in cats) {
            val type = if (k.startsWith("app:")) ItemType.APP else ItemType.SITE
            val name = k.substringAfter(':')
            val bt = if (type == ItemType.APP) blockType.ifEmpty { "close" } else ""
            add(ModeTarget(name, name, type, bt))
        }
        return out
    }

    fun blockedApps(targets: List<ModeTarget>): Set<String> =
        targets.filter { it.type == ItemType.APP }.mapTo(HashSet()) { it.target.lowercase() }

    fun blockedHosts(targets: List<ModeTarget>): Set<String> =
        targets.filter { it.type == ItemType.SITE }
            .flatMap { it.target.lowercase().split(" ") }.filterTo(HashSet()) { it.isNotEmpty() }

    fun describe(mode: Mode, categoryNames: Map<String, String> = emptyMap()): String {
        val parts = mode.categories.map { categoryNames[it] ?: it.replaceFirstChar(Char::uppercase) }.toMutableList()
        val n = mode.items.size + mode.groups.size + mode.extra.size
        if (n > 0) parts.add("$n more")
        return "Blocks: " + (if (parts.isNotEmpty()) parts.joinToString(" + ") else "nothing extra")
    }
}
