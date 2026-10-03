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

@Serializable
data class ReminderTier(val from: String, val every: Int)

@Serializable
data class SleepCfg(
    val on: Boolean = false,
    val bedtime: String = "23:00",
    val wake: String = "07:00",
    val before: Int = 30,                       // warn this many minutes before bedtime
    val repeat: Int = 5,                        // fallback interval when no tier applies yet
    val mode: String = "",                      // mode to start at bedtime (empty = none)
    val warnText: String = "",
    val text: String = "",
    // "Comes back every N min after you dismiss it, from HH:MM": the latest step that has passed wins. A step set
    // earlier in the evening than bedtime counts from bedtime. No steps: back every [repeat] min.
    val tiers: List<ReminderTier> = listOf(ReminderTier("23:00", 15), ReminderTier("00:00", 5), ReminderTier("03:00", 1)),
    val guarded: Boolean = false,               // dismissing needs the anti-bypass challenge
)

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
 */
fun grayscaleStep(wanted: Boolean, switchOn: Boolean, mark: Boolean?, isOn: Boolean?): Pair<Boolean?, Boolean?> {
    val ours = mark ?: (switchOn && isOn != false).takeIf { it }
    return when {
        wanted -> (if (isOn == true) null else true) to true
        ours == true -> (if (isOn == false) null else false) to false
        else -> null to ours
    }
}

@Serializable
data class BreakCfg(
    val on: Boolean = true,
    val every: Int = 45,
    val length: Int = 5,
    val strict: Boolean = false,
    val snooze: Int = 5,
    val maxSnooze: Int = 2,
    val twenty: Boolean = false,                // 20-20-20 eye breaks
    val text: String = "",
    val twentyText: String = "",
    val guarded: Boolean = false,
)

@Serializable
data class CustomCfg(
    val id: String,
    val on: Boolean = true,
    val text: String = "",
    val kind: String = "interval",              // "interval" | "times" | "random"
    val every: Int = 60,                        // interval: minutes of use between reminders
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
)

data class RButton(val label: String, val action: String)

/** Something to show now: a notification-style popup, or (overlay=true) a full-screen prompt like bedtime. */
data class RPrompt(
    val key: String,
    val title: String,
    val text: String,
    val buttons: List<RButton>,
    val overlay: Boolean = false,
    val until: LocalDateTime? = null,
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
     *  [paused], and your own reminders hold too (one on screen goes) until it ends. */
    fun tick(now: LocalDateTime, idleSec: Double, sleep: SleepCfg, brk: BreakCfg, customs: List<CustomCfg>,
             dt: Double = TICK_SEC.toDouble(), quiet: Boolean = false, paused: Boolean = false,
             emergencyLeft: Int = 0, silent: Boolean = false): TickResult {
        val out = Out(now)
        val using = idleSec < USING_IDLE_SEC
        val held = paused || silent
        val breakDue: BreakCfg? = breaks(now, idleSec, using, dt, brk, held, out)
        this.sleep(now, sleep, quiet, held, emergencyLeft, out)
        if (silent) {
            for (key in open.filter { it.startsWith("custom:") || it.startsWith("check:") }) { grouped.remove(key); close(key, out) }
            pending.clear(); folded.clear()
            return out.build()
        }
        custom(now, using, dt, customs, quiet, out)
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
        if (continuous >= every("break", b.every.toDouble(), now) * 60 && "break" !in open) {
            if (b.strict && breakSnoozes >= b.maxSnooze) { startBreak(now, b, out); return null }
            if ("break" !in snoozed && paced(now)) return null
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
        if (!b.strict || snoozesLeft) buttons.add(RButton("Snooze ${b.snooze} min", "snooze"))
        if (!b.strict) buttons.add(RButton(DISMISS, "dismiss"))
        var text = message(b.text, BREAK_TEXT, "every" to b.every.toString(), "length" to b.length.toString())
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

    /** A step's time is placed in the night counted from bedtime; one in the daytime gap before bedtime (at or
     *  after wake) counts from bedtime. Two steps at the same time: the more frequent one. */
    private fun sleepInterval(s: SleepCfg, bed: LocalDateTime, now: LocalDateTime): Int {
        val wakeT = hm(s.wake) ?: return s.repeat
        val wakeOff = sinceBed(bed.toLocalTime(), wakeT).let { if (it == 0) 1440 else it }
        var best: Pair<LocalDateTime, Int>? = null
        for (t in s.tiers) {
            val tierT = hm(t.from) ?: continue
            val off = sinceBed(bed.toLocalTime(), tierT)
            val whenAt = if (off >= wakeOff) bed else bed.plusMinutes(off.toLong())
            if (whenAt.isAfter(now)) continue
            val b = best
            if (b == null || whenAt.isAfter(b.first) || (whenAt == b.first && t.every < b.second)) best = whenAt to t.every
        }
        return best?.let { maxOf(1, it.second) } ?: s.repeat
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
                null, buttons, out)
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
                    if ((counters[r.id] ?: 0.0) >= every(r.id, r.every.toDouble(), now) * 60) {
                        counters[r.id] = 0.0; fire(r, now, customs, out)
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
                    "snooze" -> { breakSnoozes++; snoozed["break"] = now.plusMinutes(brk.snooze.toLong()); folded.addAll(fld) }
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
                        action.startsWith("snooze:") -> sleepNext[nkey] = now.plusMinutes(maxOf(1L, action.substringAfter(":").toLong()))
                        else -> sleepNext[nkey] = now.plusMinutes(sleepInterval(sleep, bed, now).toLong())
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

    private fun every(key: String, minutes: Double, now: LocalDateTime): Double =
        minutes * (if (backedOff[key] == now.toLocalDate()) 2 else 1)

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

    private fun overlay(key: String, title: String, text: String, until: LocalDateTime?, buttons: List<RButton>, out: Out) {
        if (key in open) return
        open.add(key)
        out.show.add(RPrompt(key, title, text, buttons, overlay = true, until = until))
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

        /** A change that weakens a guarded (important) reminder - turning it off, or dropping its guard. */
        fun loosensReminder(oldOn: Boolean, oldGuarded: Boolean, newOn: Boolean, newGuarded: Boolean): Boolean =
            oldGuarded && ((oldOn && !newOn) || !newGuarded)
    }
}
