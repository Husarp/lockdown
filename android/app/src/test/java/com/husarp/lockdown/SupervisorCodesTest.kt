package com.husarp.lockdown

import com.husarp.lockdown.engine.SupervisorCodes as Codes
import com.husarp.lockdown.guard.SupervisorKey
import com.husarp.lockdown.guard.SupervisorState
import com.husarp.lockdown.guard.SupervisorStore
import com.husarp.lockdown.guard.SupervisorStore.Approval
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.IOException
import java.util.concurrent.Callable
import java.util.concurrent.Executors

class SupervisorCodesTest {
    private val secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    private val pairing = Codes.Pairing(secret, 40)
    private var disk = SupervisorState(active = SupervisorKey(secret, 40))
    private var elapsed = 0L
    private var failWrite = false

    private fun store() = SupervisorStore({ disk }, {
        if (failWrite) throw IOException("test disk failure")
        disk = it
    }, { elapsed })

    @Test fun shared_pc_vectors_and_boundaries() {
        val vectors = listOf(
            Triple(30, 1234567890L, "005924"), Triple(30, 1111111109L, "081804"),
            Triple(40, 1440L, "003784"), Triple(40, 79L, "287082"), Triple(40, 80L, "359152"),
        )
        for ((period, now, code) in vectors) {
            val key = Codes.Pairing(secret, period)
            val counter = now / period
            assertEquals(code, Codes.display(key, now).code)
            assertEquals(counter, Codes.match(key, code, now))
            assertEquals(counter, Codes.match(key, code, (counter + 1) * period - 1))
            assertEquals(counter, Codes.match(key, code, (counter + 1) * period))
            assertNull(Codes.match(key, code, counter * period - 1))
            assertNull(Codes.match(key, code, (counter + 2) * period))
            assertNull(Codes.match(key, " $code", now))
        }
        assertEquals(Codes.Display("287082", 1, 41), Codes.display(pairing, 79))
        assertEquals(Codes.Display("359152", 11, 41), Codes.display(Codes.Pairing(secret, 30), 79))
    }

    @Test fun import_keeps_uri_period_and_validates_parameters() {
        assertEquals(pairing, Codes.parse(Codes.uri(pairing, "PC & Phone"), 30))
        assertEquals(pairing, Codes.parse(secret))
        assertEquals(30, Codes.parse(secret, 30).period)
        val legacy = "otpauth://totp/Lockdown:PC?secret=$secret"
        assertEquals(30, Codes.parse(legacy).period)
        for (query in listOf("period=40&period=30", "period=04", "period=%ZZ", "issuer", "issuer=%FF",
            "issuer=%00", "issuer=", "counter=1", "secret=other", "period=40#fragment")) {
            assertThrows(IllegalArgumentException::class.java) { Codes.parse("$legacy&$query") }
        }
    }

    @Test fun adjacent_collision_consumes_newest_and_blocks_replay() {
        val step = 910738L
        assertEquals("911617", Codes.display(pairing, (step - 1) * 40).code)
        assertEquals("911617", Codes.display(pairing, step * 40).code)
        assertEquals(step, Codes.match(pairing, "911617", step * 40))
        assertNull(Codes.match(pairing, "911617", step * 40, step - 1))
    }

    @Test fun setup_is_one_shot_and_old_key_stays_live_until_confirmation() {
        val store = store()
        val first = store.begin()
        val firstCode = Codes.display(first.pairing, 1440).code
        val cancelled = store.verifySetup(first, firstCode, 1440)
        store.cancel(first)
        assertThrows(IllegalArgumentException::class.java) { store.confirm(cancelled) }
        assertEquals(secret, disk.active!!.secret)
        val second = store.begin()
        val secondCode = Codes.display(second.pairing, 1440).code
        val verified = store.verifySetup(second, secondCode, 1440)
        failWrite = true
        assertThrows(IOException::class.java) { store.confirm(verified) }
        assertEquals(secret, disk.active!!.secret)
        failWrite = false
        store.confirm(verified)
        assertEquals(second.pairing.secret, disk.active!!.secret)
        assertEquals(Approval.REFUSED, store().consume(secondCode, 1480))
        assertThrows(IllegalArgumentException::class.java) { store.confirm(verified) }
        store.remove()
        assertNull(store.activePeriod())
    }

    @Test fun replacement_and_expiry_invalidate_previous_setup() {
        val store = store()
        val first = store.begin()
        val firstCode = Codes.display(first.pairing, 1440).code
        val verified = store.verifySetup(first, firstCode, 1440)
        val second = store.begin()
        store.cancel(first)
        assertThrows(IllegalArgumentException::class.java) { store.confirm(verified) }
        val secondCode = Codes.display(second.pairing, 1440).code
        val replacement = store.verifySetup(second, secondCode, 1440)
        elapsed = 600_001
        assertThrows(IllegalArgumentException::class.java) { store.confirm(replacement) }
        assertEquals(secret, disk.active!!.secret)
    }

    @Test fun racing_approvals_commit_once_and_survive_restart() {
        val store = store()
        val pool = Executors.newFixedThreadPool(2)
        try {
            val results = pool.invokeAll(List(2) { Callable { store.consume("003784", 1440) } }).map { it.get() }
            assertEquals(1, results.count { it == Approval.ACCEPTED })
            assertEquals(1, results.count { it == Approval.REFUSED })
        } finally {
            pool.shutdown()
        }
        assertEquals(Approval.REFUSED, store().consume("003784", 1480))
    }

    @Test fun failed_persistence_never_returns_approval_and_slowdown_survives_recreation() {
        val store = store()
        failWrite = true
        assertThrows(IOException::class.java) { store.consume("003784", 1440) }
        assertEquals(-1L, disk.last)
        failWrite = false
        repeat(3) { assertEquals(Approval.REFUSED, store.consume("000000", 1440)) }
        assertEquals(Approval.WAIT, store().consume("003784", 1440))
        assertEquals(Approval.ACCEPTED, store().consume("003784", 1441))
    }

    @Test fun own_and_equivalent_keys_are_filtered_and_other_keys_can_be_edited() {
        val store = store()
        val setup = store.begin()
        assertThrows(IllegalArgumentException::class.java) { store.add("Pending", setup.pairing.secret, 40) }
        assertThrows(IllegalArgumentException::class.java) { store.add("Own with zero byte", secret + "AA", 30) }
        val external = Codes.newPairing()
        assertNotEquals(secret, external.secret)
        store.add("External", Codes.uri(external, "PC"), 30)
        store.add("Legacy", external.secret, 30)
        val id = store.rows(79).first().id
        store.rename(id, "Other PC")
        assertEquals(listOf("Other PC", "Legacy"), store.rows(79).map { it.name })
        assertEquals(listOf(40, 30), store.rows(79).map { it.period })
        store.delete(id)
        assertEquals(1, store.rows(79).size)
    }
}
