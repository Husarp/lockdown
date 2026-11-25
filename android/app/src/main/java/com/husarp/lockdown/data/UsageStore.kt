package com.husarp.lockdown.data

import android.content.Context
import com.husarp.lockdown.block.Enforce
import com.husarp.lockdown.engine.Active
import com.husarp.lockdown.engine.BlockPause
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.QuotaContribution
import com.husarp.lockdown.engine.QuotaExemptions
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.Usage
import com.husarp.lockdown.engine.UsageCounter
import com.husarp.lockdown.link.Batch
import com.husarp.lockdown.link.IslandLink
import com.husarp.lockdown.link.LinkProtocol
import com.husarp.lockdown.link.LinkUsage
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import java.io.File
import java.time.LocalDateTime

/** The local counters and linked usage outbox. Mutations happen on the main looper. */
object UsageStore {
    @Serializable
    private data class Saved(
        val counters: Map<String, Int> = emptyMap(),
        val pending: List<QuotaContribution> = emptyList(),
        val inflight: Batch? = null,
        val nextSeq: Long = 1,
        val link: String = "",
        val appliedLink: String = "",
        val appliedSeq: Long = 0,
        val localCarry: Map<String, Long> = emptyMap(),
    )

    private lateinit var file: File
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private var lastFlush = 0L
    private var flushedChanges = -1L
    private var saved = Saved()
    private val pending = ArrayList<QuotaContribution>()
    private var pendingTotals: Map<String, Int>? = null
    private var cachedConfig: Config? = null
    private var cachedPauses: List<BlockPause>? = null
    private var cachedExemptions = QuotaExemptions()

    lateinit var counter: UsageCounter
        private set

    @Volatile var base: Map<String, Int> = emptyMap()
    val inflight get() = saved.inflight

    val usage: Usage = { owner, bucket ->
        val key = "$owner\u0000$bucket"
        counter.usage(owner, bucket) + (base[key] ?: 0) + (inflight?.deltas?.get(key) ?: 0) +
            if (IslandLink.isHelper) (totals()[key] ?: 0) else 0
    }

    fun init(ctx: Context) = init(File(ctx.filesDir, "usage.json"))

    internal fun init(usageFile: File) {
        file = usageFile
        saved = runCatching {
            val raw = file.readText()
            if ("counters" in json.parseToJsonElement(raw).jsonObject) json.decodeFromString<Saved>(raw)
            else Saved(counters = json.decodeFromString<Map<String, Int>>(raw))
        }.getOrDefault(Saved())
        counter = UsageCounter(saved.counters.toMutableMap(), carry = saved.localCarry.toMutableMap())
        pending.clear()
        pending.addAll(saved.pending)
        pendingTotals = null
        cachedConfig = null
        base = emptyMap()
        lastFlush = 0
        flushedChanges = -1
    }

    fun exemptions(cfg: Config = Store.config): QuotaExemptions {
        val pauses = Store.quotaPauses
        if (cfg !== cachedConfig || pauses !== cachedPauses) {
            cachedConfig = cfg
            cachedPauses = pauses
            cachedExemptions = QuotaExemptions(pauses + listOfNotNull(cfg.pause), cfg.allUses())
            pendingTotals = null
        }
        return cachedExemptions
    }

    private fun totals(): Map<String, Int> {
        val exemptions = exemptions()
        return pendingTotals ?: LinkUsage.tally(inflight?.contributions.orEmpty() + pending, exemptions)
            .also { pendingTotals = it }
    }

    /** Keep observations for openings even when the item is currently exempt or blocked. */
    fun record(actives: List<Active>, now: LocalDateTime = LocalDateTime.now(), cfg: Config = Store.config) {
        counter.clock = cfg.clock()
        val holds = cfg.resetCarry
        val resetEdges = (listOf(holds?.dayStart, holds?.switch) +
            holds?.holds.orEmpty().values.flatMap { listOf(it.from, it.until) })
            .filterNotNull().mapNotNull(QuotaExemptions::parse)
        val journal: ((QuotaContribution) -> Unit)? = if (IslandLink.isHelper) { entry ->
            LinkUsage.append(pending, entry)
            pendingTotals = null
            counter.touch()
        } else null
        counter.record(actives, now, exemptions(cfg), resetEdges, { item, at ->
            if (item.type == ItemType.APP) Enforce.counts(cfg, item, at)
            else !Enforce.itemBlocked(cfg, item, at)
        }, journal)
        maybeFlush()
    }

    fun active(item: Item, cfg: Config, launched: Boolean = false) =
        Active(item, Rules.countedRules(item, cfg.groups), launched,
            emergencyEligible = !Rules.permanent(Rules.effectiveRules(item, cfg.groups)))

    fun prune(now: LocalDateTime = LocalDateTime.now()) {
        counter.prune(now)
        flush()
    }

    /** Migrate the pre-journal outbox once. The hash identifies the pairing without storing its secret here. */
    fun restoreHelper(link: String, legacy: Batch?, legacyNext: Long) {
        if (saved.link == link) return
        saved = saved.copy(link = link, inflight = legacy, nextSeq = legacyNext)
        flush()
    }

    fun newHelper(link: String): Boolean {
        val batch = inflight
        val counts = LinkUsage.sum(counter.counters, batch?.deltas)
        val queued = batch?.contributions.orEmpty() + pending
        val next = snapshot().copy(link = link, inflight = null, nextSeq = 1, pending = queued, counters = counts)
        if (!persist(next)) return false
        replaceCounters(counts)
        pending.clear()
        pending.addAll(queued)
        pendingTotals = null
        return true
    }

    fun prepareBatch(): Boolean {
        if (inflight != null || (pending.isEmpty() && counter.counters.isEmpty())) return true
        val entries = pending.take(LinkUsage.BATCH_SAMPLES)
        val batch = Batch(saved.nextSeq, HashMap(counter.counters), entries)
        val next = snapshot().copy(counters = emptyMap(), pending = pending.drop(entries.size),
            inflight = batch, nextSeq = saved.nextSeq + 1)
        if (!persist(next)) return false
        counter.counters.clear()
        pending.subList(0, entries.size).clear()
        pendingTotals = null
        return true
    }

    fun acknowledge(ack: Long, usage: Map<String, Int>): Boolean {
        val next = snapshot().copy(inflight = LinkUsage.acked(inflight, ack), nextSeq = maxOf(saved.nextSeq, ack + 1))
        if ((next.inflight != saved.inflight || next.nextSeq != saved.nextSeq) && !persist(next)) return false
        base = usage
        pendingTotals = null
        return true
    }

    fun appliedSeq(link: String, legacySeq: Long): Long =
        maxOf(legacySeq, saved.appliedSeq.takeIf { saved.appliedLink == link } ?: 0)

    /** Persist the signed batch's counters, carry and sequence together before acknowledging. */
    fun applyIsland(link: String, batch: Batch, legacySeq: Long): Boolean {
        if (!LinkProtocol.shouldApply(batch.seq, appliedSeq(link, legacySeq))) return true
        val carry = counter.carry.toMutableMap()
        val delta = LinkUsage.tally(batch.contributions, exemptions(), carry)
        val counts = LinkUsage.sum(counter.counters, LinkProtocol.cleanDeltas(batch.deltas), delta)
        if (!persist(snapshot().copy(counters = counts, appliedLink = link, appliedSeq = batch.seq,
                localCarry = carry))) return false
        replaceCounters(counts)
        replaceCarry(carry)
        return true
    }

    fun finishHelper(ack: Long, usage: Map<String, Int>): Boolean {
        val batch = LinkUsage.acked(inflight, ack)
        val carry = counter.carry.toMutableMap()
        val paid = LinkUsage.tally(batch?.contributions.orEmpty() + pending, exemptions(), carry)
        val counts = LinkUsage.sum(usage, batch?.deltas, counter.counters, paid)
        if (!persist(snapshot().copy(link = "", inflight = null, nextSeq = 1, pending = emptyList(),
                counters = counts, localCarry = carry))) return false
        replaceCounters(counts)
        replaceCarry(carry)
        pending.clear()
        pendingTotals = null
        base = emptyMap()
        return true
    }

    private fun replaceCounters(counts: Map<String, Int>) {
        counter.counters.clear()
        counter.counters.putAll(counts)
        counter.touch()
    }

    private fun replaceCarry(carry: Map<String, Long>) {
        counter.carry.clear()
        counter.carry.putAll(carry)
    }

    private fun snapshot() = saved.copy(counters = HashMap(counter.counters), pending = pending.toList(),
        localCarry = HashMap(counter.carry))

    private fun maybeFlush() {
        if (System.currentTimeMillis() - lastFlush >= FLUSH_SEC * 1000L && counter.changes != flushedChanges) flush()
    }

    fun flush(): Boolean = persist(snapshot())

    private fun persist(next: Saved): Boolean = runCatching {
        synchronized(FileWrites.lock) { FileWrites.replace(listOf(file to json.encodeToString(next))) }
        saved = next
        lastFlush = System.currentTimeMillis()
        flushedChanges = counter.changes
        true
    }.getOrDefault(false)

    private const val FLUSH_SEC = 20L
}
