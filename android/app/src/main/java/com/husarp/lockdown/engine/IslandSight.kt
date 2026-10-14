package com.husarp.lockdown.engine

import com.husarp.lockdown.link.Foreground
import kotlinx.serialization.Serializable

/** One visit to an Island app, from Island's usage events (wall ms; [to] is "now" while it is still in front). */
@Serializable
data class IslandFront(val pkg: String, val from: Long, val to: Long)

/**
 * Can main see Island apps itself? In Android (AOSP since 5.0, checked in the Android 15 source) a work profile
 * shares its parent's accessibility state: the events and windows of Island apps reach main's accessibility
 * service, with their plain package name (no profile on it - YouTube in Island looks like YouTube). Where that
 * holds, main counts and blocks Island apps like its own: the rules are by package and the Island copy runs main's
 * rules anyway, and limits are shared across the two profiles anyway. The Island copy then needs no foreground
 * service, so it is not in Android's "Active apps" list and nothing there can stop the blocking.
 *
 * Main takes over only once it has seen it happen: the Island copy reports its visits (from Island's own usage
 * events) and main checks each against what its accessibility service heard at that moment. It keeps checking
 * (the Island copy still reports every time it wakes) and hands back as soon as it misses Island apps; a phone
 * that never passes Island's events on keeps the old way (the Island copy's service). Pure.
 *
 * Counting: main counts what its service sees in front, whichever profile it is in; the Island copy counts only in
 * its service, which runs only while main doesn't cover Island - so a minute is never counted by both.
 */
object IslandSight {
    const val MIN_MS = 3_000L              // a visit shorter than this isn't judged (a flick through, a trampoline)
    const val EARLY_MS = 300L              // main's event may be logged a moment before Island's usage event time
    const val LOOK_MS = 15_000L            // ... and is looked for in the first 15 s of the visit
    const val NEAR_MS = 30_000L            // main heard nothing at all this close to the visit: screen off / service down
    const val BACK_MS = 20 * 60_000L       // how far back the Island copy reports visits
    const val KEEP_MS = 25 * 60_000L       // how long main keeps what its service heard
    const val THROTTLE_MS = 1_000L         // one entry per app per second in main's log
    const val SAME_VISIT_MS = 1_000L       // back in front this soon: still the same visit (a move between its screens)
    const val MAX_LOG = 4_000
    const val MAX_FRONTS = 60
    const val CONFIRM = 3                  // seen in a row (and no miss in the window) before main takes over
    const val WINDOW = 10                  // the last judged visits that count
    const val MAX_MISSES = 2               // this many misses in the window and main hands back
    /** Main covers Island: Lockdown in Island checks in only when it wakes (every 15 min), so it is "recent" for longer. */
    const val COVERED_SEEN_MS = 60 * 60_000L

    /** One event from Island's usage log. */
    data class Ev(val ms: Long, val type: Int, val pkg: String, val cls: String)

    /** Island copy: its visits from usage events in time order (the app in front, as [Foreground] tracks it), leaving
     *  out Lockdown itself ([self]: main's service ignores its own package) and visits shorter than [MIN_MS]. Moving
     *  between screens of one app (its PAUSED, then RESUMED a moment later) stays one visit. The last one is still
     *  going at [now]. At most [MAX_FRONTS], the newest. */
    fun visits(events: List<Ev>, now: Long, self: String): List<IslandFront> {
        var resumed = emptyList<String>()
        var cur: String? = null
        var since = 0L
        val out = ArrayList<IslandFront>()
        for (e in events) {
            resumed = Foreground.reduce(resumed, listOf(Triple(e.type, e.pkg, e.cls)))
            val f = Foreground.front(resumed)
            if (f == cur) continue
            cur?.let { out.add(IslandFront(it, since, e.ms)) }
            cur = f; since = e.ms
            val prev = out.lastOrNull()
            if (f != null && prev != null && prev.pkg == f && e.ms - prev.to in 0..SAME_VISIT_MS) { out.removeAt(out.size - 1); since = prev.from }
        }
        cur?.let { out.add(IslandFront(it, since, maxOf(since, now))) }
        return out.filter { it.pkg != self && it.to - it.from >= MIN_MS }.takeLast(MAX_FRONTS)
    }

    /** What main accepts from the Island copy: a real package name, a visit that ends after it starts and lasts at
     *  most a day, at most [MAX_FRONTS]. */
    fun clean(fronts: List<IslandFront>): List<IslandFront> = fronts.filter {
        it.pkg.isNotBlank() && it.pkg.length <= 255 && it.to >= it.from && it.to - it.from <= 86_400_000L
    }.takeLast(MAX_FRONTS)

    /**
     * Main: did its accessibility service hear from [f]'s app while it was in front in Island? [log]: (package, wall
     * ms) of what it heard, complete from [since] on. null when it can't tell: Lockdown itself, a visit too short,
     * before main was listening, or main heard nothing at all around then (screen off, its service down) - those
     * count neither way.
     */
    fun seen(f: IslandFront, log: List<Pair<String, Long>>, since: Long, self: String): Boolean? {
        if (f.pkg == self || f.to - f.from < MIN_MS || f.from - NEAR_MS < since) return null
        if (log.none { it.second in f.from - NEAR_MS..f.from + NEAR_MS }) return null
        val end = minOf(f.to, f.from + LOOK_MS) + 1_000
        return log.any { it.first == f.pkg && it.second in f.from - EARLY_MS..end }
    }

    /** Main's running verdict. [recent]: the last judged visits, oldest first ('1' seen, '0' missed). */
    data class Sight(val recent: String = "", val covers: Boolean = false)

    /** Takes over after [CONFIRM] seen in a row with no miss in the window; hands back at [MAX_MISSES] misses in it.
     *  Once handed back, every miss has to leave the window before it takes over again. */
    fun update(s: Sight, results: List<Boolean>): Sight {
        if (results.isEmpty()) return s
        val r = (s.recent + results.joinToString("") { if (it) "1" else "0" }).takeLast(WINDOW)
        val misses = r.count { it == '0' }
        val covers = if (s.covers) misses < MAX_MISSES else misses == 0 && r.endsWith("1".repeat(CONFIRM))
        return Sight(r, covers)
    }

    /** Main tells the Island copy it covers Island: its own accessibility service runs and has been seeing Island apps. */
    fun mainCovers(linked: Boolean, serviceUp: Boolean, s: Sight) = linked && serviceUp && s.covers

    /** Island copy: it needs its foreground service (and counts and blocks itself) unless this check-in got through
     *  and main said it covers Island. Main unreachable, refused, or not covering: the old way. */
    fun helperNeedsService(syncOk: Boolean, mainCovers: Boolean) = !(syncOk && mainCovers)

    /** Island copy: it counts and blocks only while main doesn't cover Island (else main counts it: never both). */
    fun helperCounts(mainCovers: Boolean) = !mainCovers

    /** Main, an Island browser in front that the Island copy counts: the site main's history keeps with it. Only from
     *  [live], its address bar read just now (null: main can't read that window) - never the address main kept for
     *  the package, which may be the personal copy's. A search phrase or a private tab: none. */
    fun islandSite(pkg: String, live: String?, privateTab: Boolean): String =
        if (privateTab) "" else Browsers.hostOf(pkg, live) ?: ""

    /** Main's quiet-helper alert: how much screen-on time without a check-in means it stopped. */
    fun quietMs(mainCovers: Boolean) = if (mainCovers) HelperWatch.COVERED_QUIET_ON_MS else HelperWatch.QUIET_ON_MS

    /** Main: the Island copy counts as "checked in lately" within this long (it only wakes every 15 min when main covers). */
    fun seenWithinMs(mainCovers: Boolean) = if (mainCovers) COVERED_SEEN_MS else 10 * 60_000L

    /** Main: has the Island copy checked in lately, [ageMs] after its last check-in (negative: a clock set back, not a
     *  fresh check-in)? [toldCovers]: main told it at that check-in that it covers Island, so it sleeps until its next
     *  15-min wake and only then hears that main stopped covering: the hour holds until it can have heard (main
     *  stopping to cover, or main's process just starting with its service not yet up, doesn't cut it to 10 min).
     *  [coversNow]: main covers Island now. The one rule for the Island cards and the "not covered" notice. */
    fun checkedInLately(ageMs: Long, toldCovers: Boolean, coversNow: Boolean) =
        ageMs in 0 until seenWithinMs(toldCovers || coversNow)

    /** Main's 15-minute "Island apps not covered" notice: up only after [NOTICE_AFTER] failed checks in a row (one
     *  late check-in - Doze letting the worker run just before Lockdown in Island - is no reason), down as soon as a
     *  check passes. A check with the screen off counts neither way: Doze holds Lockdown in Island back then too. */
    const val NOTICE_AFTER = 2

    enum class Notice { NONE, POST, CANCEL }

    /** One check: [fails] failed checks in a row so far, [bad] Island isn't covered now. The new count, and what to do. */
    fun noticeStep(fails: Int, bad: Boolean, screenOn: Boolean): Pair<Int, Notice> = when {
        !bad -> 0 to Notice.CANCEL
        !screenOn -> fails to Notice.NONE
        fails + 1 >= NOTICE_AFTER -> fails + 1 to Notice.POST
        else -> fails + 1 to Notice.NONE
    }

    /** Home's "Island apps not covered" banner: up exactly while the notice is - [fails] (as [noticeStep] left it)
     *  reached [NOTICE_AFTER] and Island is still [bad] now (it goes the moment a check-in brings Island back). */
    fun alertUp(fails: Int, bad: Boolean): Boolean = bad && fails >= NOTICE_AFTER

    /** Main's record of which apps its accessibility service heard from and when: one entry per app per second at
     *  most, the last [KEEP_MS], at most [MAX_LOG]. Thread-safe (the service writes, the link server reads). */
    class Log(private val keepMs: Long = KEEP_MS, private val max: Int = MAX_LOG) {
        private val entries = ArrayDeque<Pair<String, Long>>()
        private val last = HashMap<String, Long>()
        private var since = Long.MAX_VALUE      // nothing is known before this: the first entry, or the last one dropped

        @Synchronized fun add(pkg: String, ms: Long) {
            val l = last[pkg]
            if (l != null && ms - l in 0 until THROTTLE_MS) return
            last[pkg] = ms
            if (since == Long.MAX_VALUE) since = ms
            entries.addLast(pkg to ms)
            while (entries.isNotEmpty() && (entries.size > max || ms - entries.first().second > keepMs)) {
                val (p, t) = entries.removeFirst()
                if (last[p] == t) last.remove(p)
                since = maxOf(since, t)
            }
        }

        /** What it heard, and from when on that is complete. */
        @Synchronized fun snapshot(): Pair<List<Pair<String, Long>>, Long> = entries.toList() to since

        @Synchronized fun clear() { entries.clear(); last.clear(); since = Long.MAX_VALUE }
    }
}
