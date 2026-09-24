# Lockdown

Windows app that blocks websites and apps, tracks all network activity, and makes it genuinely hard to cheat your own rules.

## What it does

- **Block websites** — permanently, on a schedule, with daily time limits, or temporarily
- **Block apps** — prevent games/launchers/apps from running or accessing the internet (Steam games are found
  automatically; blocking one closes everything running from its game folder)
- **Network log** — see every connection your PC makes (which app, where, when)
- **Anti-bypass** — locked settings, delayed unlocks, challenges, watchdog services — designed to beat your own willpower

## Status

- **Phase 1 (MVP) done:** permanent website blocking via the hosts file, enforcement service, GUI with blocklist + popular-sites quick-list, system tray.
- **Phase 2 done:** blocking by hours (allow-only or block-during, several time windows with their own days),
  daily time limits, temporary blocks, stacked rules, locked browser DoH/QUIC policies, closing open connections on block,
  blocked-visit notifications, tray agent at login, single instance, clock-change protection.
- **Phase 3 done:** app blocking (close politely then force after 10 s / block internet via firewall / both),
  app browser with icons, daily limits for apps, groups with shared rules + per-member customization,
  "N minutes allowed during blocked hours", warnings before blocks + reminders while in use + "block started".
- **Blocking UI:** five tabs — **Overview** (everything with rules as chips, sortable, Edit/Remove), **Groups** (list + editor), **Add** (pick a site or app,
  tick any number of blockers: hours, time limit, opening limit, permanent, temporary), **Site protection** (always-on
  lists, your own lists, blocked words, allowed-anyway) and **Calendar** (one row per blocked item with its blocked
  stretches as bars; Day / 3 days / Week; emergency unlocks marked; summary cards for the day). Site suggestions while typing,
  popular sites suggested while typing (small typos are fine). **Auto-save** is on by default; turn it off to stage changes until you
  press **Save changes** (unsaved items then show "Not applied").
- **Limits per day / week / month** (stackable, e.g. 2 h a day + max 8 h a week), a configurable **limit reset
  time** (Settings; a change never ends the current limit day, week or month early), and **Emergency unlock**
  (Blocking > Overview: unblock chosen items for 20 min; 3 uses per week by default, configurable in Settings).

- **Dashboard** (home page) and **Screen Time** (Overview / Apps / Websites / Switches, Today to 30 days) with
  categories (productive / neutral / distracting + your own, with your colours - click a category to change it). Your Steam games and purely-for-fun sites / apps (game
  launchers, streaming, short video) start as Distracting.
  Hover a chart for details. Themes: Dark, AMOLED, Light, Match Windows + any accent colour (Settings > Appearance).
- **Protection lists** (Blocking > Protection): always-on community lists of scam, phishing, malware and adult sites
  (gambling optional; ~4.3M sites from HaGeZi, Block List Project, Phishing Army, URLhaus), updated daily with a
  download progress, with exceptions and a "check a site" box. Blocked by the service's own DNS filter.
  Also **forced SafeSearch** (Google / Bing / DuckDuckGo, YouTube Restricted Mode) and **blocked words**: the browser
  tab in front is closed (or sent back) when its address or title has a blocked word (ready-made English and Polish
  adult word lists you can open and edit, your own words, exceptions).
- **Modes** (Phase 6a): Work / Study / Focus (Pomodoro) / Do Not Disturb / Relax + your own - each blocks a
  category plus picked sites/apps while it's on; start for a while, until a time or until stopped (optionally locked),
  or on a schedule; also from the tray menu.
- **Reminders** (Phase 6b): bedtime overlay, break reminders (optionally forced), your own reminders (interval / set
  times / random) with snooze, "did you really do it?" check and quotes; they wait while you're in a full-screen game.
- **Anti-Bypass** (Phase 7): loosening a block (removing it, higher limits, switching a protection list off, more
  emergency unlocks, quitting from the tray, ...) needs the challenges you turn on - typing a random phrase (no
  pasting; optionally word by word in a 3×3 grid of boxes picked at random - macros can't type blindly)
  and/or only during chosen hours; tightening is always instant. The tray app and the service come back
  within a minute if they're closed; a new time zone counts only after 24 hours; uninstalling asks for the challenge.
- **Phase 8a**: your own block lists by URL (with a preview), settings backup / import, screen-time CSV, a weekly
  summary notification, streaks, a month calendar on Screen Time, display settings (⚙) for Dashboard / Screen Time.
- **Network Log** (Phase 5): which app connected to which site in the last hour (names from the Windows DNS
  cache); table or graph, filters, export; click a row to block the site/app or copy it.

See [PLAN.md](PLAN.md) for the full plan and later phases.

## How it works

- The **GUI + tray agent** (runs as you, starts hidden at login) writes the blocklist to `C:\ProgramData\Lockdown\config.db`
  and shows notifications.
- The **enforcement service** (runs as SYSTEM) every 2 s:
  - evaluates the rules (permanent / by hours / temporary) and rewrites a marked `# >>> Lockdown` section in
    `C:\Windows\System32\drivers\etc\hosts`, redirecting blocked hostnames to `127.0.0.1`.
    Manual edits are repaired; the rest of the hosts file is never touched (backup: `C:\ProgramData\Lockdown\hosts.backup`);
  - keeps browser policies set that turn off DNS-over-HTTPS and QUIC in Chrome, Edge, Brave and Firefox
    (browsers then show "managed by your organization") — otherwise browsers could skip the hosts file;
  - closes already-open connections to a site right after it gets blocked.
- Apps are matched by exe name. The service checks running processes 4x per second: an app started while blocked
  is killed at once; one that was already open when its block began is asked to close (like clicking X) and
  force-closed after 10 s. "Minimize" keeps the app running but the tray agent minimizes it whenever it comes to the
  front; "Block internet" adds a Windows Firewall rule. "Also close its background processes" (with Close app)
  closes, once the app is gone, what it started and whatever runs from its install folder. Windows' own processes
  can't be blocked.
- Groups (Blocking > Groups) hold shared rules; members inherit them, a member can be customized, and a group
  daily limit is one total for all members. Warnings/reminders are set on the Notifications page.
- Daily limits: the tray agent reads the active browser tab's address (Windows UI Automation — Chrome, Edge, Brave,
  Firefox, no extension needed) and counts time on limited sites; the service blocks the site once the limit is used up,
  until the limit period ends (at the limit reset time).
- The service keeps its own trusted time (internet time + the Windows tick counter), so changing the Windows clock
  doesn't unlock anything; clock changes are logged.
- Site icons are downloaded once from Google's favicon service and cached in `%LOCALAPPDATA%\Lockdown\icons`
  (a letter icon is shown when offline).
- The service also listens on `127.0.0.1:80/443`: when a browser tries to open a blocked site, it records which
  site and why; the tray agent turns that into a notification (configurable on the Notifications page).
- **DNS filter** (protection lists - millions of sites, far too many for the hosts file, which makes Windows' DNS hang):
  the service runs a small DNS server on `127.0.0.1` / `::1` port 53. Connected network adapters are set to DNS
  "127.0.0.1, <their own DNS servers>" (+ `::1` for IPv6): listed sites get 127.0.0.1, everything else is passed to
  the network's DNS. If the service stops, Windows falls back to the network's DNS (internet keeps working, lists
  not enforced). Original settings are saved and restored on uninstall or with `service.py restore-dns`.
- Service log: `C:\ProgramData\Lockdown\lockdown.log`.

## Install

Run **`LockdownSetup.exe`** (build it with `scripts\build.ps1`, see below). It installs Lockdown into Program Files,
registers the "Lockdown Enforcer" Windows service and starts everything. Run a newer `LockdownSetup.exe` to update -
your settings, blocks and history (in `C:\ProgramData\Lockdown`) are kept. Uninstall from Settings > Apps
(needs the Anti-Bypass challenge if it's on).

## Build

```powershell
& "<project folder>\scripts\build.ps1"     # -> build\LockdownSetup.exe (and build\dist\Lockdown)
```

## Setup from source (development)

Requires Windows and Python 3.13.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Install the enforcement service (once, from an **Administrator** PowerShell — works from any folder;
re-run after changing service code):

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force; & "<project folder>\scripts\install_service.ps1"
```

Remove it with `scripts\uninstall_service.ps1` (also removes the browser policies and restores DNS settings; clear the blocklist first so the hosts entries are removed).

If pages ever stop loading because of Lockdown, run `scripts\repair_dns.ps1` the same way: it stops the service,
restores the network adapters' DNS settings and removes Lockdown's hosts-file section (run `install_service.ps1`
again to turn Lockdown back on).

## Usage

Double-click **Lockdown** on the Desktop (or `Lockdown.lnk` in the project folder) — no console window.
Create/recreate the shortcuts with `scripts\create_shortcut.ps1` (no admin needed).

Or from a terminal in the project folder:

```powershell
.\.venv\Scripts\pythonw.exe src\main.py
```

- The app registers itself to start hidden in the tray at login (`HKCU\...\Run\Lockdown`).
- Only one copy runs; launching it again just shows the window.
- Shortcuts: Esc closes pop-ups; in text boxes Ctrl+Z / Ctrl+Y, Ctrl+Backspace, Ctrl+A. Searches forgive small typos.
- Closing the window minimizes it to the tray; use the tray icon's **Exit** to quit (needs the Anti-Bypass challenge
  when it's on). Closed any other way (e.g. Task Manager), it's started again within a minute.
- Tray icon: green = service enforcing, red = service not running.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Set `LOCKDOWN_DATA_DIR` to use a different data folder (e.g. for testing).

Design: `design/Lockdown Dashboard & Screen Time.dc.html` (Claude Design). Colours/fonts live in `src/gui/theme.py`;
fonts (Inter, Barlow Condensed - SIL Open Font License, licences next to them) are in `assets/fonts`, the Lucide
sidebar icons (rendered from the design) in `assets/icons`.

## Known limitations

- The hosts file has no wildcards: blocking `reddit.com` also blocks `www.reddit.com`, but not other subdomains
  unless they are listed (the quick-list, and typing a known site's domain, include the common ones).
- Closing open connections works for IPv4 TCP only, and uses the site's IPs as resolved at block time
  (big sites on CDNs may use other IPs too).
- The DNS filter can be bypassed by changing the network adapter's DNS by hand (Lockdown switches it back within
  10 s) or by stopping the service; Windows may also briefly use the fallback DNS if the filter answers slowly.
- Anti-Bypass works in the app: someone with admin rights can still stop the scheduled tasks, or edit
  `config.db` directly (making settings service-only is planned).

## Licence

MIT — © 2026 Husarp. Free to use, change and share, keeping the copyright notice; see
[LICENSE](LICENSE). The bundled fonts (Barlow Condensed, Inter) keep their own licence, the SIL Open
Font License — the licence files are next to them in `assets/fonts/`.
