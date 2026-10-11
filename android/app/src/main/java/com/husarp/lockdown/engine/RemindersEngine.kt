package com.husarp.lockdown.engine

import kotlinx.serialization.Serializable
import java.time.Duration
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import kotlin.random.Random

/**
 * Reminders scheduler, ported from the PC reminders.py. It only decides *when* each reminder is due and what
 * it should say / offer; the service turns a [TickResult] into notifications or an overlay. Pure and testable:
 * feed it ticks with the current time, how long the user has been idle, and the settings.
 *
 * Faithful to the PC: bedtime warning + overlay with escalation tiers (and an optional mode), work-break cadence
 * with strict auto-start / snooze / 20-20-20, and your own reminders by interval / set times / a daily random
 * time, honouring chosen days, an hours window, a "did you do it?" check, and a per-day Done cap - plus pacing
 * (at most one interruption per [PACE_MIN] min) and back-off (waved away too often → asks half as often).
 */

/** A bedtime "comes back" step. Intervals are in seconds now: [every] (minutes) is a step saved before that, read
 *  through [seconds] when [everySec] is missing; it stays in the file so an older copy (in Island) still reads it. */
@Serializable
data class ReminderTier(val from: String, val every: Int = 0, val everySec: Int? = null) {
    val seconds: Int get() = everySec ?: (every * 60)
}

@Serializable
data class SleepCfg(
    val on: Boolean = false,
    val bedtime: String = "23:00",
    val wake: String = "07:00",
    val before: Int = 30,                       // warn this many minutes before bedtime
    val repeat: Int = 5,                        // minutes, saved before seconds: read through [repeatSeconds]
    val mode: String = "",                      // mode to start at bedtime (empty = none)
    val warnText: String = "",
    val text: String = "",
    // "Comes back every N min after you dismiss it, from HH:MM": the latest step that has passed wins. A step set
    // earlier in the evening than bedtime counts from bedtime. No steps: back every [repeatSeconds].
    val tiers: List<ReminderTier> = listOf(ReminderTier("23:00", 15), ReminderTier("00:00", 5), ReminderTier("03:00", 1)),
    val guarded: Boolean = false,               // dismissing needs the anti-bypass challenge
    val dismissWait: Int = 0,                   // seconds before Dismiss works on the bedtime screen (0 = off)
    val repeatSec: Int? = null,                 // fallback interval (seconds) when no step applies yet
) {
    val repeatSeconds: Int get() = repeatSec ?: (repeat * 60)
}

/** Should bedtime grayscale be on at [mins] (minutes since midnight)? Only with the toggle on, Bedtime on, and inside bedtime→wake. */
fun bedtimeGrayscaleWanted(toggle: Boolean, s: SleepCfg, mins: Int): Boolean {
    if (!toggle || !s.on) return false
    fun hhmm(t: String) = t.split(":").let { (it.getOrNull(0)?.toIntOrNull() ?: 0) * 60 + (it.getOrNull(1)?.toIntOrNull() ?: 0) }
    val bed = hhmm(s.bedtime); val wake = hhmm(s.wake)
    return if (bed <= wake) mins in bed until wake else mins >= bed || mins < wake
}

/**
 * One bedtime-grayscale step: what to write and the new "Lockdown turned it on" mark. [mark] is null when it was
 * never written (a build before 0.5.10, or a reinstall wiped it); [isOn] is null when Android won't say.
 * Returns (write: true = turn grayscale on, false = turn it off, null = leave it; the mark to keep).
 * An unmarked grey screen while the switch is on counts as Lockdown's own (left on by an older copy), so it goes off
 * at wake time; otherwise grayscale nobody marked (the user's own) is never turned off by this.
 * Lockdown's own grayscale is always undone once, even if the colour already came back some other way, so the
 * colour-correction settings it changed are put back too ([grayscaleRestore]).
 */
fun grayscaleStep(wanted: Boolean, switchOn: Boolean, mark: Boolean?, isOn: Boolean?): Pair<Boolean?, Boolean?> {
    val ours = mark ?: (switchOn && isOn != false).takeIf { it }
    return when {
        wanted -> (if (isOn == true) null else true) to true
        ours == true -> false to false
        else -> null to ours
    }
}

/** Android's colour-correction mode for grayscale, and the mode Android uses when none was ever set (deuteranomaly). */
const val DALTONIZER_GRAYSCALE = 0
const val DALTONIZER_DEFAULT = 12

/**
 * What Lockdown writes to give the colour back: (correction on 1/0, mode). [prevEnabled] and [prevMode] are the
 * colour-correction settings from just before Lockdown took the screen over (null = unknown; a mode never set is
 * Android's default). A colour correction of the user's own (not grayscale) comes back as it was. Otherwise
 * correction goes off, and the mode is never left on grayscale: Android's Colour correction switch, tile or shortcut
 * would turn the screen grey again later, and some phones read the mode alone. Grayscale that was already on when
 * bedtime took it over goes off too (it can't be told apart from grey an older copy left on), unless [ownGrey]:
 * Lockdown's last step was giving the colour back, so that grey was the user's own - it comes back as it was.
 */
fun grayscaleRestore(prevEnabled: Int?, prevMode: Int?, ownGrey: Boolean = false): Pair<Int, Int> {
    val mode = prevMode ?: DALTONIZER_DEFAULT
    if (ownGrey && prevEnabled == 1 && mode == DALTONIZER_GRAYSCALE) return 1 to DALTONIZER_GRAYSCALE
    val own = prevEnabled == 1 && mode != DALTONIZER_GRAYSCALE
    return (if (own) 1 else 0) to (if (mode == DALTONIZER_GRAYSCALE) DALTONIZER_DEFAULT else mode)
}

/** The screen is grey outside bedtime and it may be Lockdown's doing (its mark, or no mark and the bedtime grayscale
 *  switch on): the app offers "Turn grayscale off now". Grayscale the user set after Lockdown gave the colour back
 *  (mark false), or with the switch off, is left alone. */
fun grayscaleStuck(wanted: Boolean, switchOn: Boolean, mark: Boolean?, isOn: Boolean?): Boolean =
    isOn == true && !wanted && (mark == true || (mark == null && switchOn))

@Serializable
data class BreakCfg(
    val on: Boolean = true,
    val every: Int = 45,                        // minutes, saved before seconds: read through [everySeconds]
    val length: Int = 5,
    val strict: Boolean = false,
    val snooze: Int = 5,                        // minutes, saved before seconds: read through [snoozeSeconds]
    val maxSnooze: Int = 2,
    val twenty: Boolean = false,                // 20-20-20 eye breaks
    val text: String = "",
    val twentyText: String = "",
    val guarded: Boolean = false,
    val everySec: Int? = null,                  // seconds of use between breaks
    val snoozeSec: Int? = null,                 // a snooze, in seconds
    val allowed: List<String> = emptyList(),    // apps a strict break leaves usable ("Allowed during breaks", engine/BreakAllow.kt)
) {
    val everySeconds: Int get() = everySec ?: (every * 60)
    val snoozeSeconds: Int get() = snoozeSec ?: (snooze * 60)
}

@Serializable
data class CustomCfg(
    val id: String,
    val on: Boolean = true,
    val text: String = "",
    val kind: String = "interval",              // "interval" | "times" | "random"
    val every: Int = 60,                        // minutes, saved before seconds: read through [everySeconds]
    val times: List<String> = listOf("12:00"),  // times: the set times
    val days: List<Int> = listOf(0, 1, 2, 3, 4, 5, 6),
    val window: List<String> = listOf("10:00", "18:00"),
    val snooze: Int = 5,
    val maxSnooze: Int = 3,
    val check: Int = 0,                         // "did you do it?" this many minutes after Done (0 = off)
    val packs: List<String> = emptyList(),      // quote packs to draw a line from
    val quotes: String = "",                    // your own quotes, one per line
    val hours: Boolean = false,                 // interval kind: only inside the window
    val perDay: Int = 0,                        // stop for the day after this many Done (0 = no limit)
    val guarded: Boolean = false,
    val everySec: Int? = null,                  // interval: seconds of use between reminders
) {
    val everySeconds: Int get() = everySec ?: (every * 60)
}

data class RButton(val label: String, val action: String)

/** Something to show now: a notification-style popup, or (overlay=true) a full-screen prompt like bedtime. */
data class RPrompt(
    val key: String,
    val title: String,
    val text: String,
    val buttons: List<RButton>,
    val overlay: Boolean = false,
    val until: LocalDateTime? = null,
    val wait: Int = 0,                          // seconds before its Dismiss works, from each time it's shown
)

/** What a tick decided. The platform layer shows [show], dismisses [close], toasts [toasts], and acts on the rest. */
data class TickResult(
    val show: List<RPrompt> = emptyList(),
    val close: List<String> = emptyList(),
    val toasts: List<String> = emptyList(),
    val startMode: Pair<String, LocalDateTime>? = null,
    val startBreak: LocalDateTime? = null,
    val breakEnded: Boolean = false,
)

class RemindersEngine(private val rng: Random = Random.Default) {
    // ---- state carried across ticks ----
    private val open = HashSet<String>()                       // prompts currently on screen
    private var continuous = 0.0                               // seconds of use since the last break
    private var twenty = 0.0
    private var breakUntil: LocalDateTime? = null
    private var breakSnoozes = 0
    private val snoozed = HashMap<String, LocalDateTime>()     // key -> fire again at
    private val snoozesUsed = HashMap<String, Int>()
    private val counters = HashMap<String, Double>()           // interval reminders: seconds of use so far
    private val fired = HashSet<String>()                      // one-shot markers (warn/bed/set-time/random)
    private val randomAt = HashMap<String, LocalTime>()        // "id|date" -> today's random time
    private val checks = HashMap<String, LocalDateTime>()      // reminder id -> ask "did you do it?" at
    private val sleepNext = HashMap<String, LocalDateTime>()   // night key -> next bedtime overlay
    val doneToday = HashMap<String, Int>()                     // "id|date" -> times Done (per-day cap); persistable
    private var lastInterruption: LocalDateTime? = null
    private val pending = ArrayList<String>()                  // customs due while the pace said "not yet"
    private val streak = HashMap<String, Int>()               // key -> dismissed in a row
    private val backedOff = HashMap<String, LocalDate>()      // key -> the day it asks half as often

    /** One tick. [idleSec] = seconds since the last input; [quiet] = Do-not-disturb / a muting mode (things wait);
     *  [paused] = an emergency paused the bedtime and break alerts; [emergencyLeft] = uses left, offered on the
     *  bedtime screen as "Emergency (N left)"; [silent] = "Pause my blocks" silenced every alert (PC 0.84.8): as
     *  [paused], and your own reminders hold too (one on screen goes) until it ends; [holdCustoms] = only your own
     *  reminders hold, the same way (a muting mode): bedtime and breaks are untouched. */
    fun tick(now: LocalDateTime, idleSec: Double, sleep: SleepCfg, brk: BreakCfg, customs: List<CustomCfg>,
             dt: Double = TICK_SEC.toDouble(), quiet: Boolean = false, paused: Boolean = false,
             emergencyLeft: Int = 0, silent: Boolean = false, holdCustoms: Boolean = false): TickResult {
        val out = Out(now)
        val using = idleSec < USING_IDLE_SEC
        val held = paused || silent
        val breakDue: BreakCfg? = breaks(now, idleSec, using, dt, brk, held, out)
        this.sleep(now, sleep, quiet, held, emergencyLeft, out)
        if (silent || holdCustoms) {
            for (key in open.filter { it.startsWith("custom:") || it.startsWith("check:") }) { grouped.remove(key); close(key, out) }
            pending.clear(); folded.clear()
            if (silent) return out.build()
        } else custom(now, using, dt, customs, quiet, out)
        if (breakDue != null && !quiet) {
            absorb(customs, out)
            showBreak(breakDue, customs, out)
        }
        return out.build()
    }

    // ---------- breaks ----------

    private fun breaks(now: LocalDateTime, idleSec: Double, using: Boolean, dt: Double, b: BreakCfg, paused: Boolean, out: Out): BreakCfg? {
        if (paused) {
            // Emergency: the screen is yours until it ends - no break prompt, and a strict break stops. Use keeps
            // counting, so the next prompt comes when it's due.
            close("break", out)
            if (breakUntil != null) { breakUntil = null; breakSnoozes = 0; out.breakEnded = true }
            if (idleSec >= BREAK_RESET_SEC) continuous = 0.0 else if (using && b.on) continuous += dt
            return null
        }
        breakUntil?.let { until ->
            if (!now.isBefore(until)) {
                out.breakEnded = true; breakUntil = null; continuous = 0.0; breakSnoozes = 0
            }
            return null
        }
        if (!b.on) { continuous = 0.0; return null }
        if (idleSec >= BREAK_RESET_SEC) {
            continuous = 0.0; close("break", out)
        } else if (using) {
            continuous += dt
            if (b.twenty) {
                twenty += dt
                if (twenty >= TWENTY_SEC) { twenty = 0.0; out.toasts.add(message(b.twentyText, TWENTY_TEXT)) }
            }
        }
        if (now.isBefore(snoozed["break"] ?: now)) return null
        if (continuous >= every("break", b.everySeconds.toDouble(), now) && "break" !in open) {
            if (b.strict && breakSnoozes >= b.maxSnooze) { startBreak(now, b, out); return null }
            if ("break" !in snoozed && b.everySeconds >= UNPACED_UNDER_SEC && paced(now)) return null
            snoozed.remove("break")
            return b                                           // shown at the end of the tick
        }
        return null
    }

    private fun absorb(customs: List<CustomCfg>, out: Out) {
        for (key in open.filter { it.startsWith("custom:") }.toList()) {
            val rid = key.removePrefix("custom:")
            if (rid !in folded) folded.add(rid)
            close(key, out)
        }
        for (rid in pending) if (rid !in folded) folded.add(rid)
        pending.clear()
    }

    private val folded = ArrayList<String>()

    private fun showBreak(b: BreakCfg, customs: List<CustomCfg>, out: Out) {
        val snoozesLeft = breakSnoozes < b.maxSnooze
        val buttons = ArrayList<RButton>()
        buttons.add(RButton("Start break", "start"))
        if (!b.strict || snoozesLeft) buttons.add(RButton("Snooze ${Interval.text(b.snoozeSeconds)}", "snooze"))
        if (!b.strict) buttons.add(RButton(DISMISS, "dismiss"))
        // {every} says "45 min" or "30 s"; a "{every} min" (from when it was a number of minutes) says it the same
        var text = message(b.text.replace("{every} min", "{every}"), BREAK_TEXT.replace("{every} min", "{every}"),
            "every" to Interval.text(b.everySeconds), "length" to b.length.toString())
        if (b.strict) {
            val left = b.maxSnooze - breakSnoozes
            text += "\n\nStrict break: $left snooze${if (left != 1) "s" else ""} left, then it starts on its own."
        }
        val byId = customs.associateBy { it.id }
        val lines = folded.mapNotNull { byId[it]?.text }
        if (lines.isNotEmpty()) text += "\n\nWhile you're up:\n" + lines.joinToString("\n") { "• $it" }
        if ("break" in open) close("break", out)
        popup("break", "Time for a break", text, buttons, out)
    }

    private fun startBreak(now: LocalDateTime, b: BreakCfg, out: Out) {
        breakUntil = now.plusMinutes(b.length.toLong())
        breakSnoozes = 0
        close("break", out)
        out.startBreak = breakUntil
    }

    // ---------- sleep ----------

    // A time that doesn't parse (a half-typed "2" saved by an older editor) never throws here: bedtime is then
    // off, and a reminder's bad time is skipped - one bad field must not stop everything else on the tick.
    private fun night(s: SleepCfg, now: LocalDateTime): Pair<LocalDateTime, LocalDateTime>? {
        val bedT = hm(s.bedtime) ?: return null; val wakeT = hm(s.wake) ?: return null
        for (day in listOf(now.toLocalDate().minusDays(1), now.toLocalDate())) {
            val bed = LocalDateTime.of(day, bedT)
            var wake = LocalDateTime.of(day, wakeT)
            if (!wake.isAfter(bed)) wake = wake.plusDays(1)
            if (!now.isBefore(bed.minusMinutes(s.before.toLong())) && now.isBefore(wake)) return bed to wake
        }
        return null
    }

    /** Seconds (at least [Interval.MIN_SEC]). A step's time is placed in the night counted from bedtime; one in the
     *  daytime gap before bedtime (at or after wake) counts from bedtime. Two steps at the same time: the more frequent one. */
    private fun sleepInterval(s: SleepCfg, bed: LocalDateTime, now: LocalDateTime): Int {
        val wakeT = hm(s.wake) ?: return maxOf(Interval.MIN_SEC, s.repeatSeconds)
        val wakeOff = sinceBed(bed.toLocalTime(), wakeT).let { if (it == 0) 1440 else it }
        var best: Pair<LocalDateTime, Int>? = null
        for (t in s.tiers) {
            val tierT = hm(t.from) ?: continue
            val off = sinceBed(bed.toLocalTime(), tierT)
            val whenAt = if (off >= wakeOff) bed else bed.plusMinutes(off.toLong())
            if (whenAt.isAfter(now)) continue
            val b = best
            if (b == null || whenAt.isAfter(b.first) || (whenAt == b.first && t.seconds < b.second)) best = whenAt to t.seconds
        }
        return maxOf(Interval.MIN_SEC, best?.second ?: s.repeatSeconds)
    }

    private fun sleep(now: LocalDateTime, s: SleepCfg, quiet: Boolean, paused: Boolean, emergencyLeft: Int, out: Out) {
        val night = if (s.on) night(s, now) else null
        if (night == null) { close("sleep", out); return }
        val (bed, wake) = night
        val key = bed.toString()
        if (paused) { close("sleep", out); close("sleep-warn", out) }   // nothing on screen until it ends, then as before
        if (now.isBefore(bed)) {
            if ("warn|$key" !in fired && !paused) {
                fired.add("warn|$key")
                popup("sleep-warn", "Bedtime soon",
                    message(s.warnText, SLEEP_WARN_TEXT, "bedtime" to hhmm(bed), "wake" to hhmm(wake), "time" to hhmm(now)),
                    listOf(RButton("OK", "ok")), out)
            }
            return
        }
        if ("bed|$key" !in fired) {
            fired.add("bed|$key")
            sleepNext[key] = now
            if (s.mode.isNotEmpty()) out.startMode = s.mode to wake
        }
        if (!now.isBefore(sleepNext[key] ?: now) && "sleep" !in open && !quiet && !paused) {
            val buttons = arrayListOf(RButton("Dismiss", "dismiss"), RButton("Disable alerts", "disable"))
            // the sanctioned way out, no challenge: it costs an emergency use
            if (emergencyLeft > 0) buttons.add(RButton("Emergency ($emergencyLeft left)", "emergency"))
            overlay("sleep", "Time for bed",
                message(s.text, SLEEP_TEXT, "time" to hhmm(now), "bedtime" to hhmm(bed), "wake" to hhmm(wake)),
                null, buttons, out, wait = maxOf(0, s.dismissWait))
        }
    }

    // ---------- your reminders ----------

    private fun allowedNow(r: CustomCfg, now: LocalDateTime): Boolean {
        if ((now.dayOfWeek.value - 1) !in r.days) return false
        val start = r.window.getOrNull(0)?.let(::hm); val end = r.window.getOrNull(1)?.let(::hm)
        if (r.hours && r.kind != "times" && start != null && end != null) {   // a bad window doesn't hold it back
            val t = now.toLocalTime()
            val inside = if (start < end) t >= start && t < end else t >= start || t < end
            if (!inside) return false
        }
        return !doneForToday(r, now)
    }

    private fun doneForToday(r: CustomCfg, now: LocalDateTime): Boolean {
        if (r.perDay <= 0) return false
        return (doneToday["${r.id}|${now.toLocalDate()}"] ?: 0) >= r.perDay
    }

    private fun custom(now: LocalDateTime, using: Boolean, dt: Double, customs: List<CustomCfg>, quiet: Boolean, out: Out) {
        val today = now.toLocalDate()
        for (r in customs) {
            if (!r.on || r.text.isBlank() || !allowedNow(r, now)) continue
            val key = "custom:${r.id}"
            if (key in snoozed) {
                if (!now.isBefore(snoozed[key]) && key !in open) { snoozed.remove(key); fire(r, now, customs, out, paced = false) }
                continue
            }
            when (r.kind) {
                "interval" -> {
                    if (using && !outstanding(r.id)) counters[r.id] = (counters[r.id] ?: 0.0) + dt
                    if ((counters[r.id] ?: 0.0) >= every(r.id, r.everySeconds.toDouble(), now)) {
                        counters[r.id] = 0.0; fire(r, now, customs, out, paced = r.everySeconds >= UNPACED_UNDER_SEC)
                    }
                }
                "times" -> for (t in r.times) {
                    val at = LocalDateTime.of(today, hm(t) ?: continue)
                    val mark = "${r.id}|$today|$t"
                    if (!now.isBefore(at) && now.isBefore(at.plusMinutes(LATE_FIRE_MIN)) && mark !in fired) {
                        fired.add(mark); fire(r, now, customs, out)
                    }
                }
                else -> {
                    val rk = "${r.id}|$today"
                    val lo = r.window.getOrNull(0)?.let(::hm)?.let { it.hour * 60 + it.minute } ?: continue
                    val hi = r.window.getOrNull(1)?.let(::hm)?.let { it.hour * 60 + it.minute } ?: continue
                    val pick = randomAt.getOrPut(rk) {
                        val m = rng.nextInt(lo, maxOf(lo + 1, hi))
                        LocalTime.of(m / 60, m % 60)
                    }
                    val at = LocalDateTime.of(today, pick)
                    val mark = "${r.id}|$today|random"
                    if (!now.isBefore(at) && now.isBefore(at.plusMinutes(LATE_FIRE_MIN)) && mark !in fired) {
                        fired.add(mark); fire(r, now, customs, out)
                    }
                }
            }
        }
        // the pace allows another interruption: everything that waited comes up together
        if (pending.isNotEmpty() && !quiet && !paced(now)) {
            val byId = customs.associateBy { it.id }
            val due = pending.filter { byId[it]?.on == true && allowedNow(byId.getValue(it), now) }
            pending.clear()
            if (due.isNotEmpty()) {
                val lines = due.mapNotNull { byId[it]?.text }
                val title = if (lines.size > 1) "Reminders" else "Reminder"
                val body = if (lines.size > 1) lines.joinToString("\n") { "• $it" } else lines.first()
                grouped["custom:${due.first()}"] = ArrayList(due)
                popup("custom:${due.first()}", title, body, remindButtons(byId.getValue(due.first())), out)
                lastInterruption = now
            }
        }
        for ((rid, at) in checks.toList()) if (!now.isBefore(at)) {
            checks.remove(rid)
            customs.firstOrNull { it.id == rid }?.let {
                popup("check:$rid", "Did you actually do it?", it.text, listOf(RButton("Yes", "yes"), RButton("No", "no")), out)
            }
        }
    }

    private val grouped = HashMap<String, MutableList<String>>()

    private fun outstanding(rid: String): Boolean {
        if (rid in pending || rid in folded) return true
        return open.any { it.startsWith("custom:") && (grouped[it] ?: listOf(it.removePrefix("custom:"))).contains(rid) }
    }

    private fun remindButtons(r: CustomCfg): List<RButton> {
        val buttons = ArrayList<RButton>()
        buttons.add(RButton("Done", "done"))
        if ((snoozesUsed["custom:${r.id}"] ?: 0) < r.maxSnooze) buttons.add(RButton("Snooze ${r.snooze} min", "snooze"))
        buttons.add(RButton(DISMISS, "dismiss"))
        return buttons
    }

    private fun fire(r: CustomCfg, now: LocalDateTime, customs: List<CustomCfg>, out: Out, paced: Boolean = true) {
        if (outstanding(r.id)) return
        if ("break" in open) { if (r.id !in folded) folded.add(r.id); return }
        val openKey = open.firstOrNull { it.startsWith("custom:") }
        if (openKey != null && openKey != "custom:${r.id}") {
            grouped.getOrPut(openKey) { arrayListOf(openKey.removePrefix("custom:")) }.add(r.id)
            regroup(openKey, customs, out); return
        }
        if (paced && openKey == null && paced(now)) { if (r.id !in pending) pending.add(r.id); return }
        val key = "custom:${r.id}"
        grouped[key] = arrayListOf(r.id)
        val quote = quote(r)
        popup(key, "Reminder", r.text + (if (quote.isNotEmpty()) "\n\n$quote" else ""), remindButtons(r), out)
    }

    private fun regroup(key: String, customs: List<CustomCfg>, out: Out) {
        val byId = customs.associateBy { it.id }
        val wanted = grouped.getValue(key)
        val lines = wanted.mapNotNull { byId[it]?.text }
        val buttons = ArrayList<RButton>()
        buttons.add(RButton("Done", "done"))
        if (wanted.all { (snoozesUsed[key] ?: 0) < (byId[it]?.maxSnooze ?: 0) })
            buttons.add(RButton("Snooze ${byId[wanted.first()]?.snooze ?: 5} min", "snooze"))
        buttons.add(RButton(DISMISS, "dismiss"))
        close(key, out)
        open.add(key)
        out.show.add(RPrompt(key, if (lines.size > 1) "Reminders" else "Reminder",
            if (lines.size > 1) lines.joinToString("\n") { "• $it" } else lines.first(), buttons))
    }

    private fun quote(r: CustomCfg): String {
        val pool = ArrayList<String>()
        r.quotes.lines().mapNotNullTo(pool) { it.trim().ifEmpty { null } }
        for (pack in r.packs) PACKS[pack]?.let { pool.addAll(it) }
        return if (pool.isEmpty()) "" else pool[rng.nextInt(pool.size)]
    }

    // ---------- answers ----------

    /** [given] "swipe": the notification went away without an answer (swiped, or Android removed it). A strict
     *  break's counts as a snooze, so it still starts on its own; a break or reminder's as waved away. */
    fun answer(key: String, given: String, now: LocalDateTime, sleep: SleepCfg, brk: BreakCfg, customs: List<CustomCfg>): TickResult {
        val out = Out(now)
        open.remove(key)
        val action = if (given != "swipe") given else when {
            key == "break" -> if (brk.strict) "snooze" else "dismiss"
            key.startsWith("custom:") -> "dismiss"
            else -> "close"
        }
        when {
            key == "break" -> {
                val fld = ArrayList(folded); folded.clear()
                val byId = customs.associateBy { it.id }
                when (action) {
                    "start" -> {
                        if (brk.strict) startBreak(now, brk, out) else { continuous = 0.0; breakSnoozes = 0 }
                        streak["break"] = 0
                        for (rid in fld) streak[rid] = 0
                    }
                    "snooze" -> { breakSnoozes++; snoozed["break"] = now.plusSeconds(brk.snoozeSeconds.toLong()); folded.addAll(fld) }
                    "dismiss" -> {
                        continuous = 0.0; breakSnoozes = 0
                        dismissed("break", "Time for a break", now, out)
                        for (rid in fld) byId[rid]?.let { dismissed(rid, it.text, now, out) }
                    }
                }
            }
            key == "sleep" -> {
                night(sleep, now)?.let { (bed, wake) ->
                    val nkey = bed.toString()
                    when {
                        action == "off_tonight" -> sleepNext[nkey] = wake
                        action == "back" -> sleepNext[nkey] = now      // up again on the next tick, its wait from the start
                        action.startsWith("snooze:") -> sleepNext[nkey] = now.plusMinutes(maxOf(1L, action.substringAfter(":").toLong()))
                        else -> sleepNext[nkey] = now.plusSeconds(sleepInterval(sleep, bed, now).toLong())
                    }
                }
            }
            key.startsWith("custom:") -> {
                val byId = customs.associateBy { it.id }
                for (rid in grouped.remove(key) ?: mutableListOf(key.removePrefix("custom:"))) {
                    val r = byId[rid] ?: continue
                    when (action) {
                        "done" -> {
                            snoozesUsed.remove(key); streak[rid] = 0; counters[rid] = 0.0
                            val dk = "$rid|${now.toLocalDate()}"
                            doneToday[dk] = (doneToday[dk] ?: 0) + 1
                            if (r.check > 0) checks[rid] = now.plusMinutes(r.check.toLong())
                        }
                        "snooze" -> { snoozesUsed[key] = (snoozesUsed[key] ?: 0) + 1; snoozed[key] = now.plusMinutes(r.snooze.toLong()) }
                        "dismiss" -> { snoozesUsed.remove(key); dismissed(rid, r.text, now, out) }
                    }
                }
            }
            key.startsWith("check:") -> {
                val rid = key.removePrefix("check:")
                if (action == "no") customs.firstOrNull { it.id == rid }?.let { fire(it, now, customs, out, paced = false) }
            }
        }
        return out.build()
    }

    // ---------- helpers ----------

    private fun paced(now: LocalDateTime): Boolean {
        if (open.any { it == "break" || it.startsWith("custom:") }) return false
        val last = lastInterruption ?: return false
        return Duration.between(last, now).seconds < PACE_MIN * 60
    }

    /** An interval in seconds, doubled for the rest of the day once waved away [BACKOFF_AFTER] times running. */
    private fun every(key: String, seconds: Double, now: LocalDateTime): Double =
        seconds * (if (backedOff[key] == now.toLocalDate()) 2 else 1)

    private fun dismissed(key: String, name: String, now: LocalDateTime, out: Out) {
        streak[key] = (streak[key] ?: 0) + 1
        if (streak[key]!! >= BACKOFF_AFTER && backedOff[key] != now.toLocalDate()) {
            backedOff[key] = now.toLocalDate(); streak[key] = 0
            val short = if (name.length <= 40) name else name.take(39).trimEnd() + "…"
            out.toasts.add("Skipped \"$short\" $BACKOFF_AFTER times in a row - it will ask half as often for the rest of today.")
        }
    }

    private fun popup(key: String, title: String, text: String, buttons: List<RButton>, out: Out) {
        if (key in open) return
        open.add(key)
        out.show.add(RPrompt(key, title, text, buttons))
        if (key == "break" || key.startsWith("custom:")) lastInterruption = now(out)
    }

    private fun overlay(key: String, title: String, text: String, until: LocalDateTime?, buttons: List<RButton>, out: Out, wait: Int = 0) {
        if (key in open) return
        open.add(key)
        out.show.add(RPrompt(key, title, text, buttons, overlay = true, until = until, wait = wait))
    }

    private fun close(key: String, out: Out) { if (open.remove(key)) out.close.add(key) }

    // lastInterruption uses the tick's `now`; carry it on the Out so popup() can read it without a field
    private fun now(out: Out) = out.now

    private class Out(var now: LocalDateTime = LocalDateTime.MIN) {
        val show = ArrayList<RPrompt>()
        val close = ArrayList<String>()
        val toasts = ArrayList<String>()
        var startMode: Pair<String, LocalDateTime>? = null
        var startBreak: LocalDateTime? = null
        var breakEnded = false
        fun build() = TickResult(show, close, toasts, startMode, startBreak, breakEnded)
    }

    companion object {
        const val TICK_SEC = 5
        const val USING_IDLE_SEC = 60
        const val BREAK_RESET_SEC = 5 * 60
        const val TWENTY_SEC = 20 * 60
        const val LATE_FIRE_MIN = 30L
        const val PACE_MIN = 20
        /** A break or reminder set under a minute (seconds: a nag you asked for) isn't held back by the pace. */
        const val UNPACED_UNDER_SEC = 60
        const val BACKOFF_AFTER = 3
        const val DISMISS = "✕"

        const val SLEEP_WARN_TEXT = "Bedtime is at {bedtime} - time to wrap up."
        const val SLEEP_TEXT = "It's {time}. Sleep well - the screen can wait until tomorrow."
        const val BREAK_TEXT = "You've been at the screen for {every} min. Take {length} min away from it."
        const val TWENTY_TEXT = "20-20-20: look at something 20 feet (6 m) away for 20 seconds."

        val PACKS: Map<String, List<String>> = mapOf(
            "Stoic" to listOf(
                "You have power over your mind - not outside events. - Marcus Aurelius",
                "Waste no more time arguing what a good man should be. Be one. - Marcus Aurelius",
                "We suffer more often in imagination than in reality. - Seneca",
                "It is not that we have a short time to live, but that we waste a lot of it. - Seneca",
                "First say to yourself what you would be; then do what you have to do. - Epictetus",
            ),
            "Motivation" to listOf(
                "Well begun is half done. - Aristotle",
                "The secret of getting ahead is getting started. - attributed to Mark Twain",
                "It does not matter how slowly you go as long as you do not stop. - attributed to Confucius",
                "Small steps every day.", "Future you will thank you.",
            ),
            "Health" to listOf(
                "Drink a glass of water.", "Roll your shoulders and stretch your neck.",
                "Look out of the window for a moment.", "Stand up and walk around for a minute.",
            ),
        )

        private fun hhmm(t: LocalDateTime) = "%02d:%02d".format(t.hour, t.minute)

        /** "HH:MM", or null if it isn't a time. */
        private fun hm(t: String): LocalTime? = runCatching { Rules.parseHhmm(t) }.getOrNull()

        /** Minutes from bedtime to [t], going forward through the night (0..1439). */
        private fun sinceBed(bed: LocalTime, t: LocalTime): Int =
            ((t.hour * 60 + t.minute) - (bed.hour * 60 + bed.minute) + 1440) % 1440

        /** Your own wording if you wrote any, else the standard text, with {placeholders} filled in. */
        fun message(custom: String, default: String, vararg values: Pair<String, String>): String {
            var t = custom.trim().ifEmpty { default }
            for ((k, v) in values) t = t.replace("{$k}", v)
            return t
        }

        /** A step in the daytime gap before bedtime (wake → bedtime): it counts from bedtime, as the bedtime screen
         *  only starts then. The same test the engine uses. */
        fun stepFromBedtime(s: SleepCfg, from: String): Boolean {
            val t = hm(from) ?: return false
            val bed = hm(s.bedtime) ?: return false
            val wake = hm(s.wake) ?: return false
            val wakeOff = sinceBed(bed, wake).let { if (it == 0) 1440 else it }
            return sinceBed(bed, t) >= wakeOff
        }

        /** Moving the bedtime moves every step by the same amount, so the steps keep their place in the night
         *  (23:00/00:00/03:00 with bedtime 23:00 -> 22:00 gives 22:00/23:00/02:00). */
        fun shiftSteps(tiers: List<ReminderTier>, oldBed: String, newBed: String): List<ReminderTier> {
            val o = hm(oldBed) ?: return tiers
            val n = hm(newBed) ?: return tiers
            val d = sinceBed(o, n)
            return tiers.map { t -> hm(t.from)?.plusMinutes(d.toLong())?.let { t.copy(from = "%02d:%02d".format(it.hour, it.minute)) } ?: t }
        }

        /** The steps in the order of the night: by minutes since [bedtime] (a step at bedtime first, after-midnight
         *  ones after the evening ones). Same time: stays in place. A step that isn't a time keeps going last. */
        fun sortSteps(tiers: List<ReminderTier>, bedtime: String): List<ReminderTier> {
            val bed = hm(bedtime) ?: return tiers
            return tiers.sortedBy { t -> hm(t.from)?.let { sinceBed(bed, it) } ?: Int.MAX_VALUE }
        }

        /** A change that weakens a guarded (important) reminder - turning it off, or dropping its guard. */
        fun loosensReminder(oldOn: Boolean, oldGuarded: Boolean, newOn: Boolean, newGuarded: Boolean): Boolean =
            oldGuarded && ((oldOn && !newOn) || !newGuarded)

        /** A bedtime editor draft that weakens bedtime: its guard dropped, or a shorter wait before Dismiss. Times,
         *  steps, heads-up, texts and the mode are ordinary edits (as on the PC). */
        fun bedtimeLooser(old: SleepCfg, new: SleepCfg): Boolean =
            loosensReminder(old.on, old.guarded, old.on, new.guarded) || loosensDismissWait(old.dismissWait, new.dismissWait)

        /** A breaks editor draft that weakens breaks: dropping its guard, or an app added to "Allowed during breaks"
         *  (removing one is free). */
        fun breaksLooser(old: BreakCfg, new: BreakCfg): Boolean =
            loosensReminder(old.on, old.guarded, old.on, new.guarded) || AntiBypass.breakAppsLooser(old.allowed, new.allowed)

        /** "Wait before Dismiss" choices in seconds (0 = off), and the range a typed one may take. */
        val DISMISS_WAITS = listOf(0, 10, 30, 60)
        const val DISMISS_WAIT_MAX = 600

        /** A typed wait in seconds (1 to [DISMISS_WAIT_MAX]), or null. */
        fun parseDismissWait(text: String): Int? = text.trim().toIntOrNull()?.takeIf { it in 1..DISMISS_WAIT_MAX }

        /** A shorter wait before Dismiss (or none) loosens bedtime: it needs the challenge. Longer is free. */
        fun loosensDismissWait(old: Int, new: Int): Boolean = new < old

        /** Whole seconds until Dismiss works ([readyAt] and [nowMs] on the same monotonic clock); 0 = it works now. */
        fun waitLeft(readyAt: Long, nowMs: Long): Int = if (nowMs >= readyAt) 0 else ((readyAt - nowMs + 999) / 1000).toInt()

        /** The Dismiss button's label: "Dismiss (25)" while it counts down, then "Dismiss". */
        fun dismissLabel(left: Int): String = if (left > 0) "Dismiss ($left)" else "Dismiss"
    }
}
