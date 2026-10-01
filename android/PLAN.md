# Lockdown Mobile — plan & to-do

Companion Android app to Lockdown (Windows). Kotlin + Compose + Material 3 (Material You). Same feature
set as the desktop app, adapted to Android. Target phone: OnePlus 6, Android 15.

## Decisions (from the user)

- Name **Lockdown Mobile**, package `com.husarp.lockdown`, lives in `android/` in the Lockdown repo.
- Site blocking via a local **DNS-filter VPN**. User has no other VPN; warn on Private DNS / VPN clash.
- **Island (work profile):** must be handled. Plan B = a tiny companion app in Island that relays
  app-open events to the main app in the owner profile (main app holds all the rules/UI).
- **Lists:** import from Lockdown as-is; also editable (add / disable / remove), loosening behind the
  challenge.
- **Extras, each a toggle:** pause + daily open-cap (2), bedtime grayscale (4), tile + widgets (5),
  SafeSearch / YouTube Restricted Mode (6), weekly digest (8), emergency unlocks (9), plus home-screen
  widgets showing time used / time left.
- **Anti-bypass / persistence:** device-admin uninstall protection, foreground service that survives
  kill + reboot, battery-optimisation exemption, blocks its own settings during a lock, tamper
  warnings. Strong self-imposed friction — always removable by the owner (the honest ceiling on
  non-rooted Android; no hiding/disguise/true anti-removal).

## Build order

1. **Skeleton** — theme, navigation, 7 sections, docs. **[DONE 0.1.0]**
2. **Insights (screen time)** — UsageStatsManager timeline + per-app totals + range switcher. **[DONE 0.2.0, verified on-device]**
3. **App blocking** — accessibility service + full-screen overlay; forever / schedule / daily limit. **[DONE 0.2.0, verified on-device]**
4. **Site blocking** — DNS-filter VPN + block list + network log. **[DONE 0.2.0, not yet live-tested]** — keyword blocking + list import from Lockdown still TODO.
5. **Guardrails** — challenge, device-admin uninstall protection, persistence + boot receiver. **[DONE 0.2.0]** — tamper watchdog/warnings still TODO.
6. **Reminders / Modes / Updates / Appearance** — **[DONE 0.2.0]** — reminders use a coarse 15-min worker; full bedtime escalation cadence still TODO.
7. **Extras** — widget + Quick Settings tile **[DONE 0.2.0]**. Toggles present for pause-before-open, open-cap, bedtime grayscale, force-SafeSearch, weekly digest, emergency unlocks — **enforcement still TODO** (toggles save but don't act yet).
8. **Island** — **TODO**: test whether the owner-profile accessibility service sees Island app opens; build the companion relay if not.

## Done since 0.2.0
- Site-filter upstream = the network's own DNS (0.2.1); site-filter switch honest + self-heals (0.2.2).
- List import (sites + keywords) from a .txt (0.2.2).
- Keyword blocking via the accessibility service, best-effort for Chromium + Firefox (0.2.2).
- Daily open cap (0.2.2). Bedtime grayscale via WRITE_SECURE_SETTINGS (0.2.2).

## Island findings
- Accessibility is **per-profile**: the owner-profile service can't see Island (user 10) app opens, so app +
  keyword blocking don't reach Island. The **DNS VPN is device-wide**, so **site blocking already covers Island**.
- To block apps in Island: install Lockdown Mobile in user 10 too (needs the user's OK - their rule is
  "never touch Island unless I ask") and share config across profiles, or a companion relay. Not done.

## Done (0.2.3–0.2.5)
- Emergency unlocks, pause-before-open, weekly digest (0.2.3). Installed in Island (0.2.3).
- Force SafeSearch via DNS (0.2.4). Bedtime escalation with tier cadence via the service (0.2.4).
  Tamper watchdog + tappable Home status (0.2.4). Export/import all settings for cross-profile sync (0.2.5).

## Still to do
- **Live on-device verification** of the VPN forwarding fix, SafeSearch, bedtime escalation, and reminders.
- **True cross-profile auto-sync** (would need device-owner; export/import is the manual stand-in for now).
- Nice-to-haves: unit-test the bedtime tier logic (needs Robolectric - it lives on an Android class);
  richer per-app schedule UI; app icons in lists.

## Full PC-parity engine (UI-independent; user confirmed 'all of it')
- [DONE] E1 model + E2 rule engine: all rule types, groups+overrides, LimitClock, item_block, rule_state (tested)
- [DONE] E3 usage/limit accounting: UsageCounter (pure, tested) records foreground time + switches/visits into the (owner,bucket) counters; UsageStore persists them across restarts
- [DONE] E4 protection lists BUNDLED in the app (all 6, no download) - ship curated lists as assets; toggle+allowed+custom
- [DONE] E5 keywords BUILT-IN by default: adult EN/PL ready lists, own words (*/phrase/whole-word/accents), exceptions, SafeSearch+YT
- [DONE] E6 modes: engine (pure, tested) - category + items/groups, by hand/schedule, duration/until/locked, mute, Pomodoro; ModesStore persists list + active state
- [DONE] E7 reminders parity: RemindersEngine (pure, tested) - bedtime(+mode/tiers), breaks(strict/snooze/20-20-20), custom(interval/times/random, days, hours, check, per-day), pacing + back-off
- [DONE] E8 guardrails parity: AntiBypass engine (pure, tested) - challenge (phrase/hours/wait), unlock window + cooling-off, "what loosens a block" detector (rule/item/group/settings/emergency/protection), Emergency uses per day/week. Uninstall protection + trusted time already on-device.
- [DONE] E9 stats parity: Stats engine (pure, tested) - per-app/per-site, active vs idle, sessions, longest focus, categories, timeline, heatmap, switches/visits (short/style), goal + no-unlock streaks. Network log already on-device.
- [DONE] app-block types (close/background/minimize/internet) and site-block types (dns/close/back) - editable per item in the rule editor (stored on Item.blockType; overlay enforcement uses "close" today)
- [DONE] wire the engine into enforcement: BlockService feeds UsageStore + enforces via Enforce (engine rules + active mode); LockdownVpn uses Enforce.site; loosening goes through the anti-bypass challenge
- [DONE] rebuilt all 5 tabs + Guardrails/Settings + block/challenge per android/design/ (new theme: Material 3 orange scheme light+dark, Barlow/Inter bundled, sharp corners) - 0.5.0
- [TODO] install + on-device visual verify (phone was not connected at build time)

## On-device bug-fix round (0.5.1–0.5.3, from the user's testing)
- [DONE 0.5.1] Insights 7/30-day crash (main-thread query → ANR): queries moved off-main.
- [DONE 0.5.2] "Get full list" host switched to jsDelivr (raw.githubusercontent.com blocked by some DNS). NOTE: on the user's enterprise Wi-Fi the *app itself* can't resolve any host (AFWall not granting Lockdown LAN access to the 10.99.99.99 DNS) — not fixable in code; needs AFWall to allow Lockdown like Lexling.
- [DONE 0.5.3] Blocking freeze with 6137 imported items: flat lazy list, per-visible-row status (was groupBy{Live.status} over all items on main). Home/Mode-editor/Group-editor same anti-pattern fixed (audit workflow confirmed 13 hotspots).
- [DONE 0.5.3] Insights: 7/30-day show per-day totals (single query); never sticks on "Loading…"; removed the productive/neutral/distracting bar (no app-role assignment this version).
- [DONE 0.5.3] Work without the VPN: protection lists + site items + keywords enforced in the browser by the accessibility service; VPN optional/off with a warning note.
- [DONE 0.5.3] Emergency unlock only unlocks apps — never sites or protection lists.
- [DONE 0.5.3] Import runs off-main; config stored compact (was 5.4 MB pretty-printed).
- [DONE 0.5.4] Accessibility kept getting disabled: wrapped every callback (crash-proof) + Guardrails battery-optimisation health check (OnePlus kills the bg service). Root cause was OS process-kill + reinstall revoking the grant, not a code crash.
- [DONE 0.5.5] Groups take the full rule set (items + groups share one multi-rule editor: any combination of scheduled/limit/opening/temporary/permanent).
- [DONE 0.5.5] Group member picker shows apps + limitable items, hides the forever-blocked imported bulk, and can add a site by typing.
- [DONE 0.5.5] Keywords page redesigned: add-a-word at top; each list collapsible with its own search; per-word on/off for built-ins (no delete of shipped words); your words editable; exceptions section.
- [DONE 0.5.5] Removing an empty group (no rules, no members) no longer needs the anti-bypass challenge (loosens nothing).
- [DONE 0.5.8] Guardrails "unlock while the challenge is on" box (like the PC): pass the challenge once to open a 5-min window where loosening edits save without re-asking; countdown + "Lock now"; Unlock disabled outside allowed hours.
- [DONE 0.5.9] Fixed the phrase challenge being unpassable when a cool-off was also set (timer never ticked → Confirm stayed disabled). Added a mistype alert on the phrase field, and a "Table writing" option (PC 3×3 grid: random lit cell, tap + type each word, paste/macros ignored).
- [TODO] ReminderScreen RemList is a non-lazy Column (safe today - customs is small; make lazy if it can grow).
- [TODO] Consider storing imported blocklists compactly (not one Item per domain) so 6k+ imports don't bloat the config.
- [DONE 0.5.7] "Get full list" download removed (button gone from Blocking + Settings): failed on the user's enterprise Wi-Fi (AFWall/DNS); bundled top-sets are the source now.

## Redesign follow-ups (polish, after the user's visual pass)
- The full RemindersEngine tick (pacing/back-off/grouping) isn't yet the live driver - the coarse worker + bedtime cadence still deliver reminders; wire the engine tick into the service.
- Feed a MinuteRow/SwitchEvent activity log so Insights uses the Stats engine (sessions/focus/heatmap) instead of only UsageStatsManager.
- Block overlay is a native themed View (not the Compose block/challenge screen) - reliable, but could host Compose for exact design parity; allowance button not yet offered on it.
- Custom limit-day reset time (resetHour/resetMin) has no Settings UI yet (engine + Config support it).
- Old config.json won't parse into the new model → fresh defaults on first launch after this update.

## Next changes (requested 2026-10-01)
- [TODO] **Cool-off starts after the phrase, like the PC.** Today the cool-off countdown runs alongside the phrase (guard/Challenge.kt). It should start only once the phrase is typed correctly; when it ends, the change goes through.
- [TODO] **Member search in the group editor shows nothing.** Typing in "add members" finds no results. Fix it, then check every other search/picker for the same bug (mode editor targets, Blocking list, Keywords, app pickers).
- [TODO] **Choose how a site is blocked: cut the connection / go back.** Per-item option in the site rule editor (and protection lists / keywords where it applies). The flags already exist on Item.blockType (site: "dns,close,back"), but enforcement doesn't act on them: the overlay only does "close". Make "cut connection" (VPN/DNS drop) and "go back" (browser back) actually do that.
