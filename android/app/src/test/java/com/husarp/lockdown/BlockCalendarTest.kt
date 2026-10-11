package com.husarp.lockdown

import com.husarp.lockdown.engine.BlockCalendar
import com.husarp.lockdown.engine.BlockCalendar.Kind
import com.husarp.lockdown.engine.BlockCalendar.Span
import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Mode
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.SchedMode
import com.husarp.lockdown.engine.Schedule
import com.husarp.lockdown.engine.SleepCfg
import com.husarp.lockdown.engine.Window
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BlockCalendarTest {
    private fun item(id: String, vararg rules: Rule, disabled: Boolean = false) =
        Item(id, id.uppercase(), "com.$id", ItemType.APP, rules = rules.toList(), disabled = disabled)
    private fun hours(mode: SchedMode, vararg w: Window) = Rule(RuleType.SCHEDULED, schedule = Schedule(mode, w.toList()))
    private val weekdays = listOf(0, 1, 2, 3, 4)
    private fun build(items: List<Item> = emptyList(), groups: List<Group> = emptyList(), modes: List<Mode> = emptyList(),
                      sleep: SleepCfg = SleepCfg()) = BlockCalendar.build(items, groups, modes, sleep)

    @Test fun empty_config_has_seven_empty_days() {
        val w = build()
        assertEquals(7, w.days.size)
        assertTrue(w.days.all { it.spans.isEmpty() })
        assertTrue(w.noClock.isEmpty())
    }

    @Test fun always_blocked_is_all_day_every_day() {
        val w = build(listOf(item("a", Rule(RuleType.PERMANENT))))
        for (d in w.days) assertEquals(listOf(Span(0, 1440, Kind.ALWAYS, listOf("A"))), d.spans)
    }

    @Test fun block_hours_on_their_days_only() {
        val w = build(listOf(item("a", hours(SchedMode.BLOCK, Window(weekdays, "09:00", "17:00")))))
        assertEquals(listOf(Span(540, 1020, Kind.HOURS, listOf("A"))), w.days[0].spans)
        assertEquals(listOf(Span(540, 1020, Kind.HOURS, listOf("A"))), w.days[4].spans)
        assertTrue(w.days[5].spans.isEmpty())
        assertTrue(w.days[6].spans.isEmpty())
    }

    @Test fun overnight_window_splits_at_midnight_and_spills_into_next_day() {
        val w = build(listOf(item("a", hours(SchedMode.BLOCK, Window(listOf(4), "22:00", "02:00")))))   // Friday night
        assertEquals(listOf(Span(1320, 1440, Kind.HOURS, listOf("A"))), w.days[4].spans)
        assertEquals(listOf(Span(0, 120, Kind.HOURS, listOf("A"))), w.days[5].spans)
        assertTrue(w.days[3].spans.isEmpty())
    }

    @Test fun sunday_night_spills_into_monday() {
        val w = build(listOf(item("a", hours(SchedMode.BLOCK, Window(listOf(6), "23:00", "01:00")))))
        assertEquals(listOf(Span(0, 60, Kind.HOURS, listOf("A"))), w.days[0].spans)
        assertEquals(listOf(Span(1380, 1440, Kind.HOURS, listOf("A"))), w.days[6].spans)
    }

    @Test fun equal_start_and_end_is_24_hours_starting_that_day() {
        val w = build(listOf(item("a", hours(SchedMode.BLOCK, Window(listOf(2), "08:00", "08:00")))))
        assertEquals(listOf(Span(0, 1440, Kind.HOURS, listOf("A"))), w.days[2].spans)
        assertEquals(listOf(Span(0, 1440, Kind.HOURS, listOf("A"))), w.days[3].spans)   // the spill day, as the PC
        assertTrue(w.days[1].spans.isEmpty())
    }

    @Test fun allow_hours_block_everything_outside_them() {
        val w = build(listOf(item("a", hours(SchedMode.ALLOW, Window(weekdays, "09:00", "17:00")))))
        assertEquals(listOf(Span(0, 540, Kind.HOURS, listOf("A")), Span(1020, 1440, Kind.HOURS, listOf("A"))), w.days[0].spans)
        assertEquals(listOf(Span(0, 1440, Kind.HOURS, listOf("A"))), w.days[5].spans)
    }

    @Test fun allow_with_no_windows_blocks_all_day_block_with_none_blocks_nothing() {
        assertEquals(1440, build(listOf(item("a", hours(SchedMode.ALLOW)))).days[0].spans.single().end)
        assertTrue(build(listOf(item("a", hours(SchedMode.BLOCK)))).days[0].spans.isEmpty())
    }

    @Test fun several_windows_overlap_and_merge_into_one_span() {
        val w = build(listOf(item("a", hours(SchedMode.BLOCK, Window(listOf(0), "09:00", "12:00"), Window(listOf(0), "11:00", "14:00"),
            Window(listOf(0), "18:00", "19:00")))))
        assertEquals(listOf(Span(540, 840, Kind.HOURS, listOf("A")), Span(1080, 1140, Kind.HOURS, listOf("A"))), w.days[0].spans)
    }

    @Test fun items_overlapping_are_cut_so_each_stretch_names_who() {
        val w = build(listOf(
            item("b", hours(SchedMode.BLOCK, Window(listOf(0), "10:00", "12:00"))),
            item("a", hours(SchedMode.BLOCK, Window(listOf(0), "09:00", "11:00"))),
        ))
        assertEquals(listOf(
            Span(540, 600, Kind.HOURS, listOf("A")),
            Span(600, 660, Kind.HOURS, listOf("A", "B")),
            Span(660, 720, Kind.HOURS, listOf("B")),
        ), w.days[0].spans)
    }

    @Test fun always_item_skips_its_other_rules_and_other_kinds_keep_their_own_spans() {
        val w = build(listOf(
            item("a", Rule(RuleType.PERMANENT), hours(SchedMode.BLOCK, Window(listOf(0), "09:00", "10:00"))),
            item("b", hours(SchedMode.BLOCK, Window(listOf(0), "09:00", "10:00"))),
        ))
        assertEquals(listOf(Span(0, 1440, Kind.ALWAYS, listOf("A")), Span(540, 600, Kind.HOURS, listOf("B"))), w.days[0].spans)
    }

    @Test fun group_hours_count_for_members_and_paused_items_and_groups_do_not() {
        val g = Group("g1", "Social", rules = listOf(hours(SchedMode.BLOCK, Window(listOf(1), "20:00", "21:00"))), memberIds = listOf("a", "p"))
        val w = build(listOf(item("a"), item("p", disabled = true)), listOf(g))
        assertEquals(listOf(Span(1200, 1260, Kind.HOURS, listOf("A"))), w.days[1].spans)
        assertTrue(build(listOf(item("a")), listOf(g.copy(disabled = true))).days[1].spans.isEmpty())
    }

    @Test fun limits_and_temporary_have_no_clock() {
        val w = build(listOf(
            item("b", Rule(RuleType.TEMPORARY, tempUntil = "2026-12-01T10:00:00")),
            item("a", Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30)),
        ))
        assertTrue(w.days.all { it.spans.isEmpty() })
        assertEquals(listOf(Kind.LIMIT to "A", Kind.TEMPORARY to "B"), w.noClock)
    }

    @Test fun mode_schedule_block_and_allow_types() {
        val block = Mode("m1", "Study", categories = listOf("distracting"),
            schedule = Schedule(SchedMode.BLOCK, listOf(Window(listOf(0), "19:00", "21:00"))))
        val allow = Mode("m2", "Night", items = listOf("a"),
            schedule = Schedule(SchedMode.ALLOW, listOf(Window(weekdays, "09:00", "17:00"))))
        val w = build(modes = listOf(block, allow))
        assertEquals(listOf(Span(0, 540, Kind.MODE, listOf("Night")), Span(1020, 1140, Kind.MODE, listOf("Night")),
            Span(1140, 1260, Kind.MODE, listOf("Night", "Study")), Span(1260, 1440, Kind.MODE, listOf("Night"))), w.days[0].spans)
        assertEquals(listOf(Span(0, 1440, Kind.MODE, listOf("Night"))), w.days[6].spans)
    }

    @Test fun modes_without_schedule_or_targets_are_left_out() {
        val none = Mode("m1", "Manual", categories = listOf("distracting"))
        val relax = Mode("m2", "Relax", schedule = Schedule(SchedMode.BLOCK, listOf(Window(weekdays, "09:00", "10:00"))))
        assertTrue(build(modes = listOf(none, relax)).days.all { it.spans.isEmpty() })
    }

    @Test fun bedtime_runs_across_midnight_every_day_and_only_when_on() {
        assertTrue(build(sleep = SleepCfg(on = false)).days.all { it.spans.isEmpty() })
        val w = build(sleep = SleepCfg(on = true, bedtime = "23:00", wake = "07:00"))
        assertEquals(listOf(Span(0, 420, Kind.BEDTIME, listOf("Bedtime")), Span(1380, 1440, Kind.BEDTIME, listOf("Bedtime"))), w.days[3].spans)
        assertEquals(420, w.days[0].spans.first().end)   // Sunday night's spill
    }

    @Test fun bedtime_after_midnight_stays_one_stretch() {
        val w = build(sleep = SleepCfg(on = true, bedtime = "01:00", wake = "06:30"))
        assertEquals(listOf(Span(60, 390, Kind.BEDTIME, listOf("Bedtime"))), w.days[2].spans)
    }

    @Test fun spans_are_ordered_by_kind_then_time() {
        val w = build(
            items = listOf(item("a", hours(SchedMode.BLOCK, Window(listOf(0), "05:00", "06:00"))), item("z", Rule(RuleType.PERMANENT))),
            sleep = SleepCfg(on = true, bedtime = "22:00", wake = "07:00"),
        )
        assertEquals(listOf(Kind.ALWAYS, Kind.HOURS, Kind.BEDTIME, Kind.BEDTIME), w.days[0].spans.map { it.kind })
        assertEquals(listOf(0, 1320), w.days[0].spans.filter { it.kind == Kind.BEDTIME }.map { it.start })
    }
}
