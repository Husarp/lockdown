package com.husarp.lockdown

import com.husarp.lockdown.engine.Group
import com.husarp.lockdown.engine.Item
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Mode
import com.husarp.lockdown.engine.ModeActive
import com.husarp.lockdown.engine.ModeExtra
import com.husarp.lockdown.engine.Modes
import com.husarp.lockdown.engine.Pomodoro
import com.husarp.lockdown.engine.SchedMode
import com.husarp.lockdown.engine.Schedule
import com.husarp.lockdown.engine.Window
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

class ModesEngineTest {
    private val t0 = LocalDateTime.of(2026, 9, 28, 12, 0)   // Monday noon
    private val pom = Pomodoro(work = 25, brk = 5, rounds = 4, long = 15)

    @Test fun pomodoro_length_and_phases() {
        assertEquals(130L, Modes.pomodoroLength(pom))                       // 4*25 + 3*5 + 15
        assertEquals("focus", Modes.pomodoroPhase(pom, t0, t0)!!.first)
        assertEquals("break", Modes.pomodoroPhase(pom, t0, t0.plusMinutes(27))!!.first)
        val long = Modes.pomodoroPhase(pom, t0, t0.plusMinutes(120))!!
        assertEquals("long break", long.first)
        assertEquals(4, long.third)
        assertNull(Modes.pomodoroPhase(pom, t0, t0.plusMinutes(130)))       // all rounds done
    }

    @Test fun hand_started_mode_is_active_until_its_end() {
        val work = Mode("work", "Work", categories = listOf("distracting"))
        val state = ModeActive("work", t0.toString(), until = t0.plusHours(1).toString())
        assertEquals("work", Modes.active(t0.plusMinutes(30), listOf(work), state)!!.mode.id)
        assertNull(Modes.active(t0.plusHours(2), listOf(work), state))       // past its end
    }

    @Test fun focus_mode_blocks_in_focus_not_in_break() {
        val focus = Mode("focus", "Focus", categories = listOf("distracting"), pomodoro = pom)
        val state = ModeActive("focus", t0.toString())                      // no explicit end -> capped to pomodoro length
        val inFocus = Modes.active(t0.plusMinutes(10), listOf(focus), state)
        assertTrue(Modes.blocking(inFocus))
        val inBreak = Modes.active(t0.plusMinutes(27), listOf(focus), state)
        assertFalse(Modes.blocking(inBreak))
        assertEquals(t0.plusMinutes(130), inFocus!!.until)                   // pomodoro caps the end
    }

    @Test fun scheduled_mode_turns_on_in_its_window() {
        val sched = Mode("s1", "Evening", schedule = Schedule(SchedMode.BLOCK, listOf(Window(listOf(0), "20:00", "23:00"))))
        assertNull(Modes.active(t0, listOf(sched), null))                    // noon: outside window
        val evening = LocalDateTime.of(2026, 9, 28, 21, 0)
        val on = Modes.active(evening, listOf(sched), null)!!
        assertTrue(on.scheduled)
        assertEquals(LocalDateTime.of(2026, 9, 28, 23, 0), on.until)
    }

    @Test fun locked_mode_needs_force_to_stop() {
        val work = Mode("work", "Work")
        val state = ModeActive("work", t0.toString(), until = t0.plusHours(1).toString(), locked = true)
        val active = Modes.active(t0.plusMinutes(5), listOf(work), state)
        assertFalse(Modes.canStop(active, force = false))
        assertTrue(Modes.canStop(active, force = true))
    }

    @Test fun targets_gather_category_explicit_group_and_extra() {
        val i1 = Item("1", "TikTok", "com.tiktok", ItemType.APP)            // no category -> distracting
        val i2 = Item("2", "News", "news.com", ItemType.SITE)              // productive
        val i3 = Item("3", "Shop", "shop.com", ItemType.SITE)             // productive but explicitly picked
        val items = listOf(i1, i2, i3)
        val categories = mapOf(
            "site:news.com" to "productive",
            "site:shop.com" to "productive",
            "app:com.games" to "distracting",                              // in the category, not on the blocklist
        )
        val g1 = Group("g1", "Reading", memberIds = listOf("2"))
        val mode = Mode("work", "Work", categories = listOf("distracting"), items = listOf("3"),
            groups = listOf("g1"), extra = listOf(ModeExtra("Reddit", ItemType.SITE, listOf("reddit.com"))))

        val t = Modes.targets(mode, items, listOf(g1), categories)
        val apps = Modes.blockedApps(t)
        val hosts = Modes.blockedHosts(t)
        assertTrue(apps.contains("com.tiktok"))          // distracting category
        assertTrue(apps.contains("com.games"))           // category member not on the blocklist
        assertTrue(hosts.contains("shop.com"))           // explicit item
        assertTrue(hosts.contains("news.com"))           // via the picked group
        assertTrue(hosts.contains("reddit.com"))         // extra
    }

    @Test fun item_category_defaults_to_distracting() {
        val app = Item("1", "X", "com.x", ItemType.APP)
        assertEquals("distracting", Modes.itemCategory(app, emptyMap()))
        assertEquals("neutral", Modes.itemCategory(app, mapOf("app:com.x" to "neutral")))
    }

    @Test fun describe_reads_naturally() {
        assertEquals("Blocks: Distracting", Modes.describe(Mode("work", "Work", categories = listOf("distracting"))))
        assertEquals("Blocks: nothing extra", Modes.describe(Mode("relax", "Relax")))
        assertEquals("Blocks: Distracting + 1 more",
            Modes.describe(Mode("w", "W", categories = listOf("distracting"), items = listOf("3"))))
    }

    @Test fun merge_keeps_builtins_and_appends_custom() {
        val saved = listOf(
            Mode("work", "Deep Work", categories = listOf("distracting", "neutral")),   // edited built-in
            Mode("custom1", "Gaming block", categories = listOf("distracting")),        // custom
        )
        val merged = Modes.merge(saved)
        val work = merged.first { it.id == "work" }
        assertEquals("Deep Work", work.name)
        assertTrue(work.builtin)
        assertTrue(merged.any { it.id == "custom1" && !it.builtin })
        assertEquals(Modes.DEFAULT_MODES.size + 1, merged.size)
    }
}
