package com.husarp.lockdown

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.filled.BarChart
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.husarp.lockdown.screens.BlockingScreen
import com.husarp.lockdown.screens.GuardrailsScreen
import com.husarp.lockdown.screens.HomeScreen
import com.husarp.lockdown.screens.InsightsScreen
import com.husarp.lockdown.screens.ModesScreen
import com.husarp.lockdown.screens.NetLogScreen
import com.husarp.lockdown.screens.ReminderScreen
import com.husarp.lockdown.screens.SettingsScreen
import com.husarp.lockdown.ui.theme.LockdownTheme

/** The app's sections. The first five are the bottom bar; Guardrails and Settings are top-bar icons. */
enum class Dest(val route: String, val label: String, val icon: ImageVector, val inBottomBar: Boolean = true) {
    HOME("home", "Home", Icons.Filled.Home),
    BLOCKING("blocking", "Blocking", Icons.Filled.Block),
    MODES("modes", "Modes", Icons.Filled.Tune),
    INSIGHTS("insights", "Insights", Icons.Filled.BarChart),
    REMINDERS("reminders", "Reminders", Icons.Filled.Notifications),
    GUARDRAILS("guardrails", "Guardrails", Icons.Filled.Shield, inBottomBar = false),
    SETTINGS("settings", "Settings", Icons.Filled.Settings, inBottomBar = false),
}

class MainActivity : ComponentActivity() {
    companion object {
        const val EXTRA_TURN_OFF = "turn_off"
        const val ACTION_TURN_OFF = "com.husarp.lockdown.TURN_OFF"
    }

    // The Quick Settings tile asked to turn Lockdown off: App() runs it through the challenge.
    private val askOff = mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) takeTurnOff(intent)
        // if the filter should be on but the process was killed, bring it back (consent already granted)
        val cfg = com.husarp.lockdown.data.Store.config
        if (cfg.settings.siteFilterOn && cfg.guardrails.persist &&
            !com.husarp.lockdown.vpn.LockdownVpn.active &&
            com.husarp.lockdown.vpn.LockdownVpn.prepare(this) == null
        ) {
            com.husarp.lockdown.vpn.LockdownVpn.start(this)
        }
        // A fresh start (not a rotation): the update banner may show again, and an installed update's APK goes.
        if (savedInstanceState == null) com.husarp.lockdown.update.Updates.onAppStart(this)
        // Island helper: (re)start its service - also how main's "Fix" wakes it up.
        if (com.husarp.lockdown.link.IslandLink.isHelper) runCatching { com.husarp.lockdown.link.LinkService.start(this) }
        enableEdgeToEdge()
        setContent {
            val cfg by com.husarp.lockdown.data.Store.state.collectAsStateWithLifecycle()
            val helper by com.husarp.lockdown.link.IslandLink.helper.collectAsStateWithLifecycle()
            // The linked copy in Island has no editing screens: main holds the rules.
            LockdownTheme(pref = cfg.settings.theme) { if (helper) com.husarp.lockdown.screens.HelperScreen() else App(askOff) }
        }
    }

    // Launch and every return to the app: check for updates (at most every 5 min) and carry on an install
    // that was waiting on Android's "install unknown apps" screen.
    override fun onResume() {
        super.onResume()
        com.husarp.lockdown.remind.ReminderRunner.appVisible = true
        com.husarp.lockdown.update.Updates.onResume(this)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        takeTurnOff(intent)
    }

    private fun takeTurnOff(i: Intent?) {
        if (i?.action != ACTION_TURN_OFF || !i.getBooleanExtra(EXTRA_TURN_OFF, false)) return
        if (i.flags and Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY != 0) return    // reopened from Recents: not a new tap
        i.removeExtra(EXTRA_TURN_OFF)
        askOff.value = true
    }

    override fun onPause() {
        com.husarp.lockdown.remind.ReminderRunner.appVisible = false
        com.husarp.lockdown.update.Updates.onPause()
        super.onPause()
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun App(askOff: MutableState<Boolean>) {
    val nav = rememberNavController()
    val guard = com.husarp.lockdown.guard.rememberGuard()
    LaunchedEffect(askOff.value) {
        if (askOff.value) {
            askOff.value = false
            guard(true) { com.husarp.lockdown.data.Store.update { it.copy(enabled = false) } }
        }
    }
    com.husarp.lockdown.screens.BedtimeAsk()
    val current by nav.currentBackStackEntryAsState()
    val route = current?.destination?.route
    val here = Dest.entries.firstOrNull { it.route == route } ?: Dest.HOME

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(here.label) },
                actions = {
                    for (d in listOf(Dest.GUARDRAILS, Dest.SETTINGS)) {
                        IconButton(onClick = { nav.go(d) }) { Icon(d.icon, d.label) }
                    }
                },
            )
        },
        bottomBar = {
            NavigationBar {
                for (d in Dest.entries.filter { it.inBottomBar }) {
                    NavigationBarItem(
                        selected = here == d,
                        onClick = { nav.go(d) },
                        icon = { Icon(d.icon, d.label) },
                        label = { Text(d.label) },
                    )
                }
            }
        },
    ) { pad ->
        androidx.compose.foundation.layout.Column(Modifier.padding(pad)) {
        com.husarp.lockdown.screens.PauseBanner()
        NavHost(nav, startDestination = Dest.HOME.route) {
            composable(Dest.HOME.route) {
                HomeScreen(
                    onOpenBlocking = { nav.go(Dest.BLOCKING) },
                    onOpenModes = { nav.go(Dest.MODES) },
                    onOpenGuardrails = { nav.go(Dest.GUARDRAILS) },
                )
            }
            composable(Dest.BLOCKING.route) { BlockingScreen(onOpenNetLog = { nav.navigate("netlog") }) }
            composable("netlog") { NetLogScreen() }
            composable(Dest.MODES.route) { ModesScreen() }
            composable(Dest.INSIGHTS.route) { InsightsScreen() }
            composable(Dest.REMINDERS.route) { ReminderScreen() }
            composable(Dest.GUARDRAILS.route) { GuardrailsScreen() }
            composable(Dest.SETTINGS.route) { SettingsScreen() }
        }
        }
    }
}

/** Navigate to a top-level section without stacking duplicates. */
private fun androidx.navigation.NavController.go(dest: Dest) {
    if (currentDestination?.route == dest.route) return
    navigate(dest.route) {
        popUpTo(Dest.HOME.route) { saveState = true }
        launchSingleTop = true
        restoreState = true
    }
}
