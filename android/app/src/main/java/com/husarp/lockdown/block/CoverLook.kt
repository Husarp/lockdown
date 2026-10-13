package com.husarp.lockdown.block

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.ColorDrawable
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.os.Handler
import android.os.SystemClock
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.compose.ui.graphics.toArgb
import androidx.core.content.res.ResourcesCompat
import androidx.core.graphics.ColorUtils
import com.husarp.lockdown.R
import com.husarp.lockdown.engine.Cadence
import com.husarp.lockdown.ui.theme.DarkColors
import com.husarp.lockdown.ui.theme.DarkTones
import com.husarp.lockdown.ui.theme.Hue

/**
 * The look of the bedtime screen and a strict break, after the PC's full-screen cover (src/ui/css/overlay.css): centred,
 * a soft round badge with the kind's icon, a big number (the clock at bedtime, the countdown on a break), the title,
 * the text, then the buttons - Dismiss filled, Disable alerts quiet, Emergency a pink text link with the lifebuoy.
 * On a phone the buttons stack full width (portrait). Always the dark palette (the app's own dark tokens), as the PC's
 * cover is dark by default. Plain Views: the accessibility service draws it, outside any activity. Looks only - what
 * the buttons do, and when, stays in [BlockService].
 */
internal class CoverLook(private val ctx: Context) {
    enum class Kind(val hue: Hue, val icon: Int) {
        SLEEP(Hue.INDIGO, R.drawable.ic_cover_moon),       // the PC's k-sched
        BREAK(Hue.GREEN, R.drawable.ic_cover_coffee),      // the PC's k-ok
        REMIND(Hue.AMBER, R.drawable.ic_cover_bell),       // a reminder's hue in the app
    }

    /** dismiss (or the first button) = PRIMARY, disable = GHOST, emergency = EMERG (a text link), the rest PLAIN. */
    enum class Role { PRIMARY, GHOST, PLAIN, EMERG }

    /** The cover as built: [root] goes in the window, [hero] is the big number (hidden until it has one), the
     *  buttons go in [buttons]. */
    class Built(val root: LinearLayout, val hero: TextView, val buttons: LinearLayout)

    private val cs = DarkColors
    private val bg = cs.background.toArgb()
    private val ink = cs.onSurface.toArgb()
    private val ink2 = cs.onSurfaceVariant.toArgb()
    private val emerg = DarkTones[Hue.PINK.ordinal].accent.toArgb()     // = the PC's --k-emerg
    private val headFont = font(R.font.barlow_condensed_bold)
    private val numerals = font(R.font.barlow_condensed_extrabold)
    private val inter = font(R.font.inter_variable)

    private fun font(id: Int): Typeface? = runCatching { ResourcesCompat.getFont(ctx, id) }.getOrNull()
    private fun dp(v: Int) = (v * ctx.resources.displayMetrics.density).toInt()

    /** The cover's column. [heroFirst]: the number above the title (bedtime: the clock); else under it (a break). */
    fun build(kind: Kind, title: String, body: String, heroFirst: Boolean): Built {
        val tone = DarkTones[kind.hue.ordinal]
        val accent = tone.accent.toArgb()
        val col = MaxWidthColumn(ctx, dp(440)).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(dp(24), dp(40), dp(24), dp(40))
        }
        val halo = FrameLayout(ctx).apply {
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(ColorUtils.compositeColors(ColorUtils.setAlphaComponent(accent, 41), bg))   // ~16 %: the PC's --k-soft
            }
            addView(ImageView(ctx).apply { setImageResource(kind.icon); imageTintList = ColorStateList.valueOf(accent) },
                FrameLayout.LayoutParams(dp(32), dp(32), Gravity.CENTER))
        }
        val hero = TextView(ctx).apply {
            textSize = 64f; setTextColor(ink); gravity = Gravity.CENTER; includeFontPadding = false
            typeface = numerals; letterSpacing = -0.01f; fontFeatureSettings = "tnum"
            visibility = View.GONE
        }
        val head = TextView(ctx).apply {
            this.text = title; textSize = 32f; setTextColor(ink); gravity = Gravity.CENTER
            typeface = headFont
        }
        val para = TextView(ctx).apply {
            this.text = body; textSize = 17f; setTextColor(ink2); gravity = Gravity.CENTER
            typeface = inter; setLineSpacing(0f, 1.35f)
        }
        val buttons = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER_HORIZONTAL }
        col.addView(halo, LinearLayout.LayoutParams(dp(72), dp(72)))
        val order = if (heroFirst) listOf(hero, head) else listOf(head, hero)
        for (v in order + para) col.addView(v, wide(top = dp(16)))
        col.addView(buttons, wide(top = dp(32)))
        return Built(col, hero, buttons)
    }

    private fun wide(top: Int) = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
        .apply { topMargin = top }

    /** A cover button. Off ([enabled] false) it is faded, as the PC's (.55). */
    fun button(label: String, role: Role, enabled: Boolean = true): Button = Button(ctx).apply {
        this.text = label
        isAllCaps = false; stateListAnimator = null; minHeight = 0; minimumHeight = 0; minWidth = 0; minimumWidth = 0
        textSize = if (role == Role.EMERG) 15f else 16f
        typeface = inter; fontVariationSettings = "'wght' 600"
        maxLines = 2; ellipsize = TextUtils.TruncateAt.END
        val radius = dp(12).toFloat()
        val (fill, fg) = when (role) {
            Role.PRIMARY -> Color.parseColor("#DB5126") to Color.WHITE        // the PC cover's --brand / --on-brand
            Role.PLAIN -> cs.surfaceContainerLow.toArgb() to ink
            Role.GHOST -> Color.TRANSPARENT to ink
            Role.EMERG -> Color.TRANSPARENT to emerg
        }
        setTextColor(fg)
        background = ripple(fg, GradientDrawable().apply {
            cornerRadius = radius; setColor(fill)
            if (role == Role.PLAIN) setStroke(dp(1), cs.outlineVariant.toArgb())
        }, radius)
        if (role == Role.EMERG) {
            val icon = ctx.getDrawable(R.drawable.ic_cover_lifebuoy)?.mutate()?.apply {
                setTint(emerg); setBounds(0, 0, dp(18), dp(18))
            }
            setCompoundDrawablesRelative(icon, null, null, null)
            compoundDrawablePadding = dp(8)
            setPadding(dp(16), 0, dp(16), 0)
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, dp(48)).apply { topMargin = dp(12) }
        } else {
            setPadding(dp(20), 0, dp(20), 0)
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(52)).apply { topMargin = dp(10) }
        }
        isEnabled = enabled
        alpha = if (enabled) 1f else DISABLED_ALPHA
    }

    private fun ripple(ink: Int, content: Drawable, radius: Float) = RippleDrawable(
        ColorStateList.valueOf(ColorUtils.setAlphaComponent(ink, 0x29)), content,
        GradientDrawable().apply { cornerRadius = radius; setColor(Color.WHITE) })

    /** A line of text among the buttons (the emergency question), centred and quiet. */
    fun note(body: String) = TextView(ctx).apply {
        this.text = body; textSize = 15f; setTextColor(ink2); gravity = Gravity.CENTER
        typeface = inter; setLineSpacing(0f, 1.3f)
        layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            .apply { bottomMargin = dp(6) }
    }

    /** The window's root: the solid dark colour (no animation, nothing else). */
    fun backdrop() = ColorDrawable(bg)

    /** Keeps [hero] showing [read]'s text while it is on screen: [read] gives the text and how long until it changes
     *  (null: it won't). Nothing runs while the cover is off screen (a call, the lock screen, a dismissed break). */
    fun ticking(hero: TextView, handler: Handler, read: () -> Pair<String, Long?>) {
        val tick = object : Runnable {
            override fun run() {
                handler.removeCallbacks(this)
                val (s, next) = runCatching(read).getOrNull() ?: return
                if (hero.text.toString() != s) hero.text = s
                hero.visibility = if (s.isEmpty()) View.GONE else View.VISIBLE
                if (next != null) handler.postDelayed(this, next.coerceAtLeast(1))
            }
        }
        hero.addOnAttachStateChangeListener(object : View.OnAttachStateChangeListener {
            override fun onViewAttachedToWindow(v: View) = tick.run()
            override fun onViewDetachedFromWindow(v: View) = handler.removeCallbacks(tick)
        })
        tick.run()
    }

    companion object {
        const val DISABLED_ALPHA = 0.55f

        /** "4:59" / "1:04:59" (the PC's countdownText), from ms left. */
        fun countdownText(msLeft: Long): String {
            val sec = ((msLeft.coerceAtLeast(0) + 999) / 1000)
            val h = sec / 3600; val m = (sec % 3600) / 60; val s = sec % 60
            return if (h > 0) "%d:%02d:%02d".format(h, m, s) else "%d:%02d".format(m, s)
        }

        /** The break countdown for [endAt] (elapsedRealtime): its text and when the shown second changes. */
        fun countdown(endAt: Long, now: Long = SystemClock.elapsedRealtime()): Pair<String, Long?> {
            val left = endAt - now
            return countdownText(left) to (if (left > 0) Cadence.countdownMs(endAt, now) else null)
        }
    }
}

/** A column no wider than [max] (a tablet, or a phone on its side), centred by its parent. */
private class MaxWidthColumn(ctx: Context, private val max: Int) : LinearLayout(ctx) {
    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val w = MeasureSpec.getSize(widthMeasureSpec)
        val spec = if (w > max) MeasureSpec.makeMeasureSpec(max, MeasureSpec.EXACTLY) else widthMeasureSpec
        super.onMeasure(spec, heightMeasureSpec)
    }
}
