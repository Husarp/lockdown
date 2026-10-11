package com.husarp.lockdown

import com.husarp.lockdown.engine.MuteDnd
import com.husarp.lockdown.engine.MuteDnd.ALARMS
import com.husarp.lockdown.engine.MuteDnd.ALL
import com.husarp.lockdown.engine.MuteDnd.Saved
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MuteDndTest {
    private val ours = Saved(ALL, ALARMS)

    @Test fun turnsOnWhenOffAndRemembers() {
        val s = MuteDnd.decide(true, true, ALL, null)
        assertEquals(ALARMS, s.filter); assertEquals(ours, s.saved)
    }
    @Test fun alreadyOnIsLeftAlone() {
        val s = MuteDnd.decide(true, true, 2, null)
        assertNull(s.filter); assertNull(s.saved)
        // and after the mode ends nothing is turned off
        assertNull(MuteDnd.decide(false, true, 2, s.saved).filter)
    }
    @Test fun restoresWhenModeEnds() {
        val s = MuteDnd.decide(false, true, ALARMS, ours)
        assertEquals(ALL, s.filter); assertNull(s.saved)
        assertEquals(2, MuteDnd.decide(false, true, ALARMS, Saved(2, ALARMS)).filter)   // the old one was Priority
    }
    @Test fun keepsWhileModeRuns() {
        val s = MuteDnd.decide(true, true, ALARMS, ours)
        assertNull(s.filter); assertEquals(ours, s.saved)
    }
    @Test fun userChangedItDuringModeIsNotFoughtNorRestored() {
        val y = MuteDnd.decide(true, true, ALL, ours)
        assertNull(y.filter); assertEquals(ours.copy(yielded = true), y.saved)
        assertNull(MuteDnd.decide(true, true, ALL, y.saved).filter)        // not turned on again
        val end = MuteDnd.decide(false, true, ALL, y.saved)
        assertNull(end.filter); assertNull(end.saved)                       // nor restored at the end
        assertNull(MuteDnd.decide(false, true, 3, ours).filter)             // changed to Total silence, mode ended
    }
    @Test fun modeEndedWhileAppWasDeadStillRestores() {
        val back = MuteDnd.decode(MuteDnd.encode(ours))
        assertEquals(ALL, MuteDnd.decide(false, true, ALARMS, back).filter)
    }
    @Test fun accessRevokedForgetsEverything() {
        val s = MuteDnd.decide(false, false, ALARMS, ours)
        assertNull(s.filter); assertNull(s.saved)
        assertNull(MuteDnd.decide(true, false, ALL, null).filter)
    }
    @Test fun nothingWantedNothingSaved() {
        val s = MuteDnd.decide(false, true, ALL, null)
        assertNull(s.filter); assertNull(s.saved)
    }
    @Test fun encodeRoundTrip() {
        assertNull(MuteDnd.decode(MuteDnd.encode(null))); assertNull(MuteDnd.decode("x"))
        assertEquals(Saved(2, 4, true), MuteDnd.decode(MuteDnd.encode(Saved(2, 4, true))))
    }
    @Test fun unreadableFilterIsNotTheUserChangingIt() {
        val s = MuteDnd.decide(true, true, MuteDnd.UNKNOWN, ours)
        assertNull(s.filter); assertEquals(ours, s.saved)
        val after = MuteDnd.decide(true, true, ALARMS, s.saved)   // later reads fine: still ours, no yield
        assertEquals(ours, after.saved)
    }
}
