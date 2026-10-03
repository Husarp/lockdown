package com.husarp.lockdown.link

import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * The Island link's crypto (pure JVM, tested). A pairing code shown in the main copy and typed in the helper
 * proves both sides; it derives a shared secret that signs every later message. Any app on the phone can
 * connect to the loopback port, so nothing is trusted without a valid HMAC.
 */
object LinkCrypto {
    const val ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   // Crockford base32: no I, L, O, U
    const val CODE_LEN = 8
    const val NONCE_LEN = 16
    private val rnd = SecureRandom()

    /** A fresh 8-character pairing code (about 40 bits). */
    fun newCode(): String = String(CharArray(CODE_LEN) { ALPHABET[rnd.nextInt(ALPHABET.length)] })

    /** "K7QM2XPA" shown as "K7QM-2XPA". */
    fun display(code: String) = code.take(4) + "-" + code.drop(4)

    /** What was typed, as a code: uppercase, no spaces or dashes, O read as 0 and I / L as 1. */
    fun normalize(typed: String): String =
        typed.uppercase().filter { it != ' ' && it != '-' }.map { when (it) { 'O' -> '0'; 'I', 'L' -> '1'; else -> it } }.joinToString("")

    fun nonce(): ByteArray = ByteArray(NONCE_LEN).also { rnd.nextBytes(it) }

    fun hmac(key: ByteArray, vararg parts: ByteArray): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(key, "HmacSHA256"))
        for (p in parts) mac.update(p)
        return mac.doFinal()
    }

    /** The pairing key from a code. */
    fun pairKey(code: String): ByteArray = hmac("lockdown-island-pair-v1".toByteArray(), normalize(code).toByteArray())

    /** The shared secret both sides derive at pairing; it is never sent. */
    fun secret(kp: ByteArray, nM: ByteArray, nH: ByteArray): ByteArray = hmac(kp, "secret".toByteArray(), nM, nH)

    /** Constant-time compare. */
    fun same(a: ByteArray?, b: ByteArray?): Boolean = a != null && b != null && MessageDigest.isEqual(a, b)

    fun sha256(text: String): String = b64(MessageDigest.getInstance("SHA-256").digest(text.toByteArray()))

    fun b64(b: ByteArray): String = Base64.getEncoder().encodeToString(b)
    fun unb64(s: String?): ByteArray? = s?.let { runCatching { Base64.getDecoder().decode(it) }.getOrNull() }
}
