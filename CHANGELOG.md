# Changelog

## 0.8.0 — 2026-09-18 22:30
- "When blocked" for apps is now three checkboxes: Close app / Minimize / Block internet (Close and Minimize exclude each other; at least one needed); stored as flags, older values still work
- Status colours: blocked now = red, allowed now = green (also in Groups)
- Daily switch limit renamed "Daily opening limit" with a choice of what counts: **Launches / new visits** (default: apps = each start of the program, sites = coming back after N minutes away, default 5) or **Every switch** (previous behaviour); an app launched past its opening limit is closed at once
- Faster: the service re-checks rules every 2 s (was 5 s); Overview counters, countdowns and statuses update every 2 s in place
- 99 tests passing

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
