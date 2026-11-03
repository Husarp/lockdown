package com.husarp.lockdown.guard

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import com.husarp.lockdown.engine.CaptchaRenderer
import java.io.ByteArrayOutputStream
import kotlin.random.Random

/** Rasterise the secret before it reaches Compose; no accessible text or clipboard source. */
internal object AndroidCaptchaRenderer : CaptchaRenderer {
    override fun render(text: String, level: Int): ByteArray {
        val rng = Random.Default
        val width = text.length * 52 + 28
        val bitmap = Bitmap.createBitmap(width, 100, Bitmap.Config.ARGB_8888)
        try {
            val canvas = Canvas(bitmap)
            canvas.drawColor(Color.rgb(245, 246, 250))
            val paint = Paint(Paint.ANTI_ALIAS_FLAG)
            paint.color = Color.rgb(170, 180, 195)
            repeat(35 + level * 30) {
                canvas.drawCircle(rng.nextFloat() * width, rng.nextFloat() * 100, 1f + rng.nextFloat(), paint)
            }
            paint.strokeWidth = 1.2f
            repeat(1 + level) {
                canvas.drawLine(0f, rng.nextFloat() * 100, width.toFloat(), rng.nextFloat() * 100, paint)
            }
            paint.typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
            paint.textSize = 52f
            text.forEachIndexed { i, c ->
                val x = 40f + i * 52
                val y = 66f + rng.nextInt(-5, 6)
                canvas.save()
                canvas.rotate(rng.nextInt(-8 - level * 5, 9 + level * 5).toFloat(), x, 50f)
                canvas.skew((rng.nextFloat() - 0.5f) * (0.08f + level * 0.08f), 0f)
                paint.color = Color.rgb(30 + rng.nextInt(40), 35 + rng.nextInt(40), 65 + rng.nextInt(40))
                canvas.drawText(c.toString(), x - 16, y, paint)
                canvas.restore()
            }
            return ByteArrayOutputStream().use { out ->
                check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, out))
                out.toByteArray()
            }
        } finally { bitmap.recycle() }
    }
}
