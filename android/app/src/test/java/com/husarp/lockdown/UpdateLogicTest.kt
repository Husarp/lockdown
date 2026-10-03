package com.husarp.lockdown

import com.husarp.lockdown.update.UpdateLogic
import com.husarp.lockdown.update.UpdateLogic.Asset
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class UpdateLogicTest {
    @Test fun comparesAsNumbersNotText() {
        assertTrue(UpdateLogic.isNewer("0.5.11", "0.5.10"))
        assertTrue(UpdateLogic.isNewer("0.5.10", "0.5.9"))
        assertTrue(UpdateLogic.isNewer("3.10.0", "3.9.0"))
        assertFalse(UpdateLogic.isNewer("0.5.9", "0.5.10"))
        assertFalse(UpdateLogic.isNewer("0.5.11", "0.5.11"))
        assertEquals(0, UpdateLogic.compare("1.0", "1.0.0"))
        assertTrue(UpdateLogic.isNewer("1.0.1", "1.0"))
        assertTrue(UpdateLogic.isNewer("v1.2.0", "1.1.9"))
        assertEquals(0, UpdateLogic.compare("0.6.0-beta", "0.6.0"))
    }

    @Test fun readsTheVersionFromTheApkName() {
        assertEquals("0.5.11", UpdateLogic.apkVersion("LockdownMobile-0.5.11.apk"))
        assertEquals("0.5.11", UpdateLogic.apkVersion("lockdownmobile-v0.5.11.APK"))
        assertNull(UpdateLogic.apkVersion("LockdownSetup-0.84.7.exe"))
        assertNull(UpdateLogic.apkVersion("LockdownMobile.apk"))
    }

    @Test fun picksTheApkNotTheExeNorTheTag() {
        val assets = listOf(
            Asset("LockdownSetup-0.84.7.exe", "u1", 1),
            Asset("LockdownMobile-0.5.11.apk", "u2", 2),
        )
        val (a, v) = UpdateLogic.pickApk(assets)!!
        assertEquals("u2", a.url); assertEquals("0.5.11", v)
        // two APKs: the higher version wins (compared as numbers)
        val two = assets + Asset("LockdownMobile-0.5.9.apk", "u3", 3)
        assertEquals("0.5.11", UpdateLogic.pickApk(two)!!.second)
        // no APK in the release (a PC-only release) -> no Android update
        assertNull(UpdateLogic.pickApk(listOf(Asset("LockdownSetup-0.84.7.exe", "u1", 1))))
    }

    @Test fun aPcOnlyReleaseOnTopDoesNotHideThePhoneUpdate() {
        val pc10 = UpdateLogic.Release("p10", listOf(Asset("LockdownSetup-0.84.10.exe", "e10", 1)))
        val pc9 = UpdateLogic.Release("p9", listOf(Asset("LockdownSetup-0.84.9.exe", "e9", 1), Asset("LockdownMobile-0.5.12.apk", "a12", 2)))
        val pc8 = UpdateLogic.Release("p8", listOf(Asset("LockdownMobile-0.5.11.apk", "a11", 2)))
        val (rel, apk, v) = UpdateLogic.newestApk(listOf(pc10, pc9, pc8))!!
        assertEquals("p9", rel.page); assertEquals("a12", apk.url); assertEquals("0.5.12", v)
        // drafts and pre-releases don't count; no APK anywhere -> no Android update
        val draft = UpdateLogic.Release("d", listOf(Asset("LockdownMobile-0.6.0.apk", "d", 2)), draft = true)
        val pre = UpdateLogic.Release("b", listOf(Asset("LockdownMobile-0.6.1.apk", "b", 2)), prerelease = true)
        assertEquals("0.5.12", UpdateLogic.newestApk(listOf(draft, pre, pc10, pc9))!!.third)
        assertNull(UpdateLogic.newestApk(listOf(pc10)))
    }

    @Test fun asksGithubAtMostEveryFiveMinutes() {
        val t = 1_000_000_000L
        assertTrue(UpdateLogic.mayCheck(t, 0))                       // never asked yet
        assertFalse(UpdateLogic.mayCheck(t + 60_000, t))              // 1 min later
        assertFalse(UpdateLogic.mayCheck(t + 299_999, t))
        assertTrue(UpdateLogic.mayCheck(t + 300_000, t))              // 5 min later
        assertTrue(UpdateLogic.mayCheck(t - 1, t))                    // clock went back: don't get stuck
    }

    @Test fun closingTheBannerHidesItUntilTheNextStart() {
        val g = UpdateLogic.BannerGate()
        assertTrue(g.visible("0.5.12", "0.5.11"))
        assertFalse(g.visible("0.5.11", "0.5.11"))
        assertFalse(g.visible(null, "0.5.11"))
        g.dismiss()
        assertFalse(g.visible("0.5.12", "0.5.11"))                   // coming back from another app: still hidden
        assertFalse(g.visible("0.5.13", "0.5.11"))
        g.onAppStart()
        assertTrue(g.visible("0.5.12", "0.5.11"))                    // fresh start: back again, never hidden for good
    }
}
