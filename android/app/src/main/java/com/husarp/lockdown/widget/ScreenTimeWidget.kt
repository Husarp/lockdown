package com.husarp.lockdown.widget

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews
import com.husarp.lockdown.MainActivity
import com.husarp.lockdown.R
import com.husarp.lockdown.ui.fmtDuration
import com.husarp.lockdown.usage.Usage

/** Home-screen widget: today's total screen time. Tap it to open the app. */
class ScreenTimeWidget : AppWidgetProvider() {
    override fun onUpdate(ctx: Context, mgr: AppWidgetManager, ids: IntArray) {
        val (s, e) = Usage.dayBounds(0)
        val total = runCatching { Usage.range(ctx, s, e).totalMs }.getOrDefault(0)
        val open = PendingIntent.getActivity(
            ctx, 0, Intent(ctx, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        for (id in ids) {
            val v = RemoteViews(ctx.packageName, R.layout.widget_screentime)
            v.setTextViewText(R.id.time, if (total > 0) fmtDuration(total) else "—")
            v.setOnClickPendingIntent(R.id.root, open)
            mgr.updateAppWidget(id, v)
        }
    }
}
