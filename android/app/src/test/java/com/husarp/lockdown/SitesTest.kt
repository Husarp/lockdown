package com.husarp.lockdown

import com.husarp.lockdown.engine.Alerts
import com.husarp.lockdown.engine.EffRule
import com.husarp.lockdown.engine.Rule
import com.husarp.lockdown.engine.RuleType
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.Sites
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.LocalDateTime

class SitesTest {
    @Test fun a_site_address_never_keeps_a_space() {
        assertEquals("youtube.com", Sites.clean("youtube. com"))                 // the keyboard's space after a dot
        assertEquals("youtube.com", Sites.clean(" https://www.YouTube .com/watch "))
        assertEquals("reddit.com", Sites.clean("red dit.com"))
    }

    @Test fun a_box_of_several_sites_joins_dots_and_splits_the_rest() {
        assertEquals("youtube.", Sites.joinDots("youtube. "))                     // as typed: the space goes at once
        assertEquals(listOf("youtube.com", "reddit.com"), Sites.cleanList("youtube. com  www.reddit .com"))
        assertEquals(listOf("a.com", "b.com"), Sites.cleanList("a.com,b.com, a.com"))
        assertEquals(listOf("ads.example.com"), Sites.importLine("0.0.0.0 ads.example.com"))   // a hosts-file line
        assertEquals(listOf("x.com"), Sites.importLine("127.0.0.1 x.com"))
        assertEquals(listOf("a.com", "b.com"), Sites.importLine("https://www.a.com/x b. com localhost"))
    }

    @Test fun a_site_or_word_box_strips_spaces_only_from_a_site() {
        assertEquals("example.com", Sites.cleanIfSite("example. com"))
        assertEquals("free stuff", Sites.cleanIfSite("free stuff"))
    }

    @Test fun a_blocked_item_shows_one_line() {
        val now = LocalDateTime.of(2026, 10, 8, 14, 0)   // Thursday
        assertEquals("Blocked until 18:00", Alerts.blockedLine(now.withHour(18), now))
        assertEquals("Blocked until Friday 07:00", Alerts.blockedLine(now.plusDays(1).withHour(7), now))
        assertEquals("Blocked", Alerts.blockedLine(null, now))
        assertEquals("Blocked", Alerts.blockedLine(LocalDateTime.MAX, now))
    }

    @Test fun the_line_shows_the_block_that_lasts_longest() {
        val now = LocalDateTime.of(2026, 10, 8, 14, 0)
        val temp = EffRule(Rule(RuleType.TEMPORARY, tempUntil = "2026-10-08T15:00:00"), "item:1", "item:1", "item:1", "t")
        val limit = EffRule(Rule(RuleType.TIME_LIMIT, dailyLimitMin = 30), "item:1", "item:1", "item:1", "l")
        val usedUp: (String, String) -> Int = { o, b -> if (o == "item:1" && b == "day:2026-10-08") 30 * 60 else 0 }
        // Temporary (the more important reason) ends at 15:00, but the used-up limit keeps it blocked till midnight
        assertEquals(LocalDateTime.of(2026, 10, 9, 0, 0), Rules.blockEnd(listOf(temp, limit), now, usedUp))
        assertEquals(LocalDateTime.of(2026, 10, 8, 15, 0), Rules.blockEnd(listOf(temp, limit), now))
        assertEquals(LocalDateTime.MAX, Rules.blockEnd(listOf(temp, EffRule(Rule(RuleType.PERMANENT), "item:1", "item:1", "item:1", "p")), now))
        assertEquals(null, Rules.blockEnd(listOf(limit), now))
    }
}
