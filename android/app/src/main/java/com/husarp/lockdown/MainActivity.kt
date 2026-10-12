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
import androidx.compose.material3.NavigationBarItemDefaults
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
import androidx.navigation.NavType
import androidx.navigation.navArgument
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.husarp.lockdown.screens.BlockCalendarScreen
import com.husarp.lockdown.screens.BlockingScreen
import com.husarp.lockdown.screens.GuardrailsScreen
import com.husarp.lockdown.screens.HomeScreen
import com.husarp.lockdown.screens.InsightsScreen
import com.husarp.lockdown.screens.ModesScreen
import com.husarp.lockdown.screens.NetLogScreen
import com.husarp.lockdown.screens.ReminderScreen
import com.husarp.lockdown.screens.SettingsScreen
import com.husarp.lockdown.ui.theme.Hue
import com.husarp.lockdown.ui.theme.LockdownTheme
import com.husarp.lockdown.ui.theme.tone

/** The app's sections, each with its colour. The first five are the bottom bar; Guardrails and Settings are top-bar icons. */
enum class Dest(val route: String, val label: String, val icon: ImageVector, val hue: Hue, val inBottomBar: Boolean = true) {
    HOME("home", "Home", Icons.Filled.Home, Hue.ORANGE),
    BLOCKING("blocking", "Blocking", Icons.Filled.Block, Hue.RED),
    MODES("modes", "Modes", Icons.Filled.Tune, Hue.BLUE),
    INSIGHTS("insights", "Insights", Icons.Filled.BarChart, Hue.TEAL),
    REMINDERS("reminders", "Reminders", Icons.Filled.Notifications, Hue.INDIGO),
    GUARDRAILS("guardrails", "Guardrails", Icons.Filled.Shield, Hue.VIOLET, inBottomBar = false),
    SETTINGS("settings", "Settings", Icons.Filled.Settings, Hue.SLATE, inBottomBar = false),
}

class MainActivity : ComponentActivity() {
    companion object {
        const val EXTRA_TURN_OFF = "turn_off"
        const val ACTION_TURN_OFF = "com.husarp.lockdown.TURN_OFF"
    }

    // The Quick Settings tile asked to turn Lockdown off: App() runs it through the challenge.
    private val askOff = mutableStateOf(false)
    private var iconWatch: kotlinx.coroutines.Job? = null

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
        // Island helper: (re)start its service, or just check in while main covers Island - also how main's "Fix" wakes it up.
        if (com.husarp.lockdown.link.IslandLink.isHelper) runCatching { com.husarp.lockdown.link.IslandLink.helperStart(this) }
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
        runCatching { com.husarp.lockdown.remind.Grayscale.sync(this) }   // a grey screen left on outside bedtime clears on open
        // Site icons for the sites on your lists: fetched while the app is in front, when it opens and when the lists change.
        iconWatch?.cancel()
        iconWatch = if (com.husarp.lockdown.link.IslandLink.isHelper) null else com.husarp.lockdown.ui.SiteIcons.watch(this)
    }

    // Rotation, dark mode, split screen resizing don't recreate the activity (the manifest's configChanges), so a
    // page's unsaved edits stay; the bars follow a dark-mode switch here.
    override fun onConfigurationChanged(newConfig: android.content.res.Configuration) {
        super.onConfigurationChanged(newConfig)
        enableEdgeToEdge()
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
        iconWatch?.cancel()
        iconWatch = null
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
    com.husarp.lockdown.ui.UnsavedDialog()
    val current by nav.currentBackStackEntryAsState()
    val route = current?.destination?.route?.substringBefore("?")
    val here = Dest.entries.firstOrNull { it.route == route } ?: Dest.HOME

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(when (route) { "blockcal" -> "Block calendar"; "activity" -> "Activity log"; else -> here.label }) },
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
                    val t = tone(d.hue)
                    NavigationBarItem(
                        selected = (when (route) { "blockcal" -> Dest.BLOCKING; "activity" -> Dest.INSIGHTS; else -> here }) == d,
                        onClick = { nav.go(d) },
                        icon = { Icon(d.icon, d.label) },
                        label = { Text(d.label) },
                        colors = NavigationBarItemDefaults.colors(indicatorColor = t.container, selectedIconColor = t.onContainer,
                            selectedTextColor = t.accent),
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
                    onOpenInsights = { nav.go(Dest.INSIGHTS) },
                    onOpenReminders = { nav.go(Dest.REMINDERS) },
                    onOpenSettings = { section -> nav.goSettings(section) },
                )
            }
            composable(Dest.BLOCKING.route) { BlockingScreen(onOpenNetLog = { nav.navigate("netlog") }, onOpenCalendar = { nav.navigate("blockcal") }) }
            composable("blockcal") { BlockCalendarScreen() }
            composable("netlog") { NetLogScreen() }
            composable(Dest.MODES.route) { ModesScreen() }
            composable(Dest.INSIGHTS.route) { InsightsScreen(onOpenActivity = { nav.navigate("activity") }) }
            composable("activity") { com.husarp.lockdown.screens.ActivityLogScreen() }
            composable(Dest.REMINDERS.route) { ReminderScreen() }
            composable(Dest.GUARDRAILS.route) { GuardrailsScreen(onBack = { nav.popBackStack() }) }
            composable(Dest.SETTINGS.route + "?section={section}",
                arguments = listOf(navArgument("section") { type = NavType.StringType; nullable = true })) { entry ->
                SettingsScreen(onBack = { nav.popBackStack() }, start = entry.arguments?.getString("section"))
            }
        }
        }
    }
}

/** Navigate to a top-level section without stacking duplicates; from a page with unsaved edits, Save / Discard first. */
private fun androidx.navigation.NavController.go(dest: Dest) {
    if (currentDestination?.route?.substringBefore("?") == dest.route) return
    com.husarp.lockdown.ui.Unsaved.leave {
        navigate(dest.route) {
            popUpTo(Dest.HOME.route) { saveState = true }
            launchSingleTop = true
            restoreState = dest != Dest.SETTINGS    // (a restored Settings would reopen the section a link last opened)
        }
    }
}

/** Settings, opened at one of its sections ([section] null: the index), e.g. Home's "Island · Set up". */
private fun androidx.navigation.NavController.goSettings(section: String?) {
    com.husarp.lockdown.ui.Unsaved.leave {
        navigate(Dest.SETTINGS.route + (section?.let { "?section=$it" } ?: "")) {
            popUpTo(Dest.HOME.route) { saveState = true }
            launchSingleTop = true
        }
    }
}
