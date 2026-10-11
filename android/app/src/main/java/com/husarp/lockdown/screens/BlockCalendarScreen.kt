package com.husarp.lockdown.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bedtime
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.data.ModesStore
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.BlockCalendar
import com.husarp.lockdown.engine.BlockCalendar.Kind
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.Kinds
import com.husarp.lockdown.ui.Look
import com.husarp.lockdown.ui.SectionLabel
import com.husarp.lockdown.ui.theme.Hue
import com.husarp.lockdown.ui.theme.tone

private val DAY_NAMES = listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
private val HOUR_H = 18.dp      // one hour of the grid

/** What each kind looks like (the redesign's colours: red always, amber limits, indigo hours, violet temporary,
 *  blue modes), and a word for the legend. Bedtime is plain slate - indigo already means hours here. */
private fun look(k: Kind): Look = when (k) {
    Kind.ALWAYS -> Kinds.rule(RuleType.PERMANENT)
    Kind.LIMIT -> Kinds.rule(RuleType.TIME_LIMIT)
    Kind.HOURS -> Kinds.rule(RuleType.SCHEDULED)
    Kind.TEMPORARY -> Kinds.rule(RuleType.TEMPORARY)
    Kind.MODE -> Kinds.MODE
    Kind.BEDTIME -> Look(Hue.SLATE, Icons.Filled.Bedtime)
}

private fun kindName(k: Kind) = when (k) {
    Kind.ALWAYS -> "Always blocked"
    Kind.LIMIT -> "Limits"
    Kind.HOURS -> "Blocked hours"
    Kind.TEMPORARY -> "Temporary"
    Kind.MODE -> "Modes"
    Kind.BEDTIME -> "Bedtime"
}

private fun hhmm(m: Int) = if (m >= BlockCalendar.DAY) "24:00" else "%02d:%02d".format(m / 60, m % 60)

/** The week at a glance, read only: Mon-Sun, 24 hours, one thin lane per kind of block in each day. Tap a day for
 *  what is blocked then. Only what has a time of day is drawn (always-blocked, hours, scheduled modes, bedtime);
 *  limits and temporary blocks are listed under it. A scheduled Pomodoro mode shows its whole window. */
@Composable
fun BlockCalendarScreen() {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val modes by ModesStore.modes.collectAsStateWithLifecycle()
    val cs = MaterialTheme.colorScheme
    val today = TrustedTime.local(LocalContext.current).dayOfWeek.value - 1
    var picked by rememberSaveable { mutableIntStateOf(today) }
    val week = remember(cfg.items, cfg.groups, modes, cfg.sleep) { BlockCalendar.build(cfg.items, cfg.groups, modes, cfg.sleep) }
    val kinds = remember(week) { week.days.flatMap { d -> d.spans.map { it.kind } }.distinct().sorted() }
    val day = week.days[picked]

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("When things are blocked, by day. Tap a day for the details. Nothing here can be changed.",
            style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
        // legend
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            val legend = Kind.entries.filter { it in kinds || week.noClock.any { n -> n.first == it } }
            if (legend.isEmpty()) Text("Nothing is blocked by a clock rule yet.", color = cs.onSurfaceVariant)
            for (rowKinds in legend.chunked(2)) Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                for (k in rowKinds) Row(Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    val l = look(k)
                    Box(Modifier.size(12.dp).clip(RoundedCornerShape(3.dp)).background(tone(l.hue).accent))
                    Icon(l.icon, null, Modifier.size(14.dp), tint = tone(l.hue).accent)
                    Text(kindName(k) + if (k == Kind.LIMIT || k == Kind.TEMPORARY) " (no clock)" else "",
                        style = MaterialTheme.typography.labelMedium)
                }
                if (rowKinds.size == 1) Box(Modifier.weight(1f))
            }
        }
        // the grid
        Card(color = cs.surfaceContainerLow, padding = androidx.compose.foundation.layout.PaddingValues(8.dp)) {
            Row(Modifier.fillMaxWidth()) {
                Box(Modifier.width(28.dp))
                for (d in 0..6) Box(Modifier.weight(1f).heightIn(min = 48.dp)
                    .clickable(role = androidx.compose.ui.semantics.Role.Tab) { picked = d }
                    .semantics { selected = d == picked }, contentAlignment = Alignment.Center) {
                    Text(DAY_NAMES[d], maxLines = 1,
                        style = MaterialTheme.typography.labelSmall, textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                        fontWeight = if (d == today) FontWeight.Bold else FontWeight.Normal,
                        color = if (d == picked) cs.primary else cs.onSurfaceVariant)
                }
            }
            Row(Modifier.fillMaxWidth().height(HOUR_H * 24)) {
                Box(Modifier.width(28.dp).fillMaxHeight()) {
                    for (h in 0..21 step 3) Text("%02d".format(h), Modifier.offset(y = HOUR_H * h), style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant)
                }
                for (d in 0..6) {
                    val spans = week.days[d].spans
                    Row(Modifier.weight(1f).fillMaxHeight().padding(horizontal = 1.dp).clip(RoundedCornerShape(4.dp))
                        .background(if (d == picked) cs.surfaceContainerHighest else cs.surfaceContainer)
                        .clickable { picked = d }
                        .semantics { contentDescription = DAY_NAMES[d] + ": " + if (spans.isEmpty()) "nothing blocked" else spans.joinToString(", ") { "${kindName(it.kind)} ${hhmm(it.start)} to ${hhmm(it.end)}" } }) {
                        for (k in kinds) Box(Modifier.weight(1f).fillMaxHeight()) {
                            for (s in spans.filter { it.kind == k }) Box(Modifier.offset(y = HOUR_H * (s.start / 60f)).fillMaxWidth()
                                .height(HOUR_H * ((s.end - s.start) / 60f)).background(tone(look(k).hue).accent))
                        }
                    }
                }
            }
        }
        // the picked day
        Card(color = cs.surfaceContainerLow) {
            SectionLabel(DAY_NAMES[picked] + if (picked == today) " · today" else "")
            if (day.spans.isEmpty()) Text("Nothing is blocked at a set time.", Modifier.padding(top = 6.dp), color = cs.onSurfaceVariant)
            for (s in day.spans) {
                val l = look(s.kind)
                Row(Modifier.fillMaxWidth().padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Icon(l.icon, null, Modifier.size(20.dp), tint = tone(l.hue).accent)
                    Column(Modifier.weight(1f)) {
                        Text("${hhmm(s.start)}–${hhmm(s.end)} · ${kindName(s.kind)}", style = MaterialTheme.typography.titleSmall)
                        Text(s.labels.joinToString(", "), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                    }
                }
            }
        }
        if (week.noClock.isNotEmpty()) Card(color = cs.surfaceContainerLow) {
            SectionLabel("Not tied to a time")
            for (k in listOf(Kind.LIMIT, Kind.TEMPORARY)) {
                val names = week.noClock.filter { it.first == k }.map { it.second }
                if (names.isEmpty()) continue
                val l = look(k)
                Row(Modifier.fillMaxWidth().padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Icon(l.icon, null, Modifier.size(20.dp), tint = tone(l.hue).accent)
                    Column(Modifier.weight(1f)) {
                        Text(kindName(k), style = MaterialTheme.typography.titleSmall)
                        Text(names.joinToString(", "), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
                    }
                }
            }
        }
    }
}
