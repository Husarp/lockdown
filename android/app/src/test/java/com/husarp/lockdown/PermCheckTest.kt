package com.husarp.lockdown

import com.husarp.lockdown.engine.Perm
import com.husarp.lockdown.engine.PermCheck
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PermCheckTest {
    private val pkg = "com.husarp.lockdown"
    private val cls = "com.husarp.lockdown.block.BlockService"

    @Test fun accessibilityListedInFullOrShortForm() {
        assertTrue(PermCheck.a11yListed("com.husarp.lockdown/com.husarp.lockdown.block.BlockService", pkg, cls))
        assertTrue(PermCheck.a11yListed("com.husarp.lockdown/.block.BlockService", pkg, cls))
        assertTrue(PermCheck.a11yListed("com.other/.Svc:com.husarp.lockdown/.block.BlockService:org.x/org.x.Y", pkg, cls))
        assertTrue(PermCheck.a11yListed("COM.HUSARP.LOCKDOWN/com.husarp.lockdown.block.BlockService", pkg, cls))
    }

    @Test fun accessibilityNotListed() {
        assertFalse(PermCheck.a11yListed(null, pkg, cls))
        assertFalse(PermCheck.a11yListed("", pkg, cls))
        assertFalse(PermCheck.a11yListed("com.other/.block.BlockService", pkg, cls))
        assertFalse(PermCheck.a11yListed("com.husarp.lockdown/.Other", pkg, cls))
        assertFalse(PermCheck.a11yListed("com.husarp.lockdown.debug/.block.BlockService", pkg, cls))   // another app id
        assertFalse(PermCheck.a11yListed("com.husarp.lockdown", pkg, cls))
    }

    @Test fun accessibilityRunningIsWhatCounts() {
        assertEquals(Perm.ON, PermCheck.a11y(listed = true, running = true))
        assertEquals(Perm.ON, PermCheck.a11y(listed = false, running = true))     // just unticked, still bound
        assertEquals(Perm.NOT_RUNNING, PermCheck.a11y(listed = true, running = false))
        assertEquals(Perm.OFF, PermCheck.a11y(listed = false, running = false))
    }

    @Test fun usageAccessDefaultModeFallsBackToThePermission() {
        val allowed = 0; val ignored = 1; val errored = 2; val default = 3
        assertTrue(PermCheck.usageAllowed(allowed, permissionGranted = false))
        assertTrue(PermCheck.usageAllowed(default, permissionGranted = true))
        assertFalse(PermCheck.usageAllowed(default, permissionGranted = false))
        assertFalse(PermCheck.usageAllowed(ignored, permissionGranted = true))    // turned off by hand beats the grant
        assertFalse(PermCheck.usageAllowed(errored, permissionGranted = true))
    }

    @Test fun notificationsNeedThePermissionFromAndroid13() {
        assertTrue(PermCheck.notificationsOn(32, permissionGranted = false, appEnabled = true))
        assertTrue(PermCheck.notificationsOn(35, permissionGranted = true, appEnabled = true))
        assertFalse(PermCheck.notificationsOn(35, permissionGranted = false, appEnabled = true))
        assertFalse(PermCheck.notificationsOn(35, permissionGranted = true, appEnabled = false))
        assertFalse(PermCheck.notificationsOn(30, permissionGranted = true, appEnabled = false))
    }

    @Test fun siteFilterStates() {
        assertNull(PermCheck.vpn(wanted = false, running = false))
        assertNull(PermCheck.vpn(wanted = false, running = true))
        assertEquals(Perm.ON, PermCheck.vpn(wanted = true, running = true))
        assertEquals(Perm.NOT_RUNNING, PermCheck.vpn(wanted = true, running = false))
    }
}
