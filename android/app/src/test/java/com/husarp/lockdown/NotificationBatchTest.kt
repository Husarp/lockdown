package com.husarp.lockdown

import com.husarp.lockdown.engine.NotificationBatch
import org.junit.Assert.*
import org.junit.Test

class NotificationBatchTest {
    @Test fun mergesNamesReasonsAndDifferentEndTimes() {
        val b = NotificationBatch()
        assertEquals("YouTube: limit reached until 18:00.\nInstagram: blocked until 20:00.",
            b.merge(listOf("YouTube: limit reached until 18:00.", "Instagram: blocked until 20:00."), 0))
        assertTrue(b.merge(listOf("Steam: blocked until 21:00."), 3000)!!.contains("YouTube"))
    }
    @Test fun duplicatesWaitTenMinutesButNewWarningsAreKept() {
        val b = NotificationBatch()
        assertNotNull(b.merge(listOf("YouTube: 5 minutes left."), 0))
        assertNull(b.merge(listOf("YouTube: 5 minutes left."), 30_000))
        assertEquals("YouTube: 1 minute left.", b.merge(listOf("YouTube: 1 minute left."), 60_000))
        assertNotNull(b.merge(listOf("YouTube: 5 minutes left."), 600_000))
    }
    @Test fun burstExpiresAndEmptyMessagesNeverPost() {
        val b = NotificationBatch()
        assertNull(b.merge(listOf("", "  "), 0))
        b.merge(listOf("YouTube blocked."), 0)
        assertEquals("Instagram blocked.", b.merge(listOf("Instagram blocked."), 11_000))
    }
    @Test fun multilineModeDetailsDeduplicateAgainstRuleEvents() {
        val b = NotificationBatch()
        assertEquals("Work mode on\nYouTube blocked until 18:00.",
            b.merge(listOf("Work mode on\nYouTube blocked until 18:00.", "YouTube blocked until 18:00."), 0))
    }
    @Test fun continuousStreamDoesNotKeepOldDetailsForever() {
        val b = NotificationBatch()
        b.merge(listOf("A blocked."), 0)
        b.merge(listOf("B blocked."), 9000)
        assertEquals("C blocked.", b.merge(listOf("C blocked."), 18_000))
    }
    @Test fun warningsForSeveralItemsMergeAtOnce() {
        val b = NotificationBatch()
        val text = b.merge(listOf("YouTube: 5 min left.", "Instagram: 5 min left.", "YouTube: 5 min left."), 0)
        assertEquals("YouTube: 5 min left.\nInstagram: 5 min left.", text)
        assertNull(b.merge(listOf("Instagram: 5 min left."), 1000))
    }
}
