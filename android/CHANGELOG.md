# Changelog — Lockdown Mobile

## 0.5.12 - 2026-10-03 14:32
- **Fixed: turning grayscale off didn't bring the colour back.** 0.5.10 only switched off grayscale that it had marked as its own, and grayscale turned on by an older build, or by the copy you had before the reinstall for the new signing key (which wiped that mark), had no mark, so it stayed on. Now:
  - **Switching "Bedtime grayscale" off** (in Settings or Reminders > Bedtime), or switching Bedtime itself off while grayscale is on, gives the colour back straight away, whoever turned it on. Only grayscale is touched, never another colour correction.
  - Grayscale left on without the mark counts as Lockdown's own while the switch is on, so it also goes off by itself at wake time.
  - **At wake time the colour comes back within a minute** (the app-blocking service checks every minute), instead of up to 15 minutes or more later.
  - **When Lockdown isn't allowed to change the screen's colour, it says so.** That permission comes from a one-time adb command, and uninstalling the app removes it. Under the grayscale switch you now get the command and a **Copy command** button. Guardrails > Reliability has a new "Grayscale permission" row while the switch is on. If switching off fails, a message says why and where to turn it off by hand.
- **A video that is playing stops when its block starts.** Before, the block only covered the screen: the sound went on under it, and after OK, YouTube or the browser carried on in a picture-in-picture window. Now the video is paused and the app is left right away (an app goes to the home screen). The notice stays until you press OK. A picture-in-picture window of a blocked video is kept paused.
- **How a site is blocked now really happens** (your 2026-10-01 request). In a site's rule editor:
  - **Go back** makes the browser go back a page.
  - **Leave the browser** goes to the home screen. Android has no way to close a single tab.
  - **Cut the connection** lets the site filter stop the site loading. The filter now cuts a site only when this is chosen (it's the default). Without the site filter running, the page is covered instead, and the editor says so.
  - A site with no method chosen shows its default (Cut the connection) as chosen.
  - Protection-list sites in the browser now go back too.
- **The cool-off starts after the phrase, like the PC** (your request). It no longer runs while you type. It starts once the phrase is right, pauses if you change the phrase back to wrong, and the change goes through by itself when it ends.
- **Searching for members finds your apps** (your request). In a group's Members and a mode's "Also block these", the search now also lists installed apps that aren't on your blocklist yet. An app picked as a group member is added to the list when you save, with no rules of its own (the group's rules govern it). Blocking an app (Block something > An app) has a search box now too.
- **A member's limits in a group come on top of the group's, never instead (like the PC since 0.84.3).** Every member always gets every group rule, counted in the group's shared total, plus its own extra limits:
  - Time and openings fill both. With the group at 2 h a day and YouTube's own 1 h, YouTube stops at 1 h, and that hour also counts toward the group's 2 h.
  - Blocked hours add up and allowed hours narrow.
  - The group's shared allowance stays shared.
  - Group editor: a new section, **Extra limits on top of the group's**, where each member gets an "Extra limits" button.
  - Anti-Bypass: adding or tightening an extra limit is free. Removing or relaxing one, or changing its hours, needs the challenge. Clearing away a temporary extra that has run out is free.
  - "Blocked until" is the end of the longest of the blocks.
- **Emergency unlock:**
  - It runs in trusted time, so a clock set forward no longer gives a longer unlock, and an unlock can't last longer than its minutes.
  - A use dated in the future still counts.
  - Permanently blocked apps (by their own rule or a group's) are no longer offered and can't be unlocked.
  - New tick, **Pause bedtime and break alerts (and bedtime grayscale)**, for the emergency's length. Ticked together with apps it is one use; on its own it is one use too. Your bedtime settings don't change.
  - The 5-minute "unlocked for editing" window runs in trusted time too.
- **Updates:** the app now looks through recent GitHub releases for the newest `LockdownMobile-*.apk`, not only the latest release, so a PC-only release on top no longer hides a phone update.
- **Time counts only while the phone is in use.** Nothing counts while the phone is locked or the screen is off (audio playing kept YouTube's time running before).
- **A blocked item's time and retries no longer count.** They used to fill its limits and its group's. The opening that goes over a limit still counts.
- **A browser video in full screen keeps its site.** The address bar is hidden then, and the site used to be lost: no time counted, and no block. The address is now also kept fresh when keywords are off.
- **Modes:** marking music.youtube.com as Neutral no longer takes the whole YouTube item out of Work, Focus and the other modes (like the PC since 0.84.1).
- Smaller fixes:
  - A schedule inside its blocked hours, running on its allowance, shows orange ("soon") instead of "allowed".
  - "Running low" shows the limit with the least time left.
  - A site's second and later hostnames lose a typed `www.` or `https://` too, and a target saved with `www.` still matches.
  - Saving settings no longer writes the whole configuration on the screen's thread. Saves are atomic, and the site filter's thread can't overwrite a change made at the same moment.
  - Usage counters for weeks, months and openings that ended more than 45 days ago are dropped once a day, so the usage file stops growing.
  - The pause-before-open countdown no longer disappears after 3 seconds.
- Tests: new `GroupMergeTest` (14) covers the merge rule (the group's 2 h with YouTube's own 1 h, the group running out first, an old looser extra no longer loosening, blocked hours adding up, allowed hours narrowing, shared and own allowance, openings filling both, the latest "blocked until", a disabled group still counting both ways, Anti-Bypass on extras), emergencies against permanent blocks and with a future date, the orange allowance status, and the neutral subdomain. Also new: `UpdateLogicTest.aPcOnlyReleaseOnTopDoesNotHideThePhoneUpdate`, more prune cases in `UsageCounterTest`. Changed: `RulesEngineTest` (the emergency keeps a permanent block, and a member's extra is a second rule, not a replacement).
- Found in review:
  - **Switching grayscale off without the permission now opens Android's Colour correction page** so it can be switched off there in one tap, and the colour comes back by itself within a minute once the permission is granted again (the "ours" mark is kept when Android refuses).
  - **The copy in the work profile (Island) says it can't change the screen's colour** under the grayscale switch, instead of asking for a permission that wouldn't help: the colour setting belongs to the main profile. It no longer tries.
  - The grayscale decision is now a pure, tested step (`grayscaleStep`): an unmarked grey screen with the switch on goes off at wake time, grayscale the user set is left alone, and nothing is written when nothing changes.
  - Opening the group editor, a mode editor or the app picker no longer freezes the screen while every installed app's name is read (now off the main thread). Installed apps not on the list show up once something is typed, instead of burying the members list.
  - After a site sent you out of the browser, its address is forgotten, so the next page opened isn't judged by it before its address bar is read.
  - Tests: 6 more (grayscale left on by an older copy, the user's own grayscale left alone, write only on change; removing a member with extra limits, a non-member's extras doing nothing, an extra of a kind the group lacks).

## 0.5.11 - 2026-10-02 23:22
- **The app now updates itself.** It checks GitHub when it opens and every time you come back to it (at most every 5 minutes), and when a newer Lockdown Mobile is out, a banner on Home shows the version with **UPDATE** and **✕**. The ✕ hides the banner until the app is next started (coming back from another app doesn't bring it back).
- **UPDATE downloads the new version inside the app** (progress 0–100%) and hands it to Android's installer — no browser, no Downloads folder. The first time, Android's "Install unknown apps" screen opens for Lockdown; allow it, come back, and the install carries on by itself. Android still asks you to confirm every update.
- **A failed update says why**, with **TRY AGAIN** and **GITHUB** buttons; nothing opens by itself.
- **Settings → Updates:** Check for updates (on/off), your version with **CHECK NOW** (gives up after 10 seconds and says in words what's likely wrong, e.g. no internet or a firewall), **GITHUB** (the releases page), and **GET UPDATE** when one is available. An automatic check that fails stays silent.
- The downloaded file is deleted once the new version runs.
- The install keeps going even if Android restarts Lockdown when you allow "Install unknown apps" (some phones do), and a download that finishes while you're in another app opens the installer when you come back instead of being silently blocked by Android. Back from Android's settings screen returns straight to Lockdown.
- Note: the app looks for the `LockdownMobile-X.Y.Z.apk` file on the **latest** GitHub release; a release with only the PC installer means "no Android update". Only an APK signed with Lockdown Mobile's own key installs over it.

## 0.5.10 - 2026-10-02 13:43
- **Fixed: the bedtime grayscale switch couldn't turn grayscale off.** Switching it off only saved the setting; nothing ever gave the screen its colour back, so the phone stayed grey until you turned it off in Android's own settings. Turning it off now restores colour straight away (and turning it on at night greys the screen straight away, not up to 15 minutes later).
- **Grayscale follows Bedtime.** If Bedtime itself is off, the screen no longer turns grey at night.
- Lockdown only turns off grayscale it turned on itself, never one you set in Android's accessibility settings.

## 0.5.9 - 2026-09-30 01:17
- **Fixed: the phrase challenge could never be passed.** When a phrase *and* a cool-off were both set, the cool-off timer never counted down, so "Confirm change" stayed disabled even with the phrase typed correctly. The cool-off now ticks whenever a wait is set.
- **Alert when you mistype the phrase.** The field turns red and shows "That doesn't match the phrase — check it." as soon as what you've typed stops matching (and "Keep going…" while you're on track).
- **"Table writing" option (like the PC's 3×3 grid).** A new toggle in Guardrails → "Before loosening anything". When on, the phrase is typed one word at a time into a 3×3 grid: one cell lights up at random, you tap it and type the word shown, then a different cell lights up with the next word. Pasting a chunk is ignored and nothing is auto-focused, so text-expander macros and paste can't get through. A typo shows "Typo — fix it to go on."

## 0.5.8 - 2026-09-30 00:59
- **"Unlock while the challenge is on" box in Guardrails** (like the PC). When a challenge (phrase / only-in-hours) is set, a box at the top of Guardrails lets you pass it once to open a 5-minute window where loosening edits save without re-asking each one. It shows the countdown and a "Lock now" button to end the window early; the Unlock button is disabled outside your allowed hours.

## 0.5.7 - 2026-09-30 00:45
- **Removed the "Get full list" button** (Protection lists, both in Blocking and Settings). The bundled top-sets (a few thousand domains each) are the source now; the online full-list fetch was unreliable on restrictive networks. Each list still shows its domain count.

## 0.5.6 - 2026-09-30 00:32
- **Blocking list is collapsed and paginated** so a multi-thousand-item list scrolls smoothly. The "Blocked · N" section is tap-to-expand and shows 20 rows at a time, loading 20 more as you scroll. Searching opens it automatically and paginates the matches too.

## 0.5.5 - 2026-09-30 00:20
- **Groups now take the full rule set.** Items and groups share one editor, so a group can carry several rules at once — scheduled hours, a shared daily/weekly/monthly limit, an opening limit, temporary, permanent — not just a single time limit.
- **Group members fixed.** The member picker now shows **apps** and your limitable items and **hides the thousands of forever-blocked imported sites** (grouping something already blocked forever does nothing). Added an "add a site to this group" field so you can add one by typing.
- **Keywords page redesigned.** The add-a-word field is at the **top**; each list (built-in + your words) is a **collapsible section** with its own search; inside a built-in list you can switch **individual words on/off** (shipped words can't be deleted, only turned off); your words are editable; exceptions have their own section.
- **Empty groups can be removed without the challenge** — a group with no rules and no members enforces nothing, so removing it loosens nothing. Covered by a test.

## 0.5.4 - 2026-09-29 21:40
- **Accessibility service is now crash-proof.** Every accessibility callback (foreground watch, keyword scan, bedtime tick) is wrapped so a stray error can't make Android disable the service — the "it keeps losing the grant" symptom. (Note: reinstalling the app always revokes the grant once; that part is Android, not a bug.)
- **Battery health check in Guardrails.** New "Battery not restricted" row with a Fix that opens battery-optimisation settings — on OnePlus/OxygenOS aggressive battery management is what kills the background service; exempt Lockdown to keep app/site blocking alive.

## 0.5.3 - 2026-09-29 17:14
- **Fixed the Blocking freeze** (the big one). With thousands of imported sites the tab ran the rule engine for every item on the main thread and rendered them all at once → ANR. Now it's a flat lazy list that computes status only for the rows on screen. Home, the Mode editor and the Group editor had the same problem and are fixed too (Home prefilters cheaply; the editors are lazy + searchable). An audit workflow (Opus readers + a Fable verdict) confirmed the hotspots.
- **Insights 7-day / 30-day fixed.** They now show per-day totals (via a single efficient query), and the screen no longer gets stuck on "Loading…" if one query fails — each piece is guarded independently. Bars are tappable to see that day's/hour's time.
- **Removed the productive/neutral/distracting bar** from Insights (no app-role assignment in this version).
- **Works without the VPN.** The site filter (VPN) is optional and off by default; blocked sites, protection lists and keywords are now enforced in the browser by the accessibility service, so you don't need the device-wide VPN that could interfere with other apps. Added a note on the toggle.
- **Emergency unlock never releases sites.** It only unlocks apps now; blocked sites and protection lists always stay on.
- **Faster with big lists.** Import (sites/keywords) runs off the main thread, and the config is stored compact (not pretty-printed) so multi-thousand-item saves are ~3× smaller/faster.

## 0.5.2 - 2026-09-28 18:46
- **"Get full list" now works on restrictive networks.** The full blocklists are fetched from the jsDelivr CDN instead of raw.githubusercontent.com — many enterprise/DMZ DNS setups refuse to resolve raw.githubusercontent.com (the cause of the "couldn't download / UnknownHostException" errors) while allowing normal CDNs and github.com (which is why app updates already worked). Same files, a host your network will resolve.

## 0.5.1 - 2026-09-28 18:23
- **Insights no longer crashes on 7-day / 30-day.** The screen-time queries were running on the main thread; moved them off it (verified on-device: switching ranges is smooth).
- **Insights shows the right chart per range.** Today / Yesterday now show that day's 24 hourly bars; 7 days / 30 days show per-day bars. Tap any bar to see that hour's / day's exact time (tap again to clear).
- **Real app icons** in the Blocking list, the add-app picker, and the Insights top-apps list (sites still use an initial).
- **"Get full list" now reports what happens** — "Downloading…", the domain count on success, or a clear "couldn't download (a firewall like AFWall can block it)" — and each list shows its current domain count. (The bundled top-sets already work offline; the full fetch needs the network to allow it.)

## 0.5.0 - 2026-09-28 16:58
- **Completely new look + the engine wired in.** Rebuilt the whole app on the designer's system: an orange-seeded Material 3 palette (light + dark), Barlow Condensed for headings/numerals and Inter for body (both bundled), and sharp, technical corners. Five calm tabs — **Home** (hero screen-time card + goal, active mode, running-low, quick actions), **Blocking** (search, status sections Blocked/Soon/Allowed, groups, protection & keyword filters, a live-summary rule editor with every rule type + block-type flags), **Modes** (list + editor with categories, picks, Pomodoro, by-hand/schedule, Start now), **Insights** (range picker, daily-average chart, productive/neutral/distracting split, top apps), **Reminders** (bedtime hero, breaks, your reminders + full editor) — plus **Guardrails** (reliability health, challenge, emergency unlock, switches) and **Settings** in the top bar, and a redesigned block/challenge experience.
- **The tested engine now runs the app.** Config migrated to the engine model (items, groups, all rule types, the keyword/reminder/anti-bypass configs). The accessibility service feeds the usage counters and enforces via the engine (rules + the active mode); the DNS filter uses the engine's site decision; loosening any block goes through the anti-bypass challenge.

## 0.4.5 - 2026-09-28 16:00
- **Guardrails engine (E8).** `AntiBypass` (pure, tested), ported from the PC: the challenge (type a random phrase / only inside chosen hours / a cooling-off wait), a short unlock window after passing, and the "what loosens a block" detector that decides when an edit needs the challenge - across rules, items, groups, anti-bypass settings, emergency-unlock settings and protection lists. `Emergency` counts unlock uses per day/week against the limit clock. (Uninstall protection and trusted time are already on the phone.) Carries the ⛔ anti-bypass rule in code.

## 0.4.4 - 2026-09-28 16:00
- **Stats engine (E9).** `Stats` (pure, tested), ported from the PC: totals and per-app / per-site active time, active vs idle, sessions (5-min-gap), longest focus, categories (productive / neutral / distracting), the day timeline, the hour×day heatmap, switch / visit summaries (short visits, "checking / focused / mixed"), and goal + no-emergency-unlock streaks.

## 0.4.3 - 2026-09-28 15:50
- **Modes + Reminders engines (E6, E7).** `Modes` (pure, tested): one mode at a time, started by hand (for a while / until a time / until stopped, optionally locked) or by schedule, blocking a category + picked items / groups / extras, with mute and a Focus Pomodoro; `ModesStore` persists it. `RemindersEngine` (pure, tested): bedtime warning + escalating overlay (and an optional mode), work breaks (strict auto-start / snooze / 20-20-20) and your own reminders by interval / set times / a daily random time, honouring days, an hours window, a "did you do it?" check and a per-day cap - with pacing and back-off.

## 0.4.2 - 2026-09-28 15:35
- **Usage / limit accounting engine (E3).** New `UsageCounter` (pure, unit-tested) records foreground time and opens into the exact `(owner, bucket)` counters the rule engine reads - per-item day/week/month totals, shared group pots, opening limits split into switches (every return to front) vs new visits (app launches / site returns after a gap), and scheduled-allowance windows. A long gap (device asleep / service paused) isn't counted. `UsageStore` persists the counters across reboots and throttles writes. Not yet wired into the live service (that lands with the enforcement pass).

## 0.4.1 - 2026-09-28 15:17
- **Keywords engine (built-in).** Ported from the PC: Adult English + Polish word lists shipped on by default, your own words, whole-word matching, trailing-`*` stems, phrases, accent-insensitive, site/word exceptions. Tested.
- **Protection lists bundled (all six, offline).** Scam, Phishing, Malware, Adult, Gambling, Manga & anime now ship inside the app as a popularity-ranked top-set (no download needed); each can be toggled on instantly, with an optional 'Get full list' fetch later. (Design decision: option c.)

## 0.4.0 - 2026-09-28 14:46
- **Full-parity rule engine (foundation).** New self-contained `engine/` package ported from the PC app so the phone can enforce rules identically: all rule types (permanent, scheduled allow/block windows that cross midnight, time limit per day/week/month, opening limit, temporary, allowance), groups with a shared pot and per-member overrides, the limit clock (custom reset time; week/month periods), reason priority, and rule status (blocked/soon/allowed). Unit-tested; not wired to the UI yet (that lands with the redesign).

## 0.3.0 - 2026-09-27 18:01
- **Protection lists** - bundled, auto-updating blocklists (Adult, Gambling, Manga & anime piracy, Scam & fake shops, Malware), from HaGeZi. Toggle in Settings; it downloads on enable and refreshes weekly on Wi-Fi. The site filter blocks anything on an enabled list, minus your allowed exceptions.
- **Search bar in Blocking** - find and edit/disable any site among thousands (the list no longer renders them all at once).
- **Trusted time** - Lockdown's own timers ignore the phone clock being set back, and you get a warning if it is. (App usage limits still follow the system clock, which Android stamps usage with.)
- **Daily screen-time goal** - an optional once-a-day nudge when you pass a set number of minutes.

## 0.2.6 - 2026-09-27 17:36
- **Fast site matching for big lists.** Blocked domains are now indexed, so importing thousands (e.g. the adult + manga lists) stays instant per DNS query instead of scanning every rule.

## 0.2.5 - 2026-09-27 16:59
- **Export / import all settings** (Settings) - save the whole config to a .json and load it in the other profile's app, so your work-profile (Island) copy can be synced to match. (True auto-sync across profiles needs device-owner privileges, which a sideloaded app doesn't have.)

## 0.2.4 - 2026-09-27 16:56
- **Force SafeSearch** - the site filter answers Google, Bing, DuckDuckGo and YouTube with their safe-search address (and NODATA for their IPv6 so clients fall back). Falls through to normal lookup on any hiccup, so it can't break search.
- **Bedtime escalation** - the "time for bed" nudge now gets more frequent as the night deepens, following your tiers (e.g. every 15 min from 21:00, 5 from midnight, 1 from 3am), driven by the always-on service.
- **Tamper watchdog** - a notification if the accessibility service or the site filter gets switched off while blocking is on, and the Home status rows are tappable to fix it.

## 0.2.3 - 2026-09-27 16:40
- **Emergency unlocks** (Home) - pause all blocking for 15 minutes, a set number of times per week; the quota resets on a rolling 7-day window and blocking resumes automatically.
- **Pause before opening** - a mindful countdown over a time-limited app before it opens (at most once per 5 min).
- **Weekly digest** - an optional once-a-week notification with your last 7 days' screen time.
- **Runs in Island too** - installed in the work profile so app + keyword blocking work there; the site filter already covered it. (Config is copied at setup; changing rules re-syncs manually for now.)

## 0.2.2 - 2026-09-27 16:24
- **Import lists** (Settings) - load a .txt of domains as blocked sites, or a .txt of keywords, e.g. the lists exported from Lockdown on the PC. Duplicates are skipped.
- **Keyword blocking** - the accessibility service watches the browser's address/search bar and blocks on a matching word (best-effort, Chromium + Firefox).
- **Daily open cap** - when set, opening a blocked app more than the cap times in a day is blocked.
- **Bedtime grayscale** - drains the screen's colour during the sleep window (needs a one-time adb grant of WRITE_SECURE_SETTINGS).
- **Site-filter switch is honest** - it now reflects the real running state and self-heals (restarts if the process was killed and it should be on). The consent prompt only appears the first time.

## 0.2.1 - 2026-09-27 15:53
- **Site filter forwards to the network's own DNS.** It used to forward allowed lookups to 1.1.1.1, which some networks (e.g. enterprise Wi-Fi) block - so nothing resolved. It now reads the active network's resolver and forwards there, binding the query to the real network. Blocking (NXDOMAIN) is unchanged.

## 0.2.0 - 2026-09-27 15:28
- **Screen time (Insights)** - real per-app usage from UsageStatsManager: total, an hourly timeline, and a
  per-app list with time and opens; Today / Yesterday / 7 days / 30 days. (Chart cleaned up: green bars on a
  thin baseline, no grey block.)
- **App blocking** - an accessibility service shows a full-screen overlay over blocked apps. Block forever, on
  a weekly schedule, or after a daily time limit. Verified working on-device.
- **Site blocking** - a local DNS-filter VPN (NXDOMAIN for blocked domains, everything else forwarded to
  1.1.1.1), a domain block list, and a live network log. Nothing leaves the phone but ordinary DNS.
- **Guardrails (anti-bypass)** - a challenge (wait or type-a-phrase) in front of loosening actions;
  device-admin uninstall protection; a foreground service + boot receiver so the filter survives a reboot.
- **Reminders** - bedtime, breaks and your own reminders, with an Important flag (needs the challenge to turn
  off); a 15-minute WorkManager check fires due reminders.
- **Modes** - group rules and switch them on together.
- **Settings** - theme (system/light/dark, applied), update check against GitHub releases, and toggles for the
  extras.
- **Home** - master on/off (off is behind the challenge) and a live status list.
- **Widget + Quick Settings tile** - a home-screen widget showing today's screen time, and a tile that toggles
  Lockdown.
- Data model + JSON store; unit tests for the block-decision logic.

## 0.1.0 - 2026-09-27 14:22
- Project scaffold: Kotlin + Jetpack Compose, Material 3 with Material You (dynamic) colours, orange
  fallback on Android 11 and older. Navigation shell with the seven sections — Home, Blocking, Modes,
  Insights, Reminders (bottom bar) and Guardrails, Settings (top bar) — each a placeholder for now.
- Gradle wrapper, app module (`com.husarp.lockdown`, minSdk 26, targetSdk 35), padlock launcher icon,
  README, PLAN and this changelog.
