package com.husarp.lockdown.engine

import kotlinx.serialization.Serializable

/** One app visit in the Island profile, as the helper reports it to main for the Activity log: started at [at] and
 *  over at [end] (epoch ms), [sec] its foreground time in between. */
@Serializable
data class IslandOpen(val pkg: String, val label: String, val at: Long, val end: Long, val sec: Int)

/**
 * Island apps opened, for main's Activity log (main's usage events only see its own profile, even when its
 * accessibility service sees and blocks Island apps, so nothing here is listed twice). The helper groups its usage
 * events into visits the same way as the log ([ActivityLog.visits]) and sends only visits that are over and ended
 * after main's mark (the latest end main has, returned at every check-in: StateBody.opensMark), so each sync carries
 * just what is new. Visits end in order once over, so one that ends later is never behind the mark; one sent twice
 * (a lost answer) is kept once. Both fields are optional: an older helper sends none, an older main returns no mark -
 * and then the helper sends none. Pure.
 */
object IslandOpens {
    const val MAX_SEND = 500               // per sync: the latest (the log shows at most 400 per source anyway)
    const val MAX_KEEP = 1000              // kept in main
    const val KEEP_MS = (ActivityLog.DAYS + 1) * 86_400_000L
    private const val MAX_SEC = 86_400

    /** Helper: the visits in [spans] that are over by [now] (no use of that app for over [ActivityLog.APP_GAP_SEC],
     *  so a later span can't join them) and long enough to be listed, by end. [skip] leaves out Lockdown itself, the system
     *  bar and the home screen. */
    fun finished(spans: List<AppSpan>, now: Long, label: (String) -> String, skip: (String) -> Boolean = { false }): List<IslandOpen> =
        ActivityLog.visits(spans, skip)
            .filter { it.toMs < now - ActivityLog.APP_GAP_SEC * 1000 && it.ms / 1000 >= ActivityLog.MIN_VISIT_SEC && it.pkg.length <= 200 }
            .map { IslandOpen(it.pkg, label(it.pkg).take(80), it.fromMs, it.toMs, (it.ms / 1000).coerceAtMost(MAX_SEC.toLong()).toInt()) }
            .sortedBy { it.end }

    /** Helper: what goes out - the visits that ended after main's [mark], at most the latest [MAX_SEND], by end. None
     *  while the mark is unknown (an older main, or no answer yet since linking). */
    fun toSend(all: List<IslandOpen>, mark: Long?): List<IslandOpen> =
        if (mark == null) emptyList() else all.filter { it.end > mark }.sortedBy { it.end }.takeLast(MAX_SEND)

    /** Main: what it accepts from the link (any paired helper could send anything): a package, sane seconds, a visit
     *  inside the kept days and over by [now], not too many. */
    fun clean(rows: List<IslandOpen>, now: Long): List<IslandOpen> = rows.filter {
        it.pkg.isNotEmpty() && it.pkg.length <= 200 && it.sec in 1..MAX_SEC && it.at >= now - KEEP_MS && it.at <= it.end && it.end <= now
    }.map { it.copy(label = it.label.take(80)) }.take(MAX_SEND)

    /** Main: [old] and [incoming] together, one row per package and start (a re-sent visit replaces itself), the
     *  last [KEEP_MS] before [now], at most the latest [MAX_KEEP], by end. */
    fun merge(old: List<IslandOpen>, incoming: List<IslandOpen>, now: Long): List<IslandOpen> =
        (old + incoming).associateBy { it.pkg to it.at }.values
            .filter { it.at >= now - KEEP_MS }.sortedBy { it.end }.takeLast(MAX_KEEP)

    /** Main: the latest end it has, up to [now] (the helper sends only visits that ended after it; a clock set back
     *  doesn't hold it up); 0 when none. */
    fun mark(rows: List<IslandOpen>, now: Long): Long = rows.filter { it.end <= now }.maxOfOrNull { it.end } ?: 0L
}
