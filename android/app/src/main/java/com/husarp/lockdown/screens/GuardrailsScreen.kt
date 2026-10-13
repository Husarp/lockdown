package com.husarp.lockdown.screens

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.PowerManager
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.husarp.lockdown.block.BlockService
import com.husarp.lockdown.data.GuardrailsEdit
import com.husarp.lockdown.data.Store
import com.husarp.lockdown.engine.AntiBypass
import com.husarp.lockdown.engine.AntiBypassCfg
import com.husarp.lockdown.engine.Emergency
import com.husarp.lockdown.engine.EmergencyTier
import com.husarp.lockdown.engine.ItemType
import com.husarp.lockdown.engine.Pause
import com.husarp.lockdown.engine.Window
import com.husarp.lockdown.engine.Rules
import com.husarp.lockdown.engine.Perm
import com.husarp.lockdown.guard.AdminReceiver
import com.husarp.lockdown.guard.Perms
import com.husarp.lockdown.guard.rememberFresh
import com.husarp.lockdown.guard.rememberNowMs
import com.husarp.lockdown.guard.rememberTick
import com.husarp.lockdown.guard.TrustedTime
import com.husarp.lockdown.guard.rememberGuard
import com.husarp.lockdown.ui.Card
import com.husarp.lockdown.ui.Kinds
import com.husarp.lockdown.ui.SectionCard
import com.husarp.lockdown.ui.theme.Hue
import androidx.compose.material.icons.filled.HealthAndSafety
import androidx.compose.material.icons.filled.Password
import androidx.compose.material.icons.filled.ToggleOn
import com.husarp.lockdown.ui.theme.tone
import com.husarp.lockdown.ui.Chip
import com.husarp.lockdown.ui.SectionLabel
import com.husarp.lockdown.ui.SwitchRowInline
import com.husarp.lockdown.ui.HairlineSpacer
import com.husarp.lockdown.ui.SaveBar
import com.husarp.lockdown.ui.StepListEditor
import com.husarp.lockdown.ui.UnsavedChanges
import com.husarp.lockdown.ui.rememberDraft
import java.time.Duration
import java.time.LocalDateTime

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun GuardrailsScreen(onBack: () -> Unit = {}) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val guard = rememberGuard()
    val ctx = LocalContext.current
    val cs = MaterialTheme.colorScheme
    val ex = com.husarp.lockdown.ui.theme.LockdownTheme.extra
    var pickUnlock by remember { mutableStateOf(false) }
    val nowMs = rememberNowMs(1000)      // (ticks only while the app is in front)
    val now = remember(nowMs) { TrustedTime.local(ctx) }     // trusted time: the clock set forward or back changes nothing
    val ab = cfg.antibypass
    // Read again on every return to the app (and a moment after), so a permission just granted in Android shows.
    val fresh = rememberFresh()
    val a11y = remember(fresh) { Perms.a11y(ctx) }
    val battery = remember(fresh) { batteryUnrestricted(ctx) }
    val admin = remember(fresh) { isAdmin(ctx) }
    val grayOk = remember(fresh) { com.husarp.lockdown.remind.Grayscale.canWrite(ctx) }

    // The challenge, emergency and switch settings are a draft (like the PC): nothing is in effect until Save, which
    // asks for the challenge once if anything in the whole draft loosens - so turning a challenge on and setting it
    // up (your own phrase, the hours, a cool-off length) is one free change. Pause, Unlock and Use unlock act at once.
    val d = rememberDraft(GuardrailsEdit.of(cfg))
    val e = d.value
    val dab = e.ab
    fun setAb(f: (AntiBypassCfg) -> AntiBypassCfg) = d.update { it.copy(ab = f(it.ab)) }
    val ownPhrase = dab.useOwn          // "My own phrase" picked ("A random phrase" keeps it saved)
    var ownText by remember(d.resets) { mutableStateOf(dab.customPhrase) }
    val steps = dab.stepList                                  // the challenge list as edited (an unlisted config reads as one phrase step)

    fun save(then: () -> Unit) {
        val new = d.value
        val problem = new.problem() ?: when {
            !new.ab.phrase -> null
            else -> com.husarp.lockdown.engine.Challenges.validate(new.ab.stepList) ?: when {
                new.ab.stepList.any { it.kind == com.husarp.lockdown.engine.Challenges.PHRASE } &&
                    new.ab.useOwn && new.ab.customPhrase.isEmpty() -> "Type your own phrase, or pick a random one."
                else -> null
            }
        }
        if (problem != null) { Toast.makeText(ctx, problem, Toast.LENGTH_LONG).show(); return }
        val old = GuardrailsEdit.of(Store.config)
        guard(new.looser(old)) {
            Store.update { new.applyTo(it) }
            val upOn = new.guardrails.uninstallProtection
            if (upOn && !old.guardrails.uninstallProtection && !isAdmin(ctx))
                ctx.startActivity(adminIntent(ctx).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            if (!upOn && old.guardrails.uninstallProtection) runCatching { dpm(ctx).removeActiveAdmin(admin(ctx)) }
            d.clear()
            then()
        }
    }
    UnsavedChanges(d.dirty, onBack, ::save, d::clear)
    // Fix (Reliability) turns uninstall protection on at once: a draft being edited follows it
    LaunchedEffect(cfg.guardrails.uninstallProtection) { d.update { it.copy(guardrails = it.guardrails.copy(uninstallProtection = cfg.guardrails.uninstallProtection)) } }
    // Sections: the index, or one section's page, all on the one draft (one Save, one challenge). Back from a section
    // goes to the index without asking (composed after UnsavedChanges, so it wins).
    var section by remember { mutableStateOf<String?>(null) }
    androidx.activity.compose.BackHandler(enabled = section != null) { section = null }
    val changed = remember(e, d.saved) { e.changedSections(d.saved) }
    val link by com.husarp.lockdown.link.IslandLink.mainState.collectAsStateWithLifecycle()
    val greyNeeded = (cfg.settings.bedtimeGrayscale || com.husarp.lockdown.engine.BlockMethod.greyUsed(cfg.items, cfg.groups)) &&
        !com.husarp.lockdown.remind.Grayscale.otherProfile(ctx)      // only an adb command can fix it - Fix copies it
    val islandShown = link.state.isNotEmpty() && !com.husarp.lockdown.remind.Grayscale.otherProfile(ctx)   // Fix opens Lockdown in Island
    val islandOk = remember(fresh, link) { com.husarp.lockdown.link.IslandLink.healthy(ctx) }
    val failing = listOfNotNull(
        if (a11y != Perm.ON) "App blocking not running" else null,
        if (!battery) "Battery restricted" else null,
        if (!admin) "Uninstall protection off" else null,
        if (greyNeeded && !grayOk) "Grayscale permission missing" else null,
        if (islandShown && !islandOk) "Island not checking in" else null,
    )

    Column(Modifier.fillMaxSize()) {
    section?.let { sec -> com.husarp.lockdown.ui.EditorHeader(GUARD_SECTIONS.getValue(sec), onBack = { section = null }) }
    Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        if (section == null) {
        // Unlock box (like the PC): while the challenge is on, pass it once to open a short window where loosening
        // edits save without re-asking each time.
        if (AntiBypass.active(ab)) {
            val until = AntiBypass.unlockedUntil(ab, now)
            if (until != null) {
                val secs = Duration.between(now, until).seconds.coerceAtLeast(0)
                Card(color = ex.successContainer, contentColor = ex.onSuccessContainer) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("Unlocked for editing", style = MaterialTheme.typography.titleSmall)
                            Text("%d:%02d left — changes save without the challenge".format(secs / 60, secs % 60), style = MaterialTheme.typography.bodySmall)
                        }
                        OutlinedButton(onClick = { Store.update { it.copy(antibypass = AntiBypass.lock(it.antibypass)) } }) { Text("Lock now") }
                    }
                }
            } else {
                val status = AntiBypass.status(ab, now)
                Card(color = cs.primaryContainer, contentColor = cs.onPrimaryContainer) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("Locked", style = MaterialTheme.typography.titleSmall)
                            Text(if (status == "closed") "Only inside your allowed hours" else "Unlock to make changes without re-asking each time",
                                style = MaterialTheme.typography.bodySmall)
                        }
                        Button(onClick = { guard(true) {} }, enabled = status != "closed") { Text("Unlock") }
                    }
                }
            }
        }

        SectionLabel("Pause my blocks")
        PauseCard(now, guard)

        val warnDot = ex.warning
        SectionCard(Icons.Filled.HealthAndSafety, Hue.GREEN, "Reliability",
            if (failing.isEmpty()) "All good" else failing.first() + if (failing.size > 1) " · ${failing.size - 1} more" else "",
            alert = failing.isNotEmpty(), dot = if (failing.isNotEmpty()) cs.error else null) { section = "reliability" }
        SectionCard(Icons.Filled.Password, Hue.VIOLET, "Before loosening (challenge)", challengeSummary(dab),
            dot = if ("challenge" in changed) warnDot else null) { section = "challenge" }
        run {
            val em = cfg.emergency
            val tiers = em.tierList()
            val left = tiers.map { it to Emergency.usesLeft(cfg.allUses(), it, now, cfg.clock()) }
            SectionCard(Kinds.EMERGENCY.icon, Kinds.EMERGENCY.hue, "Emergency unlock", when {
                    !em.enabled -> "Off"
                    tiers.isEmpty() -> "No kinds set"
                    tiers.size == 1 -> left[0].let { (t, u) -> "${u.left} of ${u.allowed} left ${Emergency.PERIODS[t.per]} · ${t.minutes} min" }
                    else -> "${tiers.size} kinds · ${left.sumOf { it.second.left }} left"
                }, dot = if ("emergency" in changed) warnDot else null,
                trailing = {
                    val pink = tone(Kinds.EMERGENCY.hue)
                    Button(onClick = { pickUnlock = true }, enabled = em.enabled && left.any { it.second.left > 0 },
                        colors = androidx.compose.material3.ButtonDefaults.buttonColors(containerColor = pink.container, contentColor = pink.onContainer)) { Text("Use") }
                }) { section = "emergency" }
        }
        SectionCard(Icons.Filled.ToggleOn, Hue.SLATE, "Switches",
            listOfNotNull(if (e.guardrails.persist) "Keep running" else null, if (e.guardrails.trustedTime) "Trusted time" else null,
                if (e.guardrails.uninstallProtection && admin) "Uninstall protection" else null,
                if (e.guardrails.helperAlert) "Island alert" else null).joinToString(" · ").ifEmpty { "All off" },
            dot = if ("switches" in changed) warnDot else null) { section = "switches" }
        }

        if (section == "reliability") {
        Card(color = cs.surfaceContainerLow, padding = androidx.compose.foundation.layout.PaddingValues(0.dp)) {
            HealthRow("App blocking active", a11y == Perm.ON, if (a11y == Perm.NOT_RUNNING) A11Y_NOT_RUNNING else null) {
                ctx.startActivity(BlockService.settingsIntent().addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
            HealthRow("Battery not restricted", battery) { Perms.openBattery(ctx) }
            HealthRow("Uninstall protection", admin) {     // as the switch's on: the switch below then agrees
                Store.update { it.copy(guardrails = it.guardrails.copy(uninstallProtection = true)) }
                ctx.startActivity(adminIntent(ctx).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
            if (greyNeeded) HealthRow("Grayscale permission", grayOk) { copyGrant(ctx) }
            if (islandShown) HealthRow("Island helper checking in", islandOk) { com.husarp.lockdown.link.IslandLink.openOtherProfile(ctx) }
        }
        }

        if (section == null) {
        // A reinstall or a reset to get out of the rules shows here as a fresh date (on the index: always in sight).
        val installed = remember { runCatching { ctx.packageManager.getPackageInfo(ctx.packageName, 0).firstInstallTime }.getOrDefault(0L) }
        val linkedAt = com.husarp.lockdown.link.IslandLink.linkedAt()
        Text("Installed ${dateTime(installed)}" + when {
            linkedAt > 0 -> " · Island linked ${dateTime(linkedAt)}"
            com.husarp.lockdown.link.IslandLink.mainState.value.state.isNotEmpty() -> " · Island linked (by an older version, time not kept)"
            else -> ""
        }, style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
        }

        if (section == "challenge") {
        Card(color = cs.surfaceContainerLow) {
            Text("Tightening a rule never asks. Loosening needs:", style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Chip("Ask a challenge", dab.phrase) { setAb { it.copy(phrase = !it.phrase) } }
                Chip("Only in hours", dab.hours) { setAb { it.copy(hours = !it.hours, windows = it.windows.ifEmpty { listOf(DEFAULT_HOURS) }) } }
                Chip("Cool-off", dab.waitMin > 0) {
                    setAb { it.copy(waitMin = if (it.waitMin > 0) 0 else d.saved.ab.waitMin.takeIf { m -> m > 0 } ?: DEFAULT_WAIT) }
                }
            }
            if (dab.phrase) {
                Spacer(Modifier.height(14.dp))
                Text("Steps, done top to bottom", style = MaterialTheme.typography.titleSmall)
                Spacer(Modifier.height(8.dp))
                StepListEditor(steps, onChange = { l -> setAb { c -> com.husarp.lockdown.ui.StepEdit.cfgWith(c, l, d.saved.ab) } },
                    phrase = AntiBypass.phraseOpts(dab), newPhraseLength = dab.length) {
                    PhraseExtras(dab, ownText, { ownText = it }) { f -> setAb(f) }
                }
                Text("Adding a step, a higher level or more times, or moving steps is free. Removing a step, a lower level, fewer times " +
                    "or another kind needs the challenge.",
                    Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
            if (dab.waitMin > 0) {
                Spacer(Modifier.height(14.dp))
                Text("Cool-off", style = MaterialTheme.typography.titleSmall)
                Spacer(Modifier.height(6.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    for (m in (WAIT_OPTIONS + dab.waitMin).distinct().sorted())
                        Chip("$m min", dab.waitMin == m) { setAb { it.copy(waitMin = m) } }
                }
                Text("The change goes through this long after the challenge is passed, with the app left open.",
                    Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
            if (dab.hours) {
                Spacer(Modifier.height(14.dp))
                Text("Loosening allowed only", style = MaterialTheme.typography.titleSmall)
                HoursEditor(dab.windows) { w -> setAb { it.copy(windows = w) } }
            }
        }
        }

        if (section == "emergency") {
        Card(color = cs.surfaceContainer) {
            val em = cfg.emergency                              // what's in effect: the meter and Use unlock
            val tiers = em.tierList()
            val uses = cfg.allUses()
            val left = tiers.associate { it.id to Emergency.usesLeft(uses, it, now, cfg.clock()) }
            if (tiers.size == 1) {                              // one kind: as before kinds
                val u = left.getValue(tiers[0].id)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.Bottom) {
                    Text("Unblock chosen items for ${tiers[0].minutes} min", style = MaterialTheme.typography.titleSmall)
                    Text("${u.left} of ${u.allowed} left", style = MaterialTheme.typography.titleMedium)
                }
            } else {
                Text(if (tiers.isEmpty()) "No emergency kinds set" else "Unblock chosen items for a while", style = MaterialTheme.typography.titleSmall)
                for (t in tiers) {
                    val u = left.getValue(t.id)
                    Text("${t.minutes} min · ${u.left} of ${u.allowed} left ${Emergency.PERIODS[t.per]}", style = MaterialTheme.typography.bodyMedium)
                }
            }
            Spacer(Modifier.height(10.dp))
            OutlinedButton(onClick = { pickUnlock = true }, enabled = em.enabled && left.values.any { it.left > 0 }) { Text("Use unlock") }
            Spacer(Modifier.height(14.dp))
            HairlineSpacer()
            Spacer(Modifier.height(8.dp))
            SwitchRowInline("Allow emergency unlocks", e.emergencyOn) { on -> d.update { it.copy(emergencyOn = on) } }
            if (e.emergencyOn) {
                val savedIds = tiers.mapTo(HashSet()) { it.id }
                for (t in e.tiers) key(t.id) {
                    // a kind not saved yet has no meter ("New kind"); a saved one shows its uses as edited
                    TierEditor(t, if (t.id in savedIds) Emergency.usesLeft(uses, t, now, cfg.clock()) else null,
                        onChange = { new -> d.update { it.copy(tiers = it.tiers.map { x -> if (x.id == t.id) new else x }) } },
                        onRemove = { d.update { it.copy(tiers = it.tiers.filter { x -> x.id != t.id }) } })
                }
                if (e.tiers.isEmpty())
                    Text("No kinds - add one", Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
                if (e.tiers.size < Emergency.MAX_TIERS) TextButton(onClick = {
                    d.update { it.copy(tiers = it.tiers + EmergencyTier(Emergency.newId(it.tiers.map { x -> x.id } + savedIds + uses.map { u -> u.tier }), 1, "week", 20)) }
                }) { Text(if (e.tiers.isEmpty()) "+ Add a kind" else "+ Add another kind") }
                Text("Fewer, shorter, per day to per week or month, or removing a kind is free. More, longer, " +
                    "any other period, a new kind or turning it on needs the challenge.",
                    Modifier.padding(top = 6.dp), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
        }
        }

        if (section == "switches") {
        Card(color = cs.surfaceContainerLow) {
            SwitchRowInline("Keep running (restart on kill/boot)", e.guardrails.persist) { on ->
                d.update { it.copy(guardrails = it.guardrails.copy(persist = on)) }
            }
            SwitchRowInline("Trusted time (ignore clock changes)", e.guardrails.trustedTime) { on ->
                d.update { it.copy(guardrails = it.guardrails.copy(trustedTime = on)) }
            }
            TrustedTime.zonePending()?.let { (new, kept) ->
                Text("Time zone changed to $new - Lockdown keeps $kept for 24 hours.",
                    style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            }
            SwitchRowInline("Alert when Lockdown in Island stops (and count it)", e.guardrails.helperAlert) { on ->
                d.update { it.copy(guardrails = it.guardrails.copy(helperAlert = on)) }
            }
            Text("If Lockdown in Island is stopped (Android's Stop or Force stop) its apps are no longer blocked. Main sees that " +
                "after 2 minutes of screen time and keeps an alert up until it is running again. Turning this off needs the challenge.",
                style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
            // Saved: on only while Android really has it (turned off in Android's settings, or its screen cancelled,
            // shows off). Kept on but off in Android, turning it on asks Android again at once (like Fix above).
            val up = e.guardrails.uninstallProtection
            SwitchRowInline("Uninstall protection (device admin)", up && (admin || up != d.saved.guardrails.uninstallProtection)) { on ->
                if (on && up) ctx.startActivity(adminIntent(ctx).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                else d.update { it.copy(guardrails = it.guardrails.copy(uninstallProtection = on)) }
            }
        }
        }
    }
    SaveBar(d.dirty, onDiscard = d::clear) { save {} }
    }

    if (pickUnlock) EmergencyDialog(onDismiss = { pickUnlock = false })
}

private val RESET_FMT = java.time.format.DateTimeFormatter.ofPattern("EEE d MMM HH:mm")

/** Guardrails' sections (key -> title). */
private val GUARD_SECTIONS = mapOf("reliability" to "Reliability", "challenge" to "Before loosening anything",
    "emergency" to "Emergency unlock", "switches" to "Switches")

/** The challenge as set (in the page's draft) in a line: "Random phrase, 40 characters · 5 min cool-off", "3 steps · about
 *  4 min", or "Off". */
private fun challengeSummary(ab: AntiBypassCfg): String = listOfNotNull(
    if (!ab.phrase) null else ab.stepList.let { l ->
        val one = l.singleOrNull()
        if (one != null && one.kind == com.husarp.lockdown.engine.Challenges.PHRASE && one.times == 1) {
            if (AntiBypass.ownPhrase(ab).isNotBlank()) "Your own phrase" else "Random phrase, ${one.level} characters"
        } else "${l.size} ${if (l.size == 1) "step" else "steps"} · ${com.husarp.lockdown.engine.Challenges.takesText(AntiBypass.takesSeconds(ab))}"
    },
    if (ab.hours) "only in set hours" else null,
    if (ab.waitMin > 0) "${ab.waitMin} min cool-off" else null,
).joinToString(" · ").ifEmpty { "Off" }

/** One emergency kind: uses, period and length, and [left] (null for a kind not added yet) as a small meter. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TierEditor(t: EmergencyTier, left: Emergency.Uses?, onChange: (EmergencyTier) -> Unit, onRemove: (() -> Unit)?) {
    val cs = MaterialTheme.colorScheme
    Spacer(Modifier.height(10.dp))
    HairlineSpacer()
    Row(Modifier.padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(if (left == null) "New kind" else "${t.minutes} min · ${t.uses} per ${t.per}", style = MaterialTheme.typography.titleSmall,
            modifier = Modifier.weight(1f))
        if (onRemove != null) IconButton(onClick = onRemove) { Icon(Icons.Filled.Close, "remove this kind") }
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text("Uses", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        IconButton(onClick = { onChange(t.copy(uses = t.uses - 1)) }, enabled = t.uses > 1) { Icon(Icons.Filled.Remove, "fewer") }
        Text("${t.uses}", style = MaterialTheme.typography.titleMedium)
        IconButton(onClick = { onChange(t.copy(uses = t.uses + 1)) }, enabled = t.uses < Emergency.MAX_USES) { Icon(Icons.Filled.Add, "more") }
    }
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        for (p in Emergency.PERIODS.keys) Chip("per $p", t.per == p) { onChange(t.copy(per = p)) }
    }
    Spacer(Modifier.height(8.dp))
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        for (m in (Emergency.MINUTE_OPTIONS + t.minutes).distinct().sorted()) Chip("$m min", t.minutes == m) { onChange(t.copy(minutes = m)) }
    }
    if (left != null) Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        repeat(left.allowed) { i ->                         // one bar per use; the used ones grey
            Box(Modifier.padding(end = 3.dp).size(width = 10.dp, height = 6.dp)
                .background(if (i < left.left) cs.primary else cs.outlineVariant, RoundedCornerShape(3.dp)))
        }
        Text("${left.left} of ${left.allowed} left ${Emergency.PERIODS[t.per]} · resets ${left.reset.format(RESET_FMT)}",
            Modifier.padding(start = 6.dp).weight(1f), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
    }
}

/** The phrase extras every phrase step shares: a random phrase or your own (its text), Numbers and capitals, Table writing. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun PhraseExtras(dab: AntiBypassCfg, ownText: String, onOwnText: (String) -> Unit, setAb: ((AntiBypassCfg) -> AntiBypassCfg) -> Unit) {
    val cs = MaterialTheme.colorScheme
    val own = dab.useOwn
    Column {
        Text("Typed phrase", style = MaterialTheme.typography.titleSmall)
        Spacer(Modifier.height(6.dp))
        // random or your own, chosen before Save: nothing is locked in half-set
        com.husarp.lockdown.ui.Segmented(listOf("A random phrase", "My own phrase"), if (own) 1 else 0) { i -> setAb { it.copy(useOwn = i == 1) } }
        Spacer(Modifier.height(10.dp))
        if (own) {
            OutlinedTextField(ownText, { v -> onOwnText(v); setAb { it.copy(customPhrase = cleanPhrase(v)) } },
                label = { Text("Your own phrase") }, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(10.dp))
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (!own) Chip("Numbers and capitals", dab.complex) { setAb { it.copy(complex = !it.complex) } }
            Chip("Table writing", dab.grid) { setAb { it.copy(grid = !it.grid) } }
        }
        Text(
            (if (own) "Your own phrase, typed exactly each time (no pasting) - it replaces the random one in every phrase step. Once saved, changing it or picking it again needs the challenge; a random phrase keeps it saved."
                else "A random phrase" + (if (dab.complex) ", with numbers and capitals" else "") + ".") +
                if (dab.grid) " Typed word by word into a 3×3 grid (no paste/macros)." else "",
            Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant,
        )
    }
}

private val WAIT_OPTIONS = listOf(1, 2, 5, 10, 15, 30, 60)

/**
 * Pause my blocks (PC 0.84.8): every block from your own list off for a while, then back by itself. Starting needs
 * the challenge; Resume blocking never does. It lives in the config, so the Island helper pauses with it.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun PauseCard(now: LocalDateTime, guard: (Boolean, () -> Unit) -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    val cs = MaterialTheme.colorScheme
    var length by remember { mutableStateOf("1 h") }
    var silent by remember { mutableStateOf(false) }
    Card(color = cs.surfaceContainer) {
        val running = Pause.state(cfg.pause, now)
        com.husarp.lockdown.ui.IconTile(Kinds.PAUSE.icon, Kinds.PAUSE.hue, 32.dp)
        Spacer(Modifier.height(10.dp))
        if (running != null) {
            Text("Your blocks are paused until ${hhmm(LocalDateTime.parse(running.until))}" +
                if (running.silent) " - alerts are silenced." else ".", style = MaterialTheme.typography.titleSmall, color = cs.primary)
            Spacer(Modifier.height(10.dp))
            Button(onClick = { resumeBlocks(ctx) }) { Text("Resume blocking") }
        } else {
            Text("Your blocks are on.", style = MaterialTheme.typography.titleSmall)
            Spacer(Modifier.height(8.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                for (label in Pause.DURATIONS.keys) Chip(label, length == label) { length = label }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = silent, onCheckedChange = { silent = it })
                Text("Silence alerts too (bedtime, breaks and reminders)", style = MaterialTheme.typography.bodyMedium)
            }
            OutlinedButton(onClick = {
                val minutes = Pause.DURATIONS[length]
                guard(true) {
                    val at = TrustedTime.local(ctx)        // the time it is once the challenge is done
                    Store.update { it.copy(pause = Pause.start(at, minutes, silent, it.resetTime())) }
                    val end = Pause.until(Store.state.value.pause, at)
                    com.husarp.lockdown.data.BypassLog.record(ctx, com.husarp.lockdown.engine.Bypass.PAUSE,
                        end?.let { java.time.Duration.between(at, it).seconds.toInt() } ?: 0)
                    if (silent) com.husarp.lockdown.remind.Grayscale.sync(ctx)
                }
            }) { Text("Pause my blocks") }
        }
        Text("Unblocks every app and site on your list for a while - hours, time limits, opening limits, modes, temporary " +
            "and permanent blocks. The protection lists, blocked words and SafeSearch keep working, and the time you use " +
            "still counts toward your limits. It ends by itself (Rest of the day: at the next reset time). Starting it " +
            "needs the challenge; resuming early never does. No emergency unlock is used.",
            Modifier.padding(top = 8.dp), style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
    }
}

/** The slim "Blocking paused until HH:MM · Resume now" banner over every page while a pause runs (PC 0.84.8). */
@Composable
fun PauseBanner() {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    val tick = rememberTick(5000)      // a pause that runs out takes the banner with it
    val until = remember(cfg.pause, tick) { Pause.until(cfg.pause, TrustedTime.local(ctx)) } ?: return
    val slate = tone(Kinds.PAUSE.hue)          // paused is slate
    androidx.compose.material3.Surface(color = slate.container, contentColor = slate.onContainer, modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Icon(Kinds.PAUSE.icon, null, tint = slate.accent, modifier = Modifier.size(20.dp))
            Text("Blocking paused until ${hhmm(until)}", style = MaterialTheme.typography.labelLarge, modifier = Modifier.weight(1f))
            TextButton(onClick = { resumeBlocks(ctx) }) { Text("Resume now") }
        }
    }
}

/** "Resume now": blocking back on at once - always free. */
fun resumeBlocks(ctx: Context) {
    Store.update { it.copy(pause = null) }
    com.husarp.lockdown.remind.Grayscale.sync(ctx)
}

private fun hhmm(t: LocalDateTime) = "%02d:%02d".format(t.hour, t.minute)

/** The challenge's "only in hours" windows, edited in the page's draft (any change of them, or no longer needing
 *  them, is loosening - one challenge on Save for the whole edit). Each window needs a day: one with none could
 *  never let a change through. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun HoursEditor(windows: List<Window>, onChange: (List<Window>) -> Unit) {
    fun set(i: Int, w: Window?) = onChange(windows.toMutableList().also { if (w == null) it.removeAt(i) else it[i] = w })
    val days = listOf("M", "T", "W", "T", "F", "S", "S")
    windows.forEachIndexed { i, w ->
        Spacer(Modifier.height(10.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            days.forEachIndexed { d, l -> Chip(l, d in w.days) { set(i, w.copy(days = if (d in w.days) w.days - d else (w.days + d).sorted())) } }
        }
        Spacer(Modifier.height(6.dp))
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            TimeField("From", w.start, Modifier.weight(1f)) { set(i, w.copy(start = it)) }
            TimeField("To", w.end, Modifier.weight(1f)) { set(i, w.copy(end = it)) }
            if (windows.size > 1) IconButton(onClick = { set(i, null) }) { Icon(Icons.Filled.Close, "remove these hours") }
        }
        if (w.days.isEmpty()) Text("Pick at least one day.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
    }
    TextButton(onClick = { onChange(windows + DEFAULT_HOURS) }) { Text("+ Add time window") }
}

private val DEFAULT_HOURS = Window(listOf(6), "18:00", "20:00")     // Sunday 18:00-20:00, as AntiBypassCfg
private const val DEFAULT_WAIT = 5

/** Your own phrase as saved and typed: words with single spaces. */
private fun cleanPhrase(s: String) = s.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")

/** "Use unlock": tick what to free (and/or pause the alerts); with several kinds, pick how long - the shortest with
 *  a use left is picked first. One use of that kind, checked again as it's saved. */
@Composable
internal fun EmergencyDialog(onDismiss: () -> Unit) {
    val cfg by Store.state.collectAsStateWithLifecycle()
    val ctx = LocalContext.current
    val picked = remember { mutableStateOf(setOf<String>()) }
    var alerts by remember { mutableStateOf(false) }
    val now = remember(cfg) { TrustedTime.local(ctx) }
    val tiers = cfg.emergency.tierList()
    val left = tiers.associate { it.id to Emergency.usesLeft(cfg.allUses(), it, now, cfg.clock()) }
    var tierId by remember { mutableStateOf(Emergency.shortest(tiers, cfg.allUses(), now, cfg.clock())?.id) }
    val tier = tiers.firstOrNull { it.id == tierId }
    val none = cfg.emergency.enabled && left.values.none { it.left > 0 }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Emergency unlock") },
        text = {
            // Never a permanently blocked app or site (its own rule or a group's): the emergency can't free those (PC 0.84.7).
            val items = remember(cfg.items, cfg.groups) {
                cfg.items.filter { !it.disabled && !Rules.permanent(Rules.effectiveRules(it, cfg.groups)) }
                    .sortedWith(compareBy({ it.type != ItemType.APP }, { it.name.lowercase() }))
            }
            Column(Modifier.verticalScroll(rememberScrollState())) {
                if (tiers.size == 1) Text("Unblock apps or sites for ${tiers[0].minutes} minutes (one use, however many you tick). " +
                    "Permanent blocks and protection lists stay on. Time you use still counts toward your limits.")
                else {
                    Text("Unblock apps or sites (one use of the kind you pick, however many you tick). " +
                        "Permanent blocks and protection lists stay on. Time you use still counts toward your limits.")
                    Text("How long", Modifier.padding(top = 10.dp), style = MaterialTheme.typography.titleSmall)
                    for (t in tiers) {
                        val u = left.getValue(t.id)
                        Row(Modifier.fillMaxWidth().selectable(tierId == t.id, enabled = u.left > 0, role = Role.RadioButton) { tierId = t.id },
                            verticalAlignment = Alignment.CenterVertically) {
                            RadioButton(selected = tierId == t.id, onClick = null, enabled = u.left > 0)
                            Text(if (u.left > 0) "${t.minutes} min — ${u.left} of ${u.allowed} left ${Emergency.PERIODS[t.per]}"
                                else "${t.minutes} min — none left · resets ${u.reset.format(RESET_FMT)}",
                                Modifier.padding(start = 8.dp),
                                color = if (u.left > 0) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
                if (none) left.values.minOfOrNull { it.reset }?.let {
                    Text("No emergency unlocks left - next one ${it.format(RESET_FMT)}", Modifier.padding(top = 8.dp),
                        color = MaterialTheme.colorScheme.error)
                }
                if (items.isEmpty()) Text("Nothing the emergency can unlock.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                items.forEach { item ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = item.id in picked.value, onCheckedChange = { on -> picked.value = if (on) picked.value + item.id else picked.value - item.id })
                        com.husarp.lockdown.ui.ItemIcon(item.type, item.target, item.name, 28.dp)
                        Text(if (item.type == ItemType.SITE) "${item.name} · site" else item.name, Modifier.padding(start = 10.dp))
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = alerts, onCheckedChange = { alerts = it })
                    Text("Pause bedtime and break alerts (and bedtime grayscale)")
                }
            }
        },
        confirmButton = {
            if (!none) TextButton(enabled = tier != null && (picked.value.isNotEmpty() || alerts), onClick = {
                val at = TrustedTime.local(ctx)                   // trusted time: a clock set forward gives no longer unlock
                // checked against the config as it's saved: a kind spent or removed meanwhile isn't used
                var r = Emergency.Started(error = "No emergency unlocks left.")
                Store.update { c -> c.withEmergency(tier!!.id, picked.value.toList(), alerts, at).also { r = it.second }.first }
                if (r.use == null) Toast.makeText(ctx, r.error, Toast.LENGTH_LONG).show()
                else if (alerts) com.husarp.lockdown.remind.Grayscale.sync(ctx)
                onDismiss()
            }) { Text(if (tiers.size > 1 && tier != null) "Unlock for ${tier.minutes} min" else "Unlock") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

/** [note]: why it's not on although Android shows it allowed ("allowed but not running"). */
@Composable
private fun HealthRow(label: String, ok: Boolean, note: String? = null, onFix: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    val ex = com.husarp.lockdown.ui.theme.LockdownTheme.extra
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Icon(if (ok) Icons.Filled.Check else Icons.Filled.Close, null, tint = if (ok) ex.success else cs.error, modifier = Modifier.size(20.dp))
        Column(Modifier.weight(1f)) {
            Text(label, style = MaterialTheme.typography.titleSmall)
            if (note != null) Text(note, style = MaterialTheme.typography.bodySmall, color = cs.error)
        }
        if (!ok) TextButton(onClick = onFix) { Text("Fix") }
    }
}

/** App blocking turned on in Android's accessibility settings, but Android isn't running it (it crashed or was stopped). */
internal const val A11Y_NOT_RUNNING = "On in Android, but not running. Turn Lockdown off and on again in Accessibility."

private fun batteryUnrestricted(ctx: Context) =
    (ctx.getSystemService(Context.POWER_SERVICE) as PowerManager).isIgnoringBatteryOptimizations(ctx.packageName)

private fun dpm(ctx: Context) = ctx.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
internal fun dateTime(ms: Long): String =
    if (ms <= 0) "?" else java.time.Instant.ofEpochMilli(ms).atZone(java.time.ZoneId.systemDefault()).format(java.time.format.DateTimeFormatter.ofPattern("d MMM yyyy, HH:mm"))

private fun admin(ctx: Context) = ComponentName(ctx, AdminReceiver::class.java)
private fun isAdmin(ctx: Context) = dpm(ctx).isAdminActive(admin(ctx))
private fun adminIntent(ctx: Context) = Intent(DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN)
    .putExtra(DevicePolicyManager.EXTRA_DEVICE_ADMIN, admin(ctx))
    .putExtra(DevicePolicyManager.EXTRA_ADD_EXPLANATION, "Lockdown uses device admin so it can't be uninstalled while a block is active.")
