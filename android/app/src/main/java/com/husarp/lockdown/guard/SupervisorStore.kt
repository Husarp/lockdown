package com.husarp.lockdown.guard

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.os.SystemClock
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import com.husarp.lockdown.engine.SupervisorCodes as Codes
import java.io.File
import java.security.KeyStore
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

@Serializable
internal data class SupervisorKey(val secret: String, val period: Int = Codes.LEGACY_PERIOD) {
    fun pairing() = Codes.Pairing(secret, period)
}

@Serializable
internal data class SupervisorState(
    val active: SupervisorKey? = null,
    val last: Long = -1,
    val fails: Int = 0,
    val next: Long = 0,
    val entries: List<SupervisedKey> = emptyList(),
)

@Serializable
internal data class SupervisedKey(val id: String, val name: String, val key: SupervisorKey)

internal class SupervisorStore(
    private val load: () -> SupervisorState,
    private val save: (SupervisorState) -> Unit,
    private val elapsed: () -> Long,
) {
    class Setup internal constructor(val pairing: Codes.Pairing, internal val replaces: SupervisorKey?, internal val started: Long)
    class Confirmation internal constructor(internal val setup: Setup, internal val counter: Long)
    data class Row(val id: String, val name: String, val period: Int, val display: Codes.Display)
    enum class Approval { ACCEPTED, REFUSED, WAIT }

    private var pending: Setup? = null
    private var cached: SupervisorState? = null
    private fun state() = cached ?: load().also { cached = it }

    private fun commit(state: SupervisorState) {
        cached = null
        save(state)
        cached = state
    }

    @Synchronized
    fun activePeriod(): Int? = state().active?.period

    @Synchronized
    fun begin(): Setup = Setup(Codes.newPairing(), state().active, elapsed()).also { pending = it }

    @Synchronized
    fun cancel(setup: Setup) {
        if (pending === setup) pending = null
    }

    private fun liveSetup() = pending?.takeIf { elapsed() - it.started <= 600_000 }

    @Synchronized
    fun verifySetup(setup: Setup, code: String, now: Long): Confirmation {
        require(liveSetup() === setup) { "Start the supervisor setup again." }
        val counter = Codes.match(setup.pairing, code, now)
        require(counter != null) { "Use the current 6-digit code from your supervisor." }
        return Confirmation(setup, counter)
    }

    @Synchronized
    fun confirm(confirmation: Confirmation) {
        val setup = confirmation.setup
        val old = state()
        require(liveSetup() === setup && old.active == setup.replaces) { "Start the supervisor setup again." }
        val key = SupervisorKey(setup.pairing.secret, setup.pairing.period)
        commit(old.copy(active = key, last = confirmation.counter))
        pending = null
    }

    @Synchronized
    fun remove() {
        val old = state()
        if (old.active != null) commit(old.copy(active = null, last = -1))
        pending = null
    }

    @Synchronized
    fun consume(code: String, now: Long): Approval {
        val old = state()
        val pairing = old.active?.pairing() ?: return Approval.REFUSED
        val tries = Codes.Tries(old.fails, old.next)
        if (tries.waiting(now)) return Approval.WAIT
        val counter = Codes.match(pairing, code, now, old.last)
        if (counter == null) {
            val failure = tries.failed(now)
            commit(old.copy(fails = failure.fails, next = failure.next))
            return Approval.REFUSED
        }
        commit(old.copy(last = counter, fails = 0, next = 0))
        return Approval.ACCEPTED
    }

    private fun own(secret: String, state: SupervisorState) =
        listOfNotNull(state.active?.secret, liveSetup()?.pairing?.secret).any { Codes.sameKey(it, secret) }

    private fun name(value: String): String {
        val text = value.trim()
        require(text.isNotEmpty() && text.length <= 100 && text.none(Char::isISOControl)) {
            "Give this key a name (1 to 100 printable characters)."
        }
        return text
    }

    @Synchronized
    fun add(name: String, value: String, period: Int) {
        val old = state()
        val key = Codes.parse(value, period)
        require(!own(key.secret, old)) { "Use someone else's supervisor key." }
        commit(old.copy(entries = old.entries + SupervisedKey(UUID.randomUUID().toString(), name(name),
            SupervisorKey(key.secret, key.period))))
    }

    @Synchronized
    fun rename(id: String, name: String) {
        val old = state()
        val label = name(name)
        commit(old.copy(entries = old.entries.map { if (it.id == id) it.copy(name = label) else it }))
    }

    @Synchronized
    fun delete(id: String) {
        val old = state()
        commit(old.copy(entries = old.entries.filterNot { it.id == id }))
    }

    @Synchronized
    fun rows(now: Long): List<Row> {
        val data = state()
        return data.entries.filterNot { own(it.key.secret, data) }.map {
            Row(it.id, it.name, it.key.period, Codes.display(it.key.pairing(), now))
        }
    }

    companion object {
        private var instance: SupervisorStore? = null

        @Synchronized
        fun local(ctx: Context): SupervisorStore {
            instance?.let { return it }
            val disk = SupervisorDisk(ctx.applicationContext)
            return SupervisorStore(disk::load, disk::save, SystemClock::elapsedRealtime).also { instance = it }
        }
    }
}

/** SQLite commits the sealed state before approval can reach the pending action. */
private class SupervisorDisk(ctx: Context) {
    private val path = File(ctx.noBackupFilesDir, "supervisor.db")
    private val db by lazy {
        SQLiteDatabase.openOrCreateDatabase(path, null).apply {
            execSQL("PRAGMA synchronous=FULL")
            execSQL("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, sealed BLOB NOT NULL)")
        }
    }

    private fun key(create: Boolean): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val alias = "lockdown.supervisor"
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        check(create) { "Supervisor storage key is unavailable." }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build())
        }.generateKey()
    }

    fun load(): SupervisorState {
        if (!path.exists()) return SupervisorState()
        val sealed = db.rawQuery("SELECT sealed FROM state WHERE id = 1", null).use {
            if (it.moveToFirst()) it.getBlob(0) else return SupervisorState()
        }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(false), GCMParameterSpec(128, sealed, 0, 12))
        val data = cipher.doFinal(sealed, 12, sealed.size - 12)
        return Json.decodeFromString<SupervisorState>(data.decodeToString())
    }

    fun save(state: SupervisorState) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key(true))
        val sealed = cipher.iv + cipher.doFinal(Json.encodeToString(SupervisorState.serializer(), state).encodeToByteArray())
        db.execSQL("INSERT OR REPLACE INTO state (id, sealed) VALUES (1, ?)", arrayOf(sealed))
    }
}
