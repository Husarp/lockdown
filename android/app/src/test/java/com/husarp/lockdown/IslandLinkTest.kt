package com.husarp.lockdown

import com.husarp.lockdown.data.Config
import com.husarp.lockdown.data.Settings
import com.husarp.lockdown.engine.Active
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.LimitClock
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.Usage
import com.husarp.lockdown.engine.UsageCounter
import com.husarp.lockdown.link.Batch
import com.husarp.lockdown.link.Foreground
import com.husarp.lockdown.link.Frame
import com.husarp.lockdown.link.FrameTooBig
import com.husarp.lockdown.link.LinkCrypto
import com.husarp.lockdown.link.LinkProtocol
import com.husarp.lockdown.link.LinkUsage
import com.husarp.lockdown.link.PairWindow
import com.husarp.lockdown.link.applyConfig
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.time.LocalDateTime
import java.time.LocalTime

class IslandLinkTest {
    private val t0 = LocalDateTime.of(2026, 9, 28, 12, 0, 0)   // Monday noon
    private val nM = ByteArray(16) { 1 }
    private val nH = ByteArray(16) { 2 }
    private fun k(owner: String, bucket: String) = "$owner\u0000$bucket"

    // ---------- code ----------

    @Test fun a_new_code_is_8_characters_from_the_alphabet() {
        repeat(50) {
            val c = LinkCrypto.newCode()
            assertEquals(8, c.length)
            assertTrue(c.all { it in LinkCrypto.ALPHABET })
        }
    }

    @Test fun a_typed_code_is_normalised() {
        assertEquals("K7QM2XPA", LinkCrypto.normalize("k7qm-2xpa "))
        assertEquals("0", LinkCrypto.normalize("O"))
        assertEquals("11", LinkCrypto.normalize("IL"))
        assertEquals("K7QM-2XPA", LinkCrypto.display("K7QM2XPA"))
    }

    // ---------- crypto ----------

    @Test fun both_sides_derive_the_same_secret() {
        val main = LinkCrypto.secret(LinkCrypto.pairKey("K7QM2XPA"), nM, nH)
        val helper = LinkCrypto.secret(LinkCrypto.pairKey("k7qm-2xpa"), nM, nH)   // as typed
        assertArrayEquals(main, helper)
        assertFalse(main.contentEquals(LinkCrypto.secret(LinkCrypto.pairKey("K7QM2XPB"), nM, nH)))
        assertFalse(main.contentEquals(LinkCrypto.secret(LinkCrypto.pairKey("K7QM2XPA"), ByteArray(16) { 9 }, nH)))
        assertFalse(main.contentEquals(LinkCrypto.secret(LinkCrypto.pairKey("K7QM2XPA"), nM, ByteArray(16) { 9 })))
    }

    @Test fun the_pairing_proof_verifies_only_with_the_right_code() {
        val proof = LinkProtocol.pairProofH(LinkCrypto.pairKey("K7QM-2XPA"), nM, nH)
        assertTrue(LinkCrypto.same(proof, LinkProtocol.pairProofH(LinkCrypto.pairKey("K7QM2XPA"), nM, nH)))
        assertFalse(LinkCrypto.same(proof, LinkProtocol.pairProofH(LinkCrypto.pairKey("K7QM2XPB"), nM, nH)))
        // main's answer is a different message (no reflection of the helper's proof)
        val s = LinkCrypto.secret(LinkCrypto.pairKey("K7QM2XPA"), nM, nH)
        assertFalse(LinkCrypto.same(proof, LinkProtocol.pairProofM(s, nM, nH)))
    }

    @Test fun compare_handles_missing_values() {
        assertFalse(LinkCrypto.same(null, byteArrayOf(1)))
        assertFalse(LinkCrypto.same(byteArrayOf(1), byteArrayOf(1, 2)))
        assertTrue(LinkCrypto.same(byteArrayOf(1, 2), byteArrayOf(1, 2)))
        assertNull(LinkCrypto.unb64("not base64!"))
    }

    // ---------- protocol ----------

    private val s = LinkCrypto.secret(LinkCrypto.pairKey("K7QM2XPA"), nM, nH)

    @Test fun a_signed_request_verifies() {
        val f = LinkProtocol.signed("sync", s, LinkProtocol.H2M, nM, nH, """{"seq":1}""")
        assertTrue(LinkProtocol.verify(f, s, LinkProtocol.H2M, nM, nH))
        assertArrayEquals(nH, LinkCrypto.unb64(f.n))
    }

    @Test fun one_flipped_body_byte_fails() {
        val f = LinkProtocol.signed("sync", s, LinkProtocol.H2M, nM, nH, """{"seq":1}""")
        assertFalse(LinkProtocol.verify(f.copy(body = """{"seq":2}"""), s, LinkProtocol.H2M, nM, nH))
        assertFalse(LinkProtocol.verify(f.copy(mac = null), s, LinkProtocol.H2M, nM, nH))
    }

    @Test fun a_replayed_request_fails_against_a_new_hello() {
        val f = LinkProtocol.signed("sync", s, LinkProtocol.H2M, nM, nH, """{"seq":1}""")
        assertFalse(LinkProtocol.verify(f, s, LinkProtocol.H2M, ByteArray(16) { 7 }, nH))
    }

    @Test fun main_s_answer_is_not_accepted_as_a_request() {
        val answer = LinkProtocol.signed("state", s, LinkProtocol.M2H, nM, nH, """{"ack":1}""")
        assertTrue(LinkProtocol.verify(answer, s, LinkProtocol.M2H, nM, nH))
        assertFalse(LinkProtocol.verify(answer, s, LinkProtocol.H2M, nM, nH))
    }

    @Test fun a_replayed_answer_fails_against_a_new_request() {
        val answer = LinkProtocol.signed("state", s, LinkProtocol.M2H, nM, nH, """{"ack":1}""")
        assertFalse(LinkProtocol.verify(answer, s, LinkProtocol.M2H, nM, ByteArray(16) { 7 }))
    }

    @Test fun a_wrong_secret_fails() {
        val f = LinkProtocol.signed("sync", s, LinkProtocol.H2M, nM, nH, "{}")
        assertFalse(LinkProtocol.verify(f, ByteArray(32), LinkProtocol.H2M, nM, nH))
    }

    @Test fun frames_round_trip() {
        val buf = ByteArrayOutputStream()
        val f = Frame("hello", 1, n = LinkCrypto.b64(nM))
        LinkProtocol.write(DataOutputStream(buf), f)
        assertEquals(f, LinkProtocol.read(DataInputStream(ByteArrayInputStream(buf.toByteArray())), LinkProtocol.MAIN_MAX))
    }

    @Test fun an_oversized_frame_is_refused_before_reading_it() {
        val header = byteArrayOf(0x7f, 0xff.toByte(), 0xff.toByte(), 0xff.toByte())   // claims 2 GB, sends nothing
        try {
            LinkProtocol.read(DataInputStream(ByteArrayInputStream(header)), LinkProtocol.MAIN_MAX)
            fail("should refuse")
        } catch (_: FrameTooBig) { }
        val negative = byteArrayOf(0xff.toByte(), 0, 0, 0)
        try {
            LinkProtocol.read(DataInputStream(ByteArrayInputStream(negative)), LinkProtocol.MAIN_MAX)
            fail("should refuse")
        } catch (_: FrameTooBig) { }
    }

    @Test fun another_version_or_a_bad_nonce_is_refused() {
        assertNotNull(LinkProtocol.helperNonce(Frame("sync", 1, n = LinkCrypto.b64(nH))))
        assertNull(LinkProtocol.helperNonce(Frame("sync", 2, n = LinkCrypto.b64(nH))))
        assertNull(LinkProtocol.helperNonce(Frame("sync", 1, n = LinkCrypto.b64(ByteArray(8)))))
        assertNull(LinkProtocol.helperNonce(Frame("sync", 1)))
    }

    // ---------- usage ----------

    @Test fun a_batch_is_applied_once() {
        assertTrue(LinkProtocol.shouldApply(5, 4))
        assertFalse(LinkProtocol.shouldApply(4, 4))
        assertFalse(LinkProtocol.shouldApply(3, 4))
        assertFalse(LinkProtocol.shouldApply(0, 0))
        assertFalse(LinkProtocol.shouldApply(0, 4))
    }

    @Test fun deltas_only_add_to_known_counters() {
        val d = LinkProtocol.cleanDeltas(mapOf(
            k("group:g1", "week:w2026-09-28") to 60,
            k("item:1", "day:2026-09-28") to -500,              // would hand time back
            k("item:1", "sw:2026-09-28") to 0,
            k("user:1", "day:2026-09-28") to 60,                // unknown owner
            k("item:1", "year:2026") to 60,                     // unknown bucket kind
            "item:1" to 60,                                     // no bucket
            k("item:2", "op:i2SWITCH_LIMIT:2026-09-28") to 5_000_000,
        ))
        assertEquals(mapOf(k("group:g1", "week:w2026-09-28") to 60, k("item:2", "op:i2SWITCH_LIMIT:2026-09-28") to LinkProtocol.MAX_DELTA), d)
    }

    @Test fun main_sends_only_the_running_periods() {
        val c = mapOf(
            k("item:1", "day:2026-09-28") to 1, k("item:1", "day:2026-09-27") to 2,
            k("group:g1", "week:w2026-09-28") to 3, k("group:g1", "week:w2026-09-21") to 4,
            k("item:1", "month:m2026-09") to 5, k("item:1", "month:m2026-08") to 6,
            k("item:1", "sw:2026-09-28") to 7, k("item:1", "sw:2026-09-27") to 8,
            k("item:1", "op:i1SWITCH_LIMIT:2026-09-28") to 9, k("item:1", "op:i1SWITCH_LIMIT:2026-09-27") to 10,
            k("group:g1", "win:gSCHEDULED:2026-09-28T18:00") to 11, k("group:g1", "win:gSCHEDULED:2026-09-28T11:00") to 12,
        )
        assertEquals(setOf(1, 3, 5, 7, 9, 11), LinkUsage.current(c, t0, LimitClock.DEFAULT).values.toSet())
    }

    @Test fun a_custom_reset_day_key_is_kept() {
        val clock = LimitClock(resetTime = LocalTime.of(3, 0))
        val c = mapOf(
            k("item:1", "day:2026-09-28T03:00") to 1, k("item:1", "day:2026-09-27T03:00") to 2,
            k("item:1", "op:i1SWITCH_LIMIT:2026-09-28T03:00") to 3, k("item:1", "op:i1SWITCH_LIMIT:2026-09-27T03:00") to 4,
        )
        assertEquals(setOf(1, 3), LinkUsage.current(c, t0, clock).values.toSet())
    }

    // Both profiles: YouTube in a group with 2 minutes a day.
    private val yt = Item("1", "YouTube", "com.google.android.youtube", ItemType.APP)
    private val fun2 = Group("g1", "Fun", rules = listOf(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 2)), memberIds = listOf("1"))
    private fun use(c: UsageCounter, from: LocalDateTime, sec: Long) {
        val a = listOf(Active(yt, Rules.countedRules(yt, listOf(fun2))))
        c.record(a, from); c.record(a, from.plusSeconds(sec)); c.record(emptyList(), from.plusSeconds(sec))
    }
    private fun blocked(u: Usage, now: LocalDateTime) = Rules.itemBlock(Rules.effectiveRules(yt, listOf(fun2)), now, u) != null

    /** A helper: its own counter (pending), plus main's snapshot and the batch out. */
    private class Helper {
        val c = UsageCounter()
        var base: Map<String, Int> = emptyMap()
        var inflight: Batch? = null
        var nextSeq = 1L
        val usage: Usage = { o, b -> c.usage(o, b) + LinkUsage.extra(base, inflight, o, b) }
        fun form(): Batch? { val (b, n) = LinkUsage.formBatch(c.counters, inflight, nextSeq); inflight = b; nextSeq = n; return b }
    }

    /** Main's side of a sync: apply once, ack, snapshot. */
    private class Main {
        val c = UsageCounter()
        var lastSeq = 0L
        fun sync(b: Batch?, now: LocalDateTime): Pair<Long, Map<String, Int>> {
            if (b != null && LinkProtocol.shouldApply(b.seq, lastSeq)) {
                for ((k, v) in LinkProtocol.cleanDeltas(b.deltas)) c.counters[k] = (c.counters[k] ?: 0) + v
                lastSeq = b.seq
            }
            return lastSeq to LinkUsage.current(c.counters, now, LimitClock.DEFAULT)
        }
    }

    private fun Helper.take(r: Pair<Long, Map<String, Int>>) { inflight = LinkUsage.acked(inflight, r.first); base = r.second }

    @Test fun a_limit_is_shared_across_the_profiles() {
        val main = Main(); val helper = Helper()
        use(main.c, t0, 60)                                     // 1 min in the main profile
        helper.take(main.sync(helper.form(), t0.plusMinutes(1)))
        assertFalse(blocked(helper.usage, t0.plusMinutes(1)))
        use(helper.c, t0.plusMinutes(2), 60)                     // 1 min in Island
        val t = t0.plusMinutes(3)
        assertTrue(blocked(helper.usage, t))                      // before the sync: main's minute + its own
        assertFalse(blocked(main.c.usage, t))                     // main doesn't know yet
        helper.take(main.sync(helper.form(), t))
        assertTrue(blocked(main.c.usage, t))                      // both stop at 2 min
        assertTrue(blocked(helper.usage, t))
        assertEquals(120, helper.usage("group:g1", "day:2026-09-28"))
        assertEquals(120, main.c.usage("group:g1", "day:2026-09-28"))
    }

    @Test fun a_lost_ack_does_not_count_twice() {
        val main = Main(); val helper = Helper()
        use(helper.c, t0, 60)
        val b = helper.form()!!
        assertEquals(1L, b.seq)
        assertTrue(helper.c.counters.isEmpty())                   // pending moved into the batch
        assertEquals(60, helper.usage("item:1", "day:2026-09-28"))  // still counted while it's out
        main.sync(b, t0)                                          // applied, the answer is lost
        use(helper.c, t0.plusMinutes(1), 30)                      // meanwhile, 30 s more
        assertEquals(b, helper.form())                            // the same batch goes again
        val r = main.sync(helper.inflight, t0.plusMinutes(2))
        assertEquals(60, main.c.usage("item:1", "day:2026-09-28"))  // not 120
        helper.take(r)
        assertNull(helper.inflight)
        assertEquals(90, helper.usage("item:1", "day:2026-09-28"))  // main's 60 + the 30 pending, no double
        val b2 = helper.form()!!
        assertEquals(2L, b2.seq)
        helper.take(main.sync(b2, t0.plusMinutes(3)))
        assertEquals(90, main.c.usage("item:1", "day:2026-09-28"))
        assertEquals(90, helper.usage("item:1", "day:2026-09-28"))
    }

    @Test fun offline_time_queues_up_and_arrives_later() {
        val main = Main(); val helper = Helper()
        use(helper.c, t0, 60)
        helper.form()                                             // main unreachable: the batch stays out
        use(helper.c, t0.plusMinutes(5), 60)                      // more time, still offline
        assertEquals(120, helper.usage("item:1", "day:2026-09-28"))
        helper.take(main.sync(helper.inflight, t0.plusMinutes(10)))   // back: the first batch
        helper.take(main.sync(helper.form(), t0.plusMinutes(10)))     // then the rest
        assertEquals(120, main.c.usage("item:1", "day:2026-09-28"))
        assertEquals(120, helper.usage("item:1", "day:2026-09-28"))
    }

    @Test fun no_batch_without_pending_time() {
        val pending = HashMap<String, Int>()
        assertEquals(null to 1L, LinkUsage.formBatch(pending, null, 1))
        val out = Batch(4, mapOf("a" to 1))
        pending["a"] = 2
        assertEquals(out to 5L, LinkUsage.formBatch(pending, out, 5))   // one batch at a time
        assertEquals(2, pending["a"])
        assertEquals(out, LinkUsage.acked(out, 3))
        assertNull(LinkUsage.acked(out, 4))
        assertNull(LinkUsage.acked(out, 9))
    }

    @Test fun unlinking_keeps_every_second() {
        val base = mapOf("a" to 100, "b" to 5)
        val inflight = Batch(3, mapOf("a" to 10, "c" to 1))
        val pending = mapOf("a" to 1, "d" to 2)
        assertEquals(mapOf("a" to 111, "b" to 5, "c" to 1, "d" to 2), LinkUsage.sum(base, inflight.deltas, pending))
        // main's reply already holds batch 3 (acked): it isn't added again
        val usage = mapOf("a" to 110, "b" to 5, "c" to 1)
        assertEquals(mapOf("a" to 111, "b" to 5, "c" to 1, "d" to 2), LinkUsage.sum(usage, LinkUsage.acked(inflight, 3)?.deltas, pending))
    }

    @Test fun a_new_pairing_folds_the_batch_back_into_pending() {
        val pending = hashMapOf("a" to 5)
        LinkUsage.foldBack(pending, Batch(7, mapOf("a" to 10, "b" to 1)))
        assertEquals(mapOf("a" to 15, "b" to 1), pending)
        assertEquals(Batch(1, mapOf("a" to 15, "b" to 1)) to 2L, LinkUsage.formBatch(pending, null, 1))
    }

    // ---------- config ----------

    @Test fun main_s_rules_replace_the_helper_s_but_its_vpn_switch_stays() {
        val remote = Config(items = listOf(yt), groups = listOf(fun2), settings = Settings(siteFilterOn = false, pauseBeforeOpen = true))
        val local = Config(items = listOf(Item("9", "Old", "old.app", ItemType.APP)), settings = Settings(siteFilterOn = true))
        val out = applyConfig(remote, local)
        assertEquals(listOf(yt), out.items)
        assertEquals(listOf(fun2), out.groups)
        assertTrue(out.settings.pauseBeforeOpen)
        assertTrue(out.settings.siteFilterOn)
    }

    // ---------- foreground ----------

    @Test fun the_app_in_front_follows_the_events() {
        val r = Foreground.RESUMED; val p = Foreground.PAUSED; val s = Foreground.STOPPED
        fun front(vararg e: Triple<Int, String, String>, from: List<String> = emptyList()) =
            Foreground.front(Foreground.reduce(from, e.toList()))
        assertEquals("A", front(Triple(r, "A", "X")))
        assertNull(front(Triple(r, "A", "X"), Triple(p, "A", "X")))
        assertEquals("B", front(Triple(r, "A", "X"), Triple(r, "B", "Y"), Triple(p, "A", "X")))
        assertNull(front(Triple(s, "B", "Y"), from = listOf("B/Y")))
        assertEquals("B", front(Triple(7, "C", "Z"), from = listOf("B/Y")))        // other events change nothing
    }

    @Test fun moving_inside_one_app_keeps_it_in_front() {
        val r = Foreground.RESUMED; val p = Foreground.PAUSED; val s = Foreground.STOPPED
        // splash -> main: the splash's late STOPPED doesn't clear the app
        val events = listOf(Triple(r, "A", "Splash"), Triple(p, "A", "Splash"), Triple(r, "A", "Main"), Triple(s, "A", "Splash"))
        assertEquals("A", Foreground.front(Foreground.reduce(emptyList(), events)))
        // split screen: B leaves, A is still resumed beside it
        val split = listOf(Triple(r, "A", "X"), Triple(r, "B", "Y"), Triple(p, "B", "Y"))
        assertEquals("A", Foreground.front(Foreground.reduce(emptyList(), split)))
    }

    // ---------- pairing window ----------

    @Test fun the_pairing_window_closes() {
        val w = PairWindow("K7QM2XPA", 1000)
        assertTrue(w.open(1000))
        assertTrue(w.open(1000 + PairWindow.TTL_MS - 1))
        assertFalse(w.open(1000 + PairWindow.TTL_MS))              // 3 minutes
        assertFalse(w.open(999))                                    // clock set back
        repeat(4) { w.fail() }
        assertTrue(w.open(2000))
        w.fail()
        assertFalse(w.open(2000))                                   // 5 wrong codes
        val once = PairWindow("K7QM2XPA", 1000)
        once.succeed()
        assertFalse(once.open(1500))                                // single use
    }
}
