# Changelog

## 0.21.0 — 2026-09-19 04:19
- **Lockdown logo** (assets/lockdown.ico - white shield with a check on orange): Windows notifications now show the
  logo and "Lockdown" (Windows app identity registered for your user) instead of the red status dot and "Python";
  the main window, pop-up windows and the taskbar use it too
- **Faster blocked-word notices**: after closing many tabs at once, the first notice shows immediately and the rest
  come as one ("Closed 3 more tabs with blocked words") instead of one each; a new notification replaces the one still
  showing instead of queuing behind it (that's where the ~20 s delay came from); the tab is checked twice a second
- **Network Log scrolling glitch fixed**: the table is now one native table (Treeview) instead of hundreds of small
  widgets that tore while scrolling - smooth scrolling, "Show more" adds 100 rows in ~30 ms, a live refresh with
  nothing new takes ~10 ms; hover highlight, click menu, icons and light / dark theme kept
- **Anti-Bypass: "Real keyboard only"** (off by default): while the phrase window is open, keys typed by a program
  (macro tools, auto-typers) are blocked - the phrase has to be typed on a real keyboard; turning it off needs the
  challenge
- 157 tests passing

## 0.20.0 — 2026-09-19 03:57
- **Word lists**: blocked words are now lists, one line each - ready-made **Adult words - English** (102 words) and
  **Adult words - Polish** (45 words) that you switch on / off, **Your words** and **Exceptions**. "Open" shows a list:
  tick single words off (or back on, "All on"), search, remove / add your own; changes apply on Save. Turning a list or
  word off, removing your words or adding exceptions needs the Anti-Bypass challenge (it lists what you're weakening)
- **Faster pages**:
  - pages you haven't opened yet are built in the background after start (one every 0.5 s), so opening them later
    takes 0.05-0.4 s instead of 1-7 s
  - Network Log: shows the newest 25 rows ("Show more" for older ones) and only redraws when something changed
    (before: 150 rows redrawn every 3 s); switching back to it 2.4 s -> 0.2 s
  - Notifications: the alert messages and the recent blocked visits are sections you open (built the first time;
    the visits list no longer rebuilds itself on every blocked visit)
  - lists everywhere: only rows that appear / disappear are moved (not all of them on each refresh)
- 156 tests passing

## 0.19.0 — 2026-09-19 03:39
- **Forced SafeSearch** (Blocking > Protection, on by default): Google, Bing and DuckDuckGo always search with
  SafeSearch, YouTube runs in Restricted Mode (moderate) - the DNS filter answers their names with the engines' own
  "safe" addresses (forcesafesearch.google.com, strict.bing.com, safe.duckduckgo.com, restrictmoderate.youtube.com),
  in every browser and private windows; Chrome / Edge / Brave policies force it too
- **Blocked words** (on by default): every second the tray app checks the address and title of the browser tab in
  front for blocked words - 84 built-in English and Polish adult words + your own words, whole words only (a word
  ending in * also matches longer ones, several words = a phrase, accents ignored); on a match it **closes the tab**
  or **goes back** (your choice; a tab with nothing to go back to is closed) and says which word. Exceptions: a site
  (not checked) or a word (never counts). Search words still being typed into the address bar aren't checked
- Turning either off, removing one of your words or adding an exception needs the Anti-Bypass challenge
- The DNS filter stays on for SafeSearch even with every protection list off
- Designer brief: light-mode problems (switch knobs, outlined buttons, selected nav item blend in) + screenshots
- 154 tests passing

## 0.18.0 — 2026-09-19 03:23
- **Anti-Bypass** (Phase 7, new Anti-Bypass page): anything that loosens a block needs the challenges you turn on;
  making blocks stricter is always instant. Off until you turn a challenge on.
  - **Type a random phrase** (30 / 60 / 120 / 250 characters of letters and digits, pasting blocked, shows where a
    typo is); passing it allows loosening changes for 5 minutes ("Lock now" ends that early)
  - **Only during these hours** (days + times, several windows): outside them nothing can be loosened; the window
    says when the next chance is
  - needs the challenge: removing a site / app / group / member or a site from an item, higher or no limits, other
    blocked hours, more allowance, a shorter temporary block, a gentler app block type (checked on every save, with
    auto-save the edit is undone if cancelled), switching a protection list off or allowing a site, more / longer
    emergency unlocks, tray Exit, weakening Anti-Bypass itself. Not: the emergency unlock itself, limit reset time
- **Keeping Lockdown running**: the tray app is started again within a minute if it's closed any other way than
  Exit (per-user task "Lockdown Agent Watchdog", logged); the "Lockdown Watchdog" task starts the service every
  minute if it was stopped; uninstalling asks for the challenge; `repair_dns.ps1` pauses the watchdog
- Clock: a new Windows time zone only counts after 24 hours (it would shift blocked hours); summer / winter time
  changes count at once
- 147 tests passing

## 0.17.0 — 2026-09-19 03:10
- **Fix (urgent): the internet stopped working** after 0.16.0 - ~237k protection-list domains in the hosts file made
  Windows' DNS lookups hang (and froze the service at startup, so it showed as off). The lists are no longer written
  to the hosts file; `scripts/repair_dns.ps1` (admin) stops Lockdown, restores DNS settings and cleans the hosts file
- **DNS filter** in the service for the protection lists: a small DNS server on 127.0.0.1 / ::1 port 53 answers
  listed sites with 127.0.0.1 (the "blocked - it's on the scam list" notice still works) and passes everything else
  to the network's own DNS servers. Connected network adapters get DNS "127.0.0.1, <their own DNS>" (+ ::1 for IPv6),
  so if the service ever stops Windows falls back to the normal DNS and the internet keeps working; new networks are
  picked up within 10 s; original settings are saved and restored on uninstall / `service.py restore-dns` / all lists off
- **Bigger lists** (~4.3M sites; lists by HaGeZi also block every subdomain): Scam (Block List Project + HaGeZi Fake),
  Phishing (Block List Project + Phishing Army), Malware (HaGeZi Threat Intelligence ~2.6M + URLhaus), Adult (Block
  List Project + HaGeZi NSFW, ~1M), Gambling (HaGeZi); re-downloaded automatically when a list's sources change
  - in memory as sorted hash tables: 34 MB for all of them, ~15 µs per lookup, loaded in ~8 s in the background;
    downloads are streamed (the 2.6M-site list: ~18 s)
- Protection tab: **download progress** ("Downloading... 12.3 of 42.7 MB (part 1 of 2)", live); lists are checked every
  10 s instead of every minute (so "Update now" starts at once); "Check a site" searches in the background (no freeze)
  and knows subdomains; "Allowed anyway" also allows the site's subdomains
- "Start service" button: ends a stuck copy of the service first (before, Windows ignored the start while it hung)
- 140 tests passing

## 0.16.0 — 2026-09-19 02:37
- **Protection lists** (Blocking > Protection): always-on community lists - Scam (Block List Project, ~8.5k), Phishing (phishing.army, ~40k), Malware (abuse.ch URLhaus), Adult (StevenBlack, ~70k) on by default, Gambling (StevenBlack) optional
  - the service downloads them once a day (retries an hour after a failure; "Update now" button) and blocks them through the hosts file, 8 domains per line; not affected by modes or the emergency unlock
  - "Check a site" box, "Allowed anyway" exceptions
  - blocked visits say which list: "fake-shop.com is blocked - it's on the scam list." (new alert reason on the Notifications page)
- Hosts file: the 2-second check skips rebuilding while neither the blocklist nor the file changed (with ~120k domains: 300 ms -> 2 ms); manual edits are still repaired
- Fix: the emergency-unlock list no longer shows things a mode blocks that aren't on your list (they can't be unlocked)
- 137 tests passing

## 0.15.0 — 2026-09-19 02:28
- **Reminders** (Phase 6b, Modes page > Reminders tab):
  - Sleep: bedtime + wake-up time, heads-up before bedtime, a full-screen "Time for bed" overlay at bedtime ("Going to bed" / "5 more minutes") that comes back every N min until wake-up; optionally turns on a mode until wake-up
  - Breaks: after 45 min of use (resets after 5 min away) a "Time for a break" popup with a break countdown; optional forced break (covers the screen, no way out until it ends); 20-20-20 option; breaks counted
  - Your reminders: every X min of use / at set times on chosen days / once a day at a random time; Done / Snooze (limit, then it stays), "Did you actually do it?" check-back, quote packs (Stoic, Motivation, Health) or your own quotes; done / "not done" stats
  - while a full-screen app (a game) is in front - or Do Not Disturb is on - popups wait and you get a Windows notification; sleep and forced-break overlays still show
- 132 tests passing

## 0.14.0 — 2026-09-19 02:22
- **Modes** (Phase 6a): a mode blocks a category (e.g. everything marked Distracting) plus picked sites / apps / groups, on top of the normal blockers; one at a time
  - built in: Work, Study, Focus (Pomodoro 25/5 x4 + 15, blocked only in focus rounds), Do Not Disturb (also mutes Lockdown's notifications), Relax (nothing extra); make your own
  - start for 30 min / 1 h / 2 h / until a time / until stopped; optional "lock until it ends" (Stop is disabled; the emergency unlock still works); or on its own schedule (days + times)
  - Modes page (now / your modes / editor), tray menu "Modes" (quick switch + Stop), Dashboard shows the mode; notifications when a mode starts / stops and when Pomodoro breaks start / end
  - blocked visits show "blocked while this mode is on" (new alert reason, set on the Notifications page)
- 126 tests passing

## 0.13.0 — 2026-09-19 01:55
- **Network Log** (Phase 5): which app connected to which site in the last hour
  - the service reads Windows' TCP tables (every 2 s, own thread) with the owning app, names each address from the Windows DNS cache (the name the app looked up), and keeps one hour
  - Chrome/Edge/Brave policy: built-in DNS client off, so their lookups go through Windows and get names
  - page: table (time, app, site, port, allowed / blocked visit) or graph (connections per minute + top apps / sites), search, app filter, All / Allowed / Blocked, Windows' own and local-network traffic hidden by default (switches), Live updates, Export CSV
  - click a row: Block site (opens Add filled in with the main domain, e.g. googlevideo.com), Block app, Copy site, Copy app name, Show only this app
- Chart tooltips are a small window of their own now, so they're never cut off
- 122 tests passing

## 0.12.0 — 2026-09-19 00:21
- Tab bars (Screen Time, Blocking, Settings, rule editors): the chosen tab is a rounded button with padding, not a sharp block
- Charts are drawn smooth (anti-aliased): round donut, rounded bars/cells/timeline; the heatmap's less/more legend is no longer cut off; charts follow the Windows display scaling
- Hover a chart for details: heatmap cell ("Fri 10:00-11:00 16 min active"), a day bar (date + time), an hour bar (switches), a timeline piece (time + category), the donut (time per category)
- Switches per hour always shows at least 07-22 (a single busy hour no longer turns into one huge block)
- Days with an emergency unlock get a small blue dot on the day charts (tooltip: "Emergency unlock used 1x")
- Categories: clicking a category opens a small menu - pick one, "New category..." (name + colour) or "Edit categories..." (change colours, delete your own); the hint text is gone. Timelines, the donut and legends use your colours
- Faster: Screen Time tabs are built once and only updated; Dashboard/legend lists reuse their rows; the Dashboard isn't rebuilt when you come back within 10 s; Blocking tabs and blocker settings are built only when first opened
- "Switches today" compares with your usual count by this time of day (not with whole days)
- Lockdown's own window shows as "Lockdown" instead of "Pythonw"
- 117 tests passing

## 0.11.0 — 2026-09-18 23:56
- **Blocking page in the new design** (Phase 4, batch 2):
  - Overview: one table card; rules as coloured chips (group rules marked with →), status with a dot, Edit / Remove as quiet buttons, Sort + "+ Add" on the right
  - Add / Edit: blockers are collapsible cards with a one-line summary each ("1h30 per day", "Blocked 22:00-07:00 Every day"), "2 of 5 on" counter and a sentence under them saying what will happen; days are toggle buttons with the times on the same line
  - Groups: group list on the left, editor on the right (Remove group / Cancel / Done), members as chips - click one to customize it, × to remove, "+ Add member"
- Kept from the app (newer than the design): "When blocked" checkboxes, day/week/month limits, Emergency unlock
- 116 tests passing

## 0.10.0 — 2026-09-18 23:48
- **New look** from the design (Phase 4, batch 1): design colours as light + dark tokens, Inter + Barlow Condensed fonts (bundled in `assets/fonts`, OFL), Lucide icons in the sidebar, "LOCKDOWN" logo, accent orange; status colours stay blocked = red, allowed = green
- **Dashboard** (new home page): screen time today vs yesterday, blocked now, switches vs your average, time saved (blocked attempts x your usual visit length, 5 min without history), limits in progress with bars, today's timeline by category, last 7 days with the daily goal line, coming up (blocks starting/ending, limits about to run out), blocked visits today, at a glance (top app, most switched to, quietest hour); "Blocking isn't being enforced" banner with a Start service button (asks for admin); first-day empty state
- **Screen Time** page: Overview (active / idle / longest focus / sessions, day timeline, 7-day heatmap, 7- or 30-day bars, categories donut), Apps and Websites (time, share, switches/visits, category), Switches (per hour, average visit, short visits under 30 s, most switched to with "often just checking" / "focused" hints); Today / Yesterday / 7 days / 30 days
- **Categories**: productive / neutral / distracting per app and site; blocked ones start as distracting, the rest neutral; click the label to change it
- Settings: Appearance (Dark / Light / Match Windows) and a daily screen-time goal (default 5 h, can be off)
- 116 tests passing

## 0.9.2 — 2026-09-18 23:32
- Apps: new "Also close its background processes" option under Close app (off by default). Once the app itself is closed, the service also closes what it started and whatever runs from its install folder (never Windows' own processes; not used for apps inside Windows or loose folders like Desktop/Downloads)
- 111 tests passing

## 0.9.1 — 2026-09-18 23:14
- Limit reset time can be changed any number of times (no once-a-week lock); the running week and month are now also held, so a change can't start a new week/month early (before: up to 12 h early)

## 0.9.0 — 2026-09-18 23:07
- **Limit reset time** (Settings): limits start over at a chosen time instead of midnight (weekly limits on Monday, monthly on the 1st, at that time). A change never ends the current limit day early - that day gets longer instead - and the time can be changed once a week. Screen-time stats keep normal calendar days
- **Weekly and monthly limits**: "Time limit" and "Opening limit" (renamed from "Daily ...") take per day / per week / per month, in any combination (e.g. 2h a day but at most 8h a week); each blocks until its own period ends. Time can be typed as 45m, 2h, 1h30
- **Emergency unlock**: button at the top of Blocking > Overview lists everything blocked right now; tick one or more, confirm, and they're unblocked for 20 min. Unlocking several at once is one use; default 3 uses per week. Settings: on/off, duration, uses per day/week. Overview shows "Emergency unlock - 12m left"; a warning comes before it ends; every unlock is recorded (for the future graphs)
- New Settings page
- Alert texts: "daily limit" -> "time limit"
- 108 tests passing

## 0.8.1 — 2026-09-18 22:37
- Fix: the grey hint text ("site (reddit.com) or app.exe", "Display name", group name) didn't show until the field was clicked (customtkinter treats a new field as focused until its first focus-out)
- Blocking: a short green confirmation next to the tabs after adding or saving ("✓ YouTube blocker added", "✓ YouTube saved", "✓ Group Night added"); with auto-save off it adds "press Save changes to apply"; disappears after 5 s

## 0.8.0 — 2026-09-18 22:30
- "When blocked" for apps is now three checkboxes: Close app / Minimize / Block internet (Close and Minimize exclude each other; at least one needed); stored as flags, older values still work
- Status colours: blocked now = red, allowed now = green (also in Groups)
- Daily switch limit renamed "Daily opening limit" with a choice of what counts: **Launches / new visits** (default: apps = each start of the program, sites = coming back after N minutes away, default 5) or **Every switch** (previous behaviour); an app launched past its opening limit is closed at once
- Faster: the service re-checks rules every 2 s (was 5 s); Overview counters, countdowns and statuses update every 2 s in place
- 97 tests passing

## 0.7.0 — 2026-09-18 22:15
- Blocking page reworked into 3 tabs: **Overview** (everything, sort by blocked now / next block / date added / name; Edit + Remove), **Groups**, **Add** (pick a site or app, tick any number of blockers at once); Edit from Overview opens the Add form; adding something that's already in the list loads it for editing
- Switch limits: "open at most N times per day" (every switch to the app/site counts; the next opening is blocked; shared total in groups; own alert reason)
- Hours editor: "Remove window" button removed - a time window with no days ticked is ignored
- Temporary blocks being edited offer "Keep (Xh left)" so saving doesn't restart the timer
- PLAN.md: every request scheduled (Phase 3b done; Phase 4 gets the Screen Time page, Dashboard limits list, weekly block calendar); "prevent apps from starting" dropped
- Design brief: Blocking page section + new screenshots
- 94 tests passing

## 0.6.0 — 2026-09-18 22:02
- Apps: new block option "Minimize" (keeps the app running - e.g. a browser with many tabs - but the tray agent minimizes it whenever it comes to the front, checked 4x per second); "When blocked" is now Close app / Minimize / Only block internet + "Also block its internet" checkbox
- Temporary blocks: "Custom..." duration (number + minutes / hours / days, up to 30 days); durations of a day or more shown as "3d 2h"
- Auto-save is on by default; while it's on, the Save/Discard buttons are hidden
- Design brief for the designer: `design/DESIGN.md` + reference screenshots in `design/screenshots/` (start with Dashboard + Screen Time)
- Fixed: changelog timestamps corrected to the real commit times (several were estimated and in the future)
- 92 tests passing

## 0.5.0 — 2026-09-18 21:51
- Remove buttons need two clicks (first click turns the button into a red "Confirm" for 3 s) instead of a confirmation dialog (`gui/widgets.py`)
- Apps started while blocked are killed immediately; process check 4x per second (was 1x); polite 10 s close only for apps already open when their block begins
- Phase 4 data collection (no UI yet): per-minute screen time per app and browser site with active/idle split (`activity` table), switch counting (`switch_events` table)
- 88 tests passing

## 0.4.0 — 2026-09-18 21:22
- Phase 3 — app blocking:
  - Apps are blocked items like sites (all tabs/rules); matched by exe name; Windows/Lockdown processes are protected
  - Per app: Close app / Block internet / Both. Closing: tray agent sends a normal close, service force-kills after 10 s; process check every second (`blocker/apps.py`, `monitor/win.py`)
  - Block internet: Windows Firewall rules added/removed by the service (`blocker/firewall.py`); exe path learned from the running app if unknown
  - "Browse apps" popup: Start Menu apps + apps with an open window, exe icons, search, "Other .exe..." (`gui/app_browser.py`, `monitor/applist.py`)
  - Daily limits count app time while the app is in front (games count even without keyboard/mouse input)
- Groups (new Blocking > Groups tab): any combination of rules, shared daily limit, members inherit rules, per-member customization (e.g. 5-min allowance for one app), items can be in several groups; All tab shows group rules; "Edit ▾" can jump to a group (`gui/groups.py`, `block_groups`/`group_rules`/`group_members` tables)
- Hours rules: "During blocked hours, still allow N minutes" (allowance, resets each blocked period)
- Warnings: N min before a block starts (default 5, configurable, can be off), repeat reminders while you're using the item, "block started" notification; group blocks announced together (`alerts.BlockWatcher`)
- Usage storage generalized (`usage` table: owner + bucket) with migration from `site_usage`
- Shared editors/pickers: `gui/rule_editors.py`, `gui/target_picker.py`
- Fixed a possible hang: COM libraries are imported on the main thread, and garbage collection runs only on the Tk thread
- 85 tests passing

## 0.3.1 — 2026-09-18 20:49
- Full day names everywhere: rules ("Monday–Friday 09:00-17:00", "Monday, Wednesday"), hours editor checkboxes, alert messages ("until Tuesday 07:00")
- Hours editor: each time window is a box with the days on one line and from/to + "Remove window" below
- Blocking > All: Edit button moved next to Remove; with several rules it's "Edit ▾" and asks which rule to edit

## 0.3.0 — 2026-09-18 20:36
- Hours rules: "Allow only during" (default) / "Block during" switch; several time windows per rule, each with its own days (custom hours per day). Old rules keep working as "Block during".
- Daily time limits: tray agent reads the active tab URL via UI Automation (Chrome/Edge/Brave/Firefox), counts time on limited sites (not while idle 15+ min); service blocks until midnight when used up; "limit reached" alert reason (`monitor/browser_url.py`, `monitor/usage.py`, `site_usage` table)
- Clock-change protection: service uses its own trusted clock (NTP + tick counter); the GUI uses the published offset; clock changes logged (`trusted_time.py`)
- Blocking page rebuilt: add/edit form + list per tab (By Hours / By Limit / Permanent / Temporary); All = overview with per-rule Edit (jumps to the tab, highlights the site) and Remove with confirmation
- Save system: global Save changes / Discard at the top, highlighted only on real changes; Auto-save switch (default off); unsaved sites show "Not applied (unsaved)"; temporary blocks start when saved (`gui/draft.py`)
- "+ Popular sites" popup with site icons replaces the checkbox grid; favicons cached locally with letter-icon fallback (`gui/icons.py`, `gui/site_picker.py`)
- Suggestions while typing a site (popular + previously blocked), "Clear my suggestions" (`site_history` table)
- Notification format default: Windows notification only
- Fixed: Blocking sub-tabs didn't switch visibly (scrollable frames can't be raised)
- `uiautomation` added to requirements; 66 tests passing

## 0.2.0 — 2026-09-18 19:56
- Phase 2 (except daily time limits):
  - Block by hours: pick days + from/to time, overnight windows supported (`src/rules.py`)
  - Temporary blocks: 15 min to 24 h, expired ones cleaned up automatically
  - Rules stack on one item (e.g. by hours + temporary); add form has block-type checkboxes; By Hours / Permanent / Temporary sub-tabs show filtered lists; status shows "Blocked now" / "Allowed now", refreshed every 30 s
  - Typing a known site's domain (e.g. reddit.com) blocks all its common hostnames
  - Service: locked browser policies turn off DNS-over-HTTPS + QUIC (Chrome, Edge, Brave, Firefox) — `blocker/browser_policy.py`; removed again by the uninstall script
  - Service: closes open TCP connections to newly blocked sites for 3 min — `blocker/connections.py`
  - Service: blocked-visit listener on 127.0.0.1:80/443 (reads HTTP Host / HTTPS SNI) — `blocker/listener.py`, new `block_events` table
  - Tray agent: notifications for blocked visits (Windows notification / Lockdown popup / both); new Notifications page with per-reason on/off + custom messages (`{site}` `{reason}` `{until}`), repeat cooldown, format, recent visits list; per-site Alerts override (Default/On/Off) on the Blocking page
  - GUI starts hidden in the tray at login (HKCU Run key) and runs as a single instance
  - `blocked_items.notify` column added with automatic migration
  - 41 tests passing
- PLAN.md: session-0 rule + architecture split (service = enforcement, tray agent = desktop work), tray relaunch + Exit challenge added to Phase 7, Phase 2 items marked

## 0.1.4 — 2026-09-18 19:33
- PLAN.md: added blocked-visit notifications (reason: permanent / limit reached / outside hours; customizable per reason, message, cooldown, format, per-item override) — section 1.1, Notifications screen 3.10, Phase 2 checklist

## 0.1.3 — 2026-09-18 19:30
- Added `scripts/create_shortcut.ps1`: creates double-click `Lockdown.lnk` shortcuts (Desktop + project folder) that start the GUI without a console window; `*.lnk` git-ignored

## 0.1.2 — 2026-09-18 19:30
- PLAN.md: added DoH lock, QUIC disable and closing open connections on block (section 1.1 + Phase 2 checklist)

## 0.1.1 — 2026-09-18 19:30
- Install/uninstall instructions (README + script headers) now run the script directly with a process-scoped execution policy, so they work from any folder and don't depend on `powershell.exe` being on PATH

## 0.1.0 — 2026-09-18 19:28
- Phase 1 (MVP) implemented:
  - Project setup: Python 3.13 venv, `requirements.txt`, `pytest.ini`, git repo
  - `src/db.py`: SQLite layer (`blocked_items`, `block_rules`, `settings`), shared DB in `C:\ProgramData\Lockdown\`
  - `src/blocker/hosts.py`: domain normalization, managed hosts-file section with `www.` variants, original-file backup, DNS flush
  - `src/service.py`: enforcement loop (every 5 s, repairs tampering, heartbeat, log file); installed as a SYSTEM scheduled task via `scripts/install_service.ps1`
  - GUI (`src/gui/`): dark sidebar with all tabs (future ones as placeholders), Blocking page with add-site form, auto display name, quick-add popular sites, blocked items list with remove, service status
  - System tray icon (green/red status, open, exit); closing the window minimizes to tray
  - `src/importer/popular.py`: built-in popular sites list
  - Tests for DB, hosts file logic and quick-list (15 passing)
- PLAN.md: Phase 1 marked done, decisions recorded, real Windows Service + ACL lockdown added to Phase 7, SVG icons added to Phase 8

## 0.0.9 — 2026-09-18 00:00
- Verified plan completeness for new-agent handoff
- Updated DB schema: unified `blocked_items` + `block_rules` tables (replaces separate sites/apps), added `notification_log`, `switch_events`, `antibypass`, `page_display` tables
- Updated file structure: new gui files (blocking.py, app_browser.py, antibypass.py, notifications.py, page_settings.py), assets/icons/ folder for SVGs
- All decisions, wireframes, features, architecture, phases, and schemas are in PLAN.md

## 0.0.8 — 2026-09-18 00:00
- Grid Challenge: removed macro detection (unnecessary — grid mechanic is enough), added variable chunk size (2–3 chars, mostly 3), changed to escalating lockout (5s → 10s → 15s)
- Settings split: global settings (theme, startup) always editable in Settings tab; per-feature settings live inside their own tabs and can be locked
- Added global search bar (Ctrl+K): searches across all tabs, navigates to result and highlights/pulses the matching setting
- Icons: switched from emojis to SVG icons (Lucide or Phosphor library) for a modern, clean look

## 0.0.7 — 2026-09-18 00:00
- Major restructure: websites and apps now treated as unified items (no separate tabs), blocking sub-tabs are by block TYPE (By Hours, By Limit, By Switches, Permanent, Temporary)
- Multiple block types stack on one item (e.g. Reddit: hours + limit + switches all at once)
- Items show friendly display names ("Reddit" not "reddit.com") and type badges (site/app)
- Added Grid Challenge (section 2.4): anti-macro/anti-paste 4x4 grid input, 3 chars per box, random highlight, error lockout
- Added Anti-Bypass Stacking (section 2.5): multiple bypass methods combine in sequence
- Added View vs Edit separation (section 2.6): all settings viewable always, editing requires passing challenge
- New dedicated Anti-Bypass screen (3.9): methods, strictness, status sub-tabs, lock icon UI
- New dedicated Notifications screen (3.10): limit alerts, daily/weekly/monthly reports, custom reminders, topic filters, format options (Windows toast vs in-app vs both), notification log
- Per-page display settings: every data page has a gear icon to customize which widgets/charts to show and their order
- Settings screen simplified (lock & reminders moved to their own tabs)
- Added App Browser as separate popup when adding apps to blocklist

## 0.0.6 — 2026-09-18 00:00
- Major UI/UX overhaul:
  - Dashboard: added today's timeline (hour-by-hour colored bar), 3 new stat cards (switches, blocks triggered, reminders done), quick glance section, next mode change indicator, trend arrows on all cards
  - Screen Time: added hourly heatmap (day × hour grid showing productive vs distracted patterns), new Switches tab with compulsive checking detection, new Goals tab with goal templates and milestones
  - Network Log: added graph view (connections per hour by process), blocked/allowed status column, right-click context menu expanded, summary bar
  - Blocklist: added sort/filter, progress bars for time-limited sites, expandable rows, bulk operations with checkboxes, pagination
  - Apps: added "today's usage" + switch count + category + "last used" info per app, sort by most used/last used
  - Limits: added progress rings, switch limits section
- Added QoL section: first-time onboarding wizard, global search (Ctrl+K), undo bar, drag & drop blocking, keyboard shortcuts, notes on blocks, compact mode, floating focus timer, notification center
- Added design note: GUI visuals will be designed by Claude Design with prompts generated during development

## 0.0.5 — 2026-09-18 00:00
- Expanded Frozen mode: choosable duration, escalating safety warnings for longer freezes, emergency unlock (long tedious phrase), uses sleep mode (not shutdown) to preserve open apps

## 0.0.4 — 2026-09-18 00:00
- Added switch count tracking (count focus-switches to apps/tabs, not just launches; with switch limits)
- Expanded reminders into full custom reminder system: fixed-time scheduling, random quotes (uploadable), double-check verification, snooze with limits
- Added universal lock principle: every setting in the app can be individually locked with anti-bypass
- Softened Frozen mode description (off by default, warning about risks for workers)

## 0.0.3 — 2026-09-18 00:00
- Researched competitor apps (Cold Turkey, Freedom, FocusMe, AppBlock, Stay Focused)
- Added anti-bypass features from research: block Task Manager, prevent time changes, block alt browsers, block system utils, auto-block on login/wake
- Added new lock mechanisms: random password lock, allowance within blocks
- Added 15 new ideas from competitor analysis: Frozen mode, launch count limits, PC unlock limits, Wi-Fi-based rules, community plans, AI coaching, accountability partner, panic button, reward system, tab limiter, new app watcher, notification blocker, keyword blocking, block strictness panel

## 0.0.2 — 2026-09-18 00:00
- Expanded PLAN.md with full UI wireframes for all screens (Dashboard, Block Websites, Block Apps, Screen Time, Network Log, Modes, Settings)
- Added screen time tracking feature (overall PC usage + per-app + limits)
- Added health reminders (sleep, breaks)
- Added modes/profiles system (Work, Study, Focus/Pomodoro, DND, Relax, custom)
- Added visual app browser (like Windows Settings > Apps)
- Added popular websites quick-list and GitHub blocklist import
- Added SQLite database schema
- Expanded to 8 implementation phases

## 0.0.1 — 2026-09-18 00:00
- Initial project plan created (PLAN.md)
- Project structure and architecture designed
- Feature set defined across 6 implementation phases
