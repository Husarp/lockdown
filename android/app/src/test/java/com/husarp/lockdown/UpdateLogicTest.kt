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
