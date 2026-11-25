package com.husarp.lockdown.engine

import java.net.URI
import java.net.URLDecoder
import java.net.URLEncoder
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Locale
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

object SupervisorCodes {
    const val LEGACY_PERIOD = 30
    const val NEW_PERIOD = 40
    const val MAX_WAIT = 300L
    private const val ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    private val codePattern = Regex("[0-9]{6}")

    data class Pairing(val secret: String, val period: Int)
    data class Display(val code: String, val remaining: Int, val expiresIn: Int)
    data class Tries(val fails: Int = 0, val next: Long = 0) {
        fun waiting(now: Long): Boolean = next > now
        fun failed(now: Long): Tries {
            val count = fails.coerceAtMost(12) + 1
            val wait = if (count < 3) 0 else minOf(MAX_WAIT, 1L shl (count - 3))
            return Tries(count, if (wait > 0) now + wait else 0)
        }
    }

    fun validCode(value: String): Boolean = codePattern.matches(value)

    fun newPairing(): Pairing {
        val bytes = ByteArray(20)
        SecureRandom().nextBytes(bytes)
        return Pairing(encode(bytes), NEW_PERIOD)
    }

    fun uri(pairing: Pairing, name: String): String {
        val label = URLEncoder.encode(name, "UTF-8").replace("+", "%20")
        return "otpauth://totp/Lockdown:$label?secret=${pairing.secret}&issuer=Lockdown&algorithm=SHA1&digits=6&period=${pairing.period}"
    }

    fun parse(value: String, rawPeriod: Int = NEW_PERIOD): Pairing {
        val text = value.trim()
        require(text.isNotEmpty() && text.length <= 4096) { "Paste a base32 key or a pairing link." }
        var period = rawPeriod
        var secret = text
        if (text.startsWith("otpauth:", ignoreCase = true)) {
            val uri = runCatching { URI(text) }.getOrNull()
            val error = "Use a valid TOTP link with SHA1, 6 digits and a 30- or 40-second period."
            val label = uri?.rawPath?.removePrefix("/") ?: ""
            require(uri != null && uri.rawAuthority.equals("totp", ignoreCase = true) && uri.rawFragment == null &&
                label.isNotEmpty() && '/' !in label && decodeParameter(label.replace("+", "%2B")).isNotBlank()) { error }
            val params = mutableMapOf<String, String>()
            for (entry in uri.rawQuery.orEmpty().split('&')) {
                val pair = entry.split('=', limit = 2)
                require(pair.size == 2) { error }
                val key = decodeParameter(pair[0])
                val parameter = decodeParameter(pair[1])
                require(key in setOf("secret", "issuer", "algorithm", "digits", "period") &&
                    parameter.isNotBlank() && key !in params) { error }
                params[key] = parameter
            }
            require(params.getOrDefault("algorithm", "SHA1").equals("SHA1", ignoreCase = true) &&
                params.getOrDefault("digits", "6") == "6") { error }
            period = when (params.getOrDefault("period", "30")) {
                "30" -> LEGACY_PERIOD
                "40" -> NEW_PERIOD
                else -> throw IllegalArgumentException(error)
            }
            secret = params["secret"].orEmpty()
            require(Regex("[A-Za-z2-7]+=*").matches(secret)) { error }
        }
        require(period == LEGACY_PERIOD || period == NEW_PERIOD) { "Choose a 30- or 40-second period." }
        return Pairing(encode(keyBytes(secret)), period)
    }

    private fun decodeParameter(value: String): String {
        val result = URLDecoder.decode(value, "UTF-8")
        require('\uFFFD' !in result && result.none(Char::isISOControl)) { "The pairing link has invalid encoding." }
        return result
    }

    fun display(pairing: Pairing, epochSeconds: Long): Display {
        val remaining = pairing.period - (epochSeconds % pairing.period).toInt()
        return Display(hotp(keyBytes(pairing.secret), epochSeconds / pairing.period), remaining, remaining + pairing.period)
    }

    fun match(pairing: Pairing, code: String, epochSeconds: Long, last: Long = -1): Long? {
        if (!validCode(code) || epochSeconds < 0) return null
        val now = epochSeconds / pairing.period
        val key = keyBytes(pairing.secret)
        var newest: Long? = null
        var replay = false
        for (step in maxOf(0, now - 1)..now) {
            if (MessageDigest.isEqual(hotp(key, step).toByteArray(), code.toByteArray())) {
                if (step <= last) replay = true else newest = step
            }
        }
        return if (replay) null else newest
    }

    fun sameKey(first: String, second: String): Boolean = MessageDigest.isEqual(hmacKey(first), hmacKey(second))

    private fun hmacKey(secret: String): ByteArray {
        val key = keyBytes(secret)
        return (if (key.size > 64) MessageDigest.getInstance("SHA-1").digest(key) else key).copyOf(64)
    }

    private fun hotp(key: ByteArray, counter: Long): String {
        val mac = Mac.getInstance("HmacSHA1")
        mac.init(SecretKeySpec(key, "HmacSHA1"))
        val bytes = mac.doFinal(ByteBuffer.allocate(8).putLong(counter).array())
        val offset = bytes.last().toInt() and 15
        val number = (ByteBuffer.wrap(bytes, offset, 4).int and Int.MAX_VALUE) % 1_000_000
        return number.toString().padStart(6, '0')
    }

    private fun keyBytes(secret: String): ByteArray {
        val text = secret.filterNot(Char::isWhitespace).uppercase(Locale.ROOT)
        val bare = text.trimEnd('=')
        val padding = (8 - bare.length % 8) % 8
        require(bare.length % 8 in setOf(0, 2, 4, 5, 7) && (text == bare || text.length - bare.length == padding)) {
            "That is not a valid base32 key."
        }
        val out = ByteArray(bare.length * 5 / 8)
        require(out.isNotEmpty()) { "That is not a valid base32 key." }
        var buffer = 0
        var bits = 0
        var index = 0
        for (char in bare) {
            val digit = ALPHABET.indexOf(char)
            require(digit >= 0) { "That is not a valid base32 key." }
            buffer = (buffer shl 5) or digit
            bits += 5
            if (bits >= 8) {
                bits -= 8
                out[index++] = (buffer shr bits).toByte()
            }
        }
        return out
    }

    private fun encode(bytes: ByteArray): String = buildString {
        var buffer = 0
        var bits = 0
        for (byte in bytes) {
            buffer = (buffer shl 8) or (byte.toInt() and 255)
            bits += 8
            while (bits >= 5) {
                bits -= 5
                append(ALPHABET[(buffer shr bits) and 31])
            }
        }
        if (bits > 0) append(ALPHABET[(buffer shl (5 - bits)) and 31])
    }
}
