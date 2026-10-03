package com.husarp.lockdown.link

import com.husarp.lockdown.data.Config
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.IOException

/**
 * Every message on the link is one [Frame]: a 4-byte length, then UTF-8 JSON. One request and one response per
 * connection. Main says hello with its nonce nM; the helper answers with its nonce nH and either a pairing proof
 * or a signed sync; main answers with a signed state. The MAC covers the direction, both nonces and the exact body
 * bytes, so a recorded message can't be replayed (fresh nonces) or reflected (direction).
 */
@Serializable
data class Frame(
    val t: String,                       // hello | pair | paired | sync | state | denied
    val v: Int = 0,
    val n: String? = null,               // the sender's nonce (base64)
    val proof: String? = null,           // pairing proof
    val body: String? = null,            // JSON of SyncBody / StateBody, signed as is
    val mac: String? = null,
)

/** One usage batch the helper sends until main acks it. */
@Serializable
data class Batch(val seq: Long, val deltas: Map<String, Int>)

@Serializable
data class IslandApp(val pkg: String, val label: String)

/** Helper -> main. */
@Serializable
data class SyncBody(
    val seq: Long = 0,                                  // 0 = no batch
    val deltas: Map<String, Int> = emptyMap(),          // "owner\u0000bucket" -> seconds / count
    val cfg: String = "",                               // hash of the config last applied (or last unreadable)
    val modes: String = "",                             // hash of the modes last applied
    val front: String? = null,                          // the Island app in front, or null
    val app: Long = 0,                                  // the helper's versionCode
    val cfgError: Boolean = false,                      // the helper couldn't read the last config
    val apps: List<IslandApp>? = null,                  // Island's launchable apps, only when they changed
    val canBlock: Boolean = true,                       // the helper can see and block Island apps (permissions)
    val bye: Boolean = false,                           // the helper took the unlink: main may drop the link
)

/** Main -> helper. */
@Serializable
data class StateBody(
    val ack: Long = 0,                                  // every batch up to this seq is applied
    val usage: Map<String, Int> = emptyMap(),           // main's counters for the running periods
    val cfg: String? = null,                            // the config JSON, only when the helper's hash differs
    val cfgHash: String = "",
    val modes: String? = null,
    val modesHash: String = "",
    val unlink: Boolean = false,
    val app: Long = 0,
)

class FrameTooBig(len: Int) : IOException("frame of $len bytes")

object LinkProtocol {
    const val V = 1
    const val MAIN_MAX = 512 * 1024                     // what main reads: a sync is small
    const val HELPER_MAX = 32 * 1024 * 1024             // what the helper reads: a big config is several MB
    const val MAX_DELTA = 1_000_000
    val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    fun write(out: DataOutputStream, f: Frame) {
        val b = json.encodeToString(f).toByteArray(Charsets.UTF_8)
        out.writeInt(b.size); out.write(b); out.flush()
    }

    /** Reads one frame; a length over [max] is refused before anything is allocated (any app can connect). */
    fun read(inp: DataInputStream, max: Int): Frame {
        val len = inp.readInt()
        if (len < 0 || len > max) throw FrameTooBig(len)
        val b = ByteArray(len)
        inp.readFully(b)
        return json.decodeFromString(String(b, Charsets.UTF_8))
    }

    // ---------- pairing ----------

    fun pairProofH(kp: ByteArray, nM: ByteArray, nH: ByteArray) = LinkCrypto.hmac(kp, "pair-h".toByteArray(), nM, nH)
    fun pairProofM(s: ByteArray, nM: ByteArray, nH: ByteArray) = LinkCrypto.hmac(s, "pair-m".toByteArray(), nH, nM)

    // ---------- signed messages ----------

    const val H2M = "h2m"
    const val M2H = "m2h"

    fun mac(s: ByteArray, dir: String, nM: ByteArray, nH: ByteArray, body: String): ByteArray =
        LinkCrypto.hmac(s, dir.toByteArray(), nM, nH, body.toByteArray(Charsets.UTF_8))

    fun signed(t: String, s: ByteArray, dir: String, nM: ByteArray, nH: ByteArray, body: String): Frame =
        Frame(t, V, n = if (dir == H2M) LinkCrypto.b64(nH) else null, body = body, mac = LinkCrypto.b64(mac(s, dir, nM, nH, body)))

    fun verify(f: Frame, s: ByteArray, dir: String, nM: ByteArray, nH: ByteArray): Boolean {
        val body = f.body ?: return false
        return LinkCrypto.same(LinkCrypto.unb64(f.mac), mac(s, dir, nM, nH, body))
    }

    /** The helper's nonce from a request, or null if it's missing or the wrong size, or the version differs. */
    fun helperNonce(f: Frame): ByteArray? =
        if (f.v != V) null else LinkCrypto.unb64(f.n)?.takeIf { it.size == LinkCrypto.NONCE_LEN }

    // ---------- usage ----------

    /** A batch is applied once: a re-sent batch (its ack was lost) has a seq main already has. */
    fun shouldApply(seq: Long, lastSeq: Long) = seq > 0 && seq > lastSeq

    private val KINDS = setOf("day", "week", "month", "sw", "op", "win")

    /** Only additions to known counters: a negative delta would hand time back, so it is dropped. */
    fun cleanDeltas(d: Map<String, Int>): Map<String, Int> = d.filter { (k, v) ->
        val owner = k.substringBefore('\u0000', "")
        val kind = k.substringAfter('\u0000', "").substringBefore(':', "")
        v > 0 && (owner.startsWith("item:") || owner.startsWith("group:")) && kind in KINDS
    }.mapValues { it.value.coerceAtMost(MAX_DELTA) }
}

/** Main's rules applied in the helper. Only the helper's own site-filter switch stays its own (the VPN is per copy). */
fun applyConfig(remote: Config, local: Config): Config =
    remote.copy(settings = remote.settings.copy(siteFilterOn = local.settings.siteFilterOn))

/** Main's pairing window: open 3 minutes, single use, closed after 5 wrong codes. */
class PairWindow(val code: String, private val opened: Long) {
    var fails = 0; private set
    var used = false; private set

    fun open(now: Long) = !used && fails < MAX_FAILS && now - opened in 0 until TTL_MS
    fun fail() { fails++ }
    fun succeed() { used = true }
    fun until() = opened + TTL_MS

    companion object {
        const val TTL_MS = 3 * 60_000L
        const val MAX_FAILS = 5
    }
}
