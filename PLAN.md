# Lockdown — Project Plan

> Windows digital wellbeing app — blocks websites & apps, tracks screen time & network activity, enforces healthy habits, and makes it genuinely hard to cheat.

---

> **IMPORTANT — Development rule:** Every feature/change must be presented to the user and explicitly approved before implementation. This plan contains many ideas — some may be modified or rejected. Nothing gets built without a green light first.

---

## 1. Core Features

### 1.1 Website/URL Blocking
- Add websites/URLs to a blocklist
- Block modes:
  - **Permanent** — blocked until manually removed (with anti-bypass)
  - **Scheduled** — blocked during specific hours (e.g. 9:00–17:00 on weekdays)
  - **Time-limited** — daily allowance (e.g. 30 min/day on Reddit, then blocked)
  - **Temporary** — block for X hours starting now
- Blocking mechanism: **hosts file** (`C:\Windows\System32\drivers\etc\hosts`)
  - Redirect blocked domains to `127.0.0.1`
  - Works system-wide — all browsers, all apps
  - Background service re-applies blocks every few seconds (prevents manual edits)
  - **DoH lock** — browsers with DNS-over-HTTPS skip the hosts file, so the service disables DoH via locked browser policies (Chrome/Edge `DnsOverHttpsMode=off`, Firefox `DNSOverHTTPS` policy with `Locked`) and re-applies them every loop
  - **Blocked visit notifications** — notify when you try to open a blocked site, with the reason: permanently blocked / daily limit (or switch limit) reached / outside allowed hours
    - Detection: a small local listener on `127.0.0.1:80/443` (where the hosts file sends blocked sites) reads the requested hostname (HTTP `Host` header / HTTPS SNI) — so it knows which site was attempted, without breaking HTTPS
    - Fully customizable (Notifications tab): on/off per reason, custom message per reason (`{site}`, `{reason}`, `{until}`), anti-spam cooldown per site, format (Windows toast / in-app / both), per-item override
    - Every attempt is logged (feeds "Blocks Triggered" stat card + notification log)
  - **Close open connections** — when a site gets blocked, the service resolves its real IPs and closes existing TCP connections to them (`SetTcpEntry`, IPv4); QUIC/HTTP3 (UDP) is disabled via Chrome/Edge `QuicAllowed=false` so browsers fall back to TCP
- **Popular websites quick-list** — pre-built lists of common time-wasters, one-click to add:
  - Social Media: Facebook, Instagram, Twitter/X, TikTok, Reddit, Snapchat, LinkedIn
  - Streaming: YouTube, Netflix, Twitch, Disney+, Hulu, HBO Max, Crunchyroll
  - Gaming: Steam Community, Epic Games, Roblox, gaming news sites
  - News/Feeds: CNN, BBC, Fox, Reddit, Hacker News, Buzzfeed
  - Shopping: Amazon, eBay, AliExpress, Temu, Shein
  - Other: Pinterest, Tumblr, 9GAG, Imgur
- **Import blocklists from GitHub** — import community-maintained hosts files:
  - [StevenBlack/hosts](https://github.com/StevenBlack/hosts) — 100k+ domains (ads, malware, adult, gambling, social media)
  - [oisd.nl](https://oisd.nl) — comprehensive domain blocklist
  - [Energized Protection](https://github.com/EnergizedProtection/block) — multi-category
  - "Import from URL" button — paste any hosts-file URL, app parses it and adds to blocklist
  - Categories when importing: ads, adult, gambling, malware/scam, social media, trackers
  - Can preview before importing (show count, sample domains)

### 1.2 App Blocking
- Block installed applications (games, launchers, etc.)
- **Visual app browser** — shows installed apps like Windows Settings > Apps:
  - Query Windows Registry (`HKLM\...\Uninstall`) for installed programs
  - Show app name, publisher, install size, install date
  - Extract app icons from `.exe` files for visual display
  - Search/filter bar to find apps quickly
  - Also show currently running processes with their names + icons
  - Toggle switch next to each app to block/unblock
- Implementation:
  - **Kill process** — service monitors running processes, kills blocked ones immediately
  - **Rename/move executable** — rename `.exe` so it can't launch (reversible by service)
  - **Firewall rules** — block internet access for specific apps (app works offline but can't connect)
- Same block modes as websites: permanent, scheduled, time-limited, temporary

### 1.3 Network Activity Log
- Live view of all internet connections from the PC
- Shows:
  - Process name + icon (e.g. `chrome.exe`, `steam.exe`, `discord.exe`)
  - Remote address / domain (reverse DNS lookup where possible)
  - Port, protocol (TCP/UDP)
  - Timestamp
  - Connection status (ESTABLISHED, LISTEN, TIME_WAIT, etc.)
  - Data direction (inbound/outbound)
- Uses Python `psutil` to enumerate all connections + owning process
- DNS query monitoring for domain-level logging
- Log history saved to local database (SQLite)
- Filterable & searchable in the GUI
- Export logs to CSV

### 1.4 Screen Time Tracking
- **Overall PC usage** — track total active time on computer per day
  - Detect activity via mouse/keyboard input monitoring (`ctypes` Win32 hooks or `pynput`)
  - Distinguish active vs idle time (configurable idle threshold, e.g. 5 min no input = idle)
  - Daily/weekly/monthly totals
- **Per-app time tracking** — track time spent in each application
  - Monitor active foreground window via `win32gui.GetForegroundWindow()`
  - Record which app is in focus and for how long
  - Categorize: Productive / Neutral / Distracting (user-configurable per app)
- **Switch count tracking** — count how many times you switch TO an app or browser tab
  - Every time an app/tab gains focus, increment its switch counter
  - Tracks context-switching habits (not just launches — every alt-tab, every tab click)
  - Stats: "You switched to Discord 47 times today" / "You switched to Reddit 23 times"
  - Set switch limits: "max 10 switches to Discord per day" — after that it gets blocked
  - Daily/weekly trends: are you switching less over time?
  - "Most distracting" ranking by switch count (high switches + low time = compulsive checking)
- **Per-website time tracking** — track time on specific domains
  - Via DNS/connection monitoring — know which domains are being accessed
  - Combined with browser being the foreground app = approximate site time
- **Usage limits** — set daily PC usage limits
  - Warning at 80% of limit
  - Soft lock at 100% (dismissable warning, logged)
  - Hard lock option (screen lock / forced shutdown after limit)
- **Graphs & statistics:**
  - Daily bar chart: hours per day over last 30 days
  - Pie chart: time distribution by app/category
  - Line graph: trends over weeks/months (are you improving?)
  - Per-app breakdown with rankings (most used → least used)
  - Comparison: this week vs last week
  - "Time saved" counter — estimate of time saved by blocking

### 1.5 Health Reminders
- **Sleep reminder**
  - Set a bedtime (e.g. 23:00)
  - Gentle reminder 30 min before
  - Stronger reminder at bedtime (fullscreen overlay, screen dimming)
  - After bedtime: increasingly aggressive — popup every 5 min, optional auto-lock
  - Wake-up time setting — blocks PC usage before wake time (optional)
- **Break reminders**
  - Configurable interval (default: every 45 min of continuous use)
  - 20-20-20 rule option (every 20 min, look at something 20 feet away for 20 sec)
  - Break duration setting (e.g. 5 min break)
  - Optional forced break (lock screen for break duration)
  - Break counter: "You've taken 6 breaks today"
- **Custom reminders** — fully configurable reminder system
  - Create any reminder: drink water, go outside, workout, stretch, wash up, eat, etc.
  - Scheduling options:
    - Interval-based: every X minutes of active use
    - Fixed time: at specific hours (e.g. "wash up" at 21:00, "workout" at 07:00)
    - Random: pop up randomly within a time window (keeps you on your toes)
  - **Random motivational quotes** — attach a quote pool to any reminder
    - Upload your own quotes (text file / paste list)
    - Shows a random quote from your pool each time the reminder fires
    - Can also use built-in quote packs (stoic, motivational, productivity, humor)
  - **Double-check verification** — after you click "Done", the app checks back after a configurable delay (e.g. 10 min) asking "Did you actually do it?"
    - If you click "No" or ignore it, the original reminder fires again
    - Tracks completion honesty over time in stats
  - **Snooze** — dismiss a reminder temporarily
    - Snooze duration configurable in settings (default: 5 min, options: 1/5/10/15/30 min)
    - Max snoozes per reminder configurable (e.g. max 3 snoozes, then it stays on screen)
    - Snooze can be locked/limited with the same anti-bypass mechanisms as blocked apps (delay, challenge, specific hours, etc.)
  - Each reminder has its own toggle + lock settings (same lock options as everything else)

### 1.6 Modes (Profiles)
- **Work mode** — blocks social media, gaming, streaming; allows productivity tools
- **Study mode** — blocks everything except educational sites + note-taking apps
- **Focus mode (Pomodoro)** — 25 min fully blocked, 5 min break, repeat; customizable intervals
- **Do Not Disturb** — blocks ALL non-essential sites/apps, maximum restriction
- **Relax mode** — everything unblocked (but still tracked)
- **Custom modes** — user creates their own profiles with custom blocklists
- Quick-switch from system tray right-click menu
- Can be scheduled (auto-activate "Work" at 9:00, "Relax" at 18:00)
- Each mode has its own blocklist, time limits, and reminder settings

---

## 2. Anti-Bypass / Lock Mechanisms

The whole point — make it genuinely hard to undo your own rules.

> **Universal lock principle:** EVERY setting, feature, and toggle in the app comes with an optional lock. Any setting can be protected with the same anti-bypass mechanisms (delay, challenge, specific hours, etc.). Changing a locked setting requires passing its lock challenge first. This applies to: blocklists, reminders, snooze settings, modes, limits, screen time limits, reminder toggles — everything.

### 2.1 Settings Change Restrictions
| Mechanism | Description |
|---|---|
| **Specific hours only** | Settings can only be changed during a configured window (e.g. 06:00–07:00 on Sundays) |
| **Delay unlock** | Want to unblock a site? Change takes effect after 10/30/60 min delay |
| **Type a long phrase** | Must type a deterrent phrase (e.g. "I am wasting my time") multiple times |
| **Grid challenge (anti-macro)** | Advanced anti-paste/anti-macro challenge — see section 2.4 below |
| **Solve math problems** | Must correctly answer 20+ random math questions to unlock |
| **Password you don't know** | Set a password, hash it, optionally email it to a friend — you can't see it |
| **Cooling-off popup** | If you visit a blocked site, a popup nags you every 60 seconds |
| **Nuclear mode** | No changes possible until a set date — absolutely locked down |
| **Random password lock** | Generate a random password, show it once on screen — you must write it down and walk to get it to unlock |
| **Allowance within blocks** | During a block session, allow X minutes of break (e.g. 5 min Reddit during a 2-hour work block) |

### 2.2 System-Level Protection
| Mechanism | Description |
|---|---|
| **Windows Service (SYSTEM account)** | Runs with highest privileges, even admin users can't easily stop it |
| **Service permission lockdown** | Use `sc sdset` to deny stop/delete permissions even for administrators |
| **Auto-restart on kill** | Service recovery options: restart on 1st, 2nd, 3rd failure (built-in Windows feature) |
| **Watchdog pattern** | Two services monitor each other — if one dies, the other restarts it |
| **Scheduled task backup** | A Windows Scheduled Task that re-enables the service if it's stopped |
| **Disguised service name** | Service name looks like a legit system service (not obviously "Lockdown") |
| **Hosts file guardian** | Service monitors hosts file for external edits, re-applies blocks within seconds |
| **Protected file permissions** | Config/block files have ACLs that deny deletion even for admin |
| **Block Task Manager** | Prevent opening Task Manager / `taskkill` to stop the service |
| **Prevent time/date changes** | Block system clock changes — common trick to expire timers early |
| **Block alternative browsers** | Detect and block unsupported/newly-installed browsers used to bypass filters |
| **Block system utilities** | Prevent access to hosts file editors, Control Panel network settings, DNS config |
| **Block on login/wake** | Auto-start a block session when user signs into Windows or wakes from sleep |

### 2.3 Uninstall Protection
- No entry in "Add/Remove Programs" — no easy GUI uninstall
- Uninstall requires a special procedure (run a specific command with a passphrase)
- Uninstall has a delay (e.g. 24 hours) before it actually removes anything
- Must type a long deterrent phrase to confirm uninstall
- The service + scheduled task + watchdog all need to be stopped in the right order

> **Reality check:** A determined admin with enough knowledge CAN always uninstall anything on Windows. The goal is **maximum friction** — making it so annoying that the lazy/impulsive part of your brain gives up before bypassing it.

### 2.4 Grid Challenge (Anti-Paste)

A challenge designed to defeat clipboard paste. No macro detection needed — the grid mechanic itself makes automation impractical (you'd need a screen-reading AI bot, which is way too much effort just to bypass a password).

**How it works:**
1. A long passphrase is generated (random characters, not normal letters — harder to memorize/screenshot)
2. A **4×4 grid of 16 input boxes** appears on screen
3. One random box **highlights** — you can ONLY type into the highlighted box
4. You must type **2–3 characters** (randomly chosen per step, mostly 3, sometimes 2) of the passphrase into that box
5. After those chars, a different random box highlights — type the next chunk there
6. Repeat until the entire passphrase is entered across multiple boxes
7. Paste is **disabled** in all boxes (block Ctrl+V, right-click paste, and programmatic clipboard access)

**On error (escalating lockout):**
- 1st wrong input → **5-second lockout**
- 2nd wrong input → **10-second lockout**
- 3rd and every subsequent wrong input → **15-second lockout**
- Optionally: wrong input resets the entire challenge from the beginning

**Why it works:** A bypass tool would need to: read the screen to find which box is highlighted, detect which 2–3 chars to type, click into that specific box, type only those chars, then detect the next highlighted box. The variable chunk size (2 or 3) adds extra unpredictability. This essentially requires a custom screen-reading bot — far beyond what a quick paste workaround can do.

### 2.5 Anti-Bypass Stacking

Multiple anti-bypass methods can be **combined/stacked** on any setting. They are checked in sequence — you must pass ALL of them to edit.

Examples:
- "Settings only editable at 12:00–12:20" **AND** "must type long phrase" **AND** "30-minute delay before changes apply"
- "Grid challenge" **AND** "solve 10 math problems"
- "Specific hours" **AND** "random password lock"

Each anti-bypass method is independently configurable per item or globally.

### 2.6 View vs Edit Separation

All settings, blocklists, schedules, and configurations are **always viewable** — you can browse and see everything at any time. But **editing is locked** behind the anti-bypass challenge(s). The UI clearly distinguishes view mode (greyed-out controls, lock icon) from edit mode (unlocked after passing challenge).

---

## 3. GUI / User Interface

### 3.1 Layout — Sidebar Navigation

Left sidebar with icons + labels, content area on the right. Dark theme by default.

**Icons:** Use **SVG icons** throughout the app (lightweight, scalable, crisp at any size). Use an icon library like [Lucide](https://lucide.dev/) or [Phosphor](https://phosphoricons.com/) — both are free, modern, consistent, and have Python-friendly SVG files. No emojis in the actual UI. During development, load SVGs via `customtkinter` image support or convert to PNG at build time via `cairosvg`/`Pillow`.

**Main tabs:**
- **Dashboard** — main screen, customizable charts/stats
- **Blocking** — what you block (sub-tabs by block type)
- **Anti-Bypass** — lock/protection settings (viewable always, editable only after passing challenge)
- **Screen Time** — usage stats, graphs, goals
- **Network Log** — connection monitor
- **Modes** — profiles (Work, Study, Focus, etc.)
- **Notifications** — alerts, reports, reminder settings
- **Settings** — global app settings only (theme, startup, shortcuts)

**Settings split:**
- **Global settings** (Settings tab) — always editable, no lock needed: theme, startup behavior, minimize to tray, keyboard shortcuts, import/export, about. These are app-level preferences that don't affect blocking.
- **Per-feature settings** — live inside their own tabs. Blocking settings are in the Blocking tab. Anti-bypass settings are in the Anti-Bypass tab. Notification settings are in the Notifications tab. These CAN be locked.

**Global search bar:** A search bar at the top of the app (always visible, across all tabs). Type any keyword and it:
- Shows matching results from all tabs (settings, blocked items, apps, features)
- Click a result → navigates to that tab and **highlights/pulses the matching setting** so you can find it instantly
- Keyboard shortcut: Ctrl+K to focus the search bar

```
┌──────────────┬──────────────────────────────────────────────┐
│              │  [🔍 Search settings, sites, apps...] Ctrl+K │
│  [svg] Dash  │──────────────────────────────────────────────│
│              │                                              │
│  [svg] Block │   Content area changes based on              │
│              │   selected sidebar item                      │
│  [svg] Lock  │                                              │
│              │   Each page has a [⚙] button for its own    │
│  [svg] Time  │   display settings (choose which charts,    │
│              │   widgets, stats to show, and their order)   │
│  [svg] Log   │                                              │
│              │                                              │
│  [svg] Mode  │                                              │
│              │                                              │
│  [svg] Notif │                                              │
│              │                                              │
│  [svg] Set   │                                              │
│              │                                              │
│──────────────│                                              │
│  [tray icon] │                                              │
│  Work Mode   │                                              │
│  ● Active    │                                              │
└──────────────┴──────────────────────────────────────────────┘
```

**Per-page display settings:** Every page that shows data (Dashboard, Blocking, Screen Time, Network Log) has a [⚙] gear icon in its header. Clicking it opens a panel where you can:
- Choose which widgets/charts/stats to show or hide
- Reorder them (drag up/down)
- Switch chart types (bar ↔ line ↔ pie)
- Set the default time range (today / 7 days / 30 days)
- Pick what's most important to YOU — the app adapts to your preferences

### 3.2 Screen: Home (Dashboard)

Overview of everything at a glance. Stat cards are clickable — click any card to jump to its detail screen.

```
┌─────────────────────────────────────────────────────────┐
│  LOCKDOWN                                    [_][□][X]  │
├──────────┬──────────────────────────────────────────────┤
│          │  Dashboard                    [Today▼] [⚡]  │
│  Home ●  │                                  date picker │
│          │  ── Stat Cards (clickable) ──────────────    │
│  Block   │  ┌──────────┐ ┌──────────┐ ┌──────────┐     │
│          │  │ Screen   │ │ Sites    │ │ Apps     │     │
│  Time    │  │ Time     │ │ Blocked  │ │ Blocked  │     │
│          │  │ 4h 23m   │ │ 47       │ │ 5        │     │
│  Log     │  │ ▼ 12%    │ │ active   │ │ active   │     │
│          │  └──────────┘ └──────────┘ └──────────┘     │
│  Modes   │  ┌──────────┐ ┌──────────┐ ┌──────────┐     │
│          │  │ Time     │ │ Focus    │ │ Streak   │     │
│  Settings│  │ Saved    │ │ Score    │ │          │     │
│          │  │ 2h 10m   │ │ 87%      │ │ 14 days  │     │
│          │  │ today    │ │ ▲ 5%     │ │ best: 21 │     │
│          │  └──────────┘ └──────────┘ └──────────┘     │
│          │  ┌──────────┐ ┌──────────┐ ┌──────────┐     │
│          │  │ Switches │ │ Blocks   │ │ Reminders│     │
│          │  │ Today    │ │ Triggered│ │ Done     │     │
│          │  │ 83       │ │ 12       │ │ 6 / 8    │     │
│          │  │ ▼ 20%    │ │ attempts │ │ today    │     │
│          │  └──────────┘ └──────────┘ └──────────┘     │
│          │                                              │
│          │  ── Today's Timeline ───────────────────     │
│          │  ┌──────────────────────────────────────┐    │
│          │  │ 08  09  10  11  12  13  14  15  now  │    │
│          │  │ ░░░ ███ ███ ██░ ░░░ ███ ██░ ███ ░░░  │    │
│          │  │ idle work work mix  lunch work mix work   │    │
│          │  │                                      │    │
│          │  │ ██ = productive  ░░ = idle  ▓▓ = dist│    │
│          │  └──────────────────────────────────────┘    │
│          │                                              │
│          │  ── Screen Time — Last 7 Days ──────────     │
│          │  ┌──────────────────────────────────────┐    │
│          │  │  8h ┤         daily limit             │    │
│          │  │  6h ┤ ·  ·  ·  ·  ·  ·  · ·  ·  · · │    │
│          │  │  4h ┤  ██ ██    ██                    │    │
│          │  │  2h ┤  ██ ██ ██ ██ ██ ██ ░░           │    │
│          │  │  0h ┤──────────────────────            │    │
│          │  │      Mo Tu We Th Fr Sa Su              │    │
│          │  │  Avg: 5h 10m     Trend: ▼ 12%         │    │
│          │  └──────────────────────────────────────┘    │
│          │                                              │
│          │  ── Quick Glance ───────────────────────     │
│          │  Top app: Chrome (2h 15m)                    │
│          │  Most switched: Discord (47×)                │
│          │  Current Mode: [Work Mode ▼]  ● Active      │
│          │  Next mode change: Relax at 17:00            │
│          │                                              │
├──────────┤                                              │
│ ● Active │                                              │
│ Work Mode│                                              │
└──────────┴──────────────────────────────────────────────┘
```

### 3.3 Screen: Blocking — Overview & Adding

Websites and apps are treated as **unified items** — no separate tabs for sites vs apps. Each item shows a friendly **display name** (e.g. "Reddit" not "reddit.com") and a **type badge** (small SVG icon or label: `[site]` / `[app]`).

Multiple block types can **stack** on one item (e.g. Reddit: blocked after 9 PM + 30 min daily limit + max 5 switches).

```
┌──────────────────────────────────────────────────────────┐
│  Blocking                                          [⚙]  │
│  [All] [By Hours] [By Limit] [By Switches] [Permanent]  │
│        [Temporary]                       <- sub-tabs     │
│                                                          │
│  ── All Tab (overview of everything blocked) ──          │
│                                                          │
│  ┌─ Add Item ─────────────────────────────────────┐      │
│  │ [reddit.com________] or [Browse apps...]       │      │
│  │ Display name: [Reddit__]  (auto-detected)      │      │
│  │ Block type: ☐ By hours  ☐ By limit  ☐ Switches │      │
│  │             ☐ Permanent ☐ Temporary              │     │
│  │ (can check multiple)                [+ Add]    │      │
│  └────────────────────────────────────────────────┘      │
│                                                          │
│  ┌─ Quick Add — Popular Sites ────────────────────┐      │
│  │ Social:    □ Facebook  □ Instagram  □ Twitter   │      │
│  │            □ TikTok    □ Reddit     □ Snapchat  │      │
│  │ Streaming: □ YouTube   □ Netflix    □ Twitch    │      │
│  │ Gaming:    □ Steam     □ Epic       □ Roblox    │      │
│  │ Shopping:  □ Amazon    □ eBay       □ AliExpress│      │
│  │                                                 │      │
│  │ [Import from GitHub ▼]  [Import from URL...]    │      │
│  │   ├ StevenBlack/hosts (ads+malware) — 100k+    │      │
│  │   ├ Adult content filter — 50k+                 │      │
│  │   ├ Gambling sites — 5k+                        │      │
│  │   └ Ad/tracker domains — 80k+                   │      │
│  └─────────────────────────────────────────────────┘      │
│                                                          │
│  ┌─ All Blocked Items ────────────────────────────┐      │
│  │ Sort: [Name ▼]  Filter: [All ▼]  [Search...]  │      │
│  │ [☐ Select all]                                  │      │
│  │                                                 │      │
│  │ Name       │Type│ Block Rules        │ Status   │      │
│  │────────────┼────┼────────────────────┼──────────│      │
│  │ ☐ Reddit   │ [site] │ Limit: 30m/day     │ 22/30m   │      │
│  │            │    │ Hours: off 21–07   │ ████░░   │      │
│  │            │    │ Switches: 5×/day   │ 3/5      │      │
│  │                                                 │      │
│  │ ☐ Discord  │ [app] │ Limit: 1h/day      │ 45/60m   │      │
│  │            │    │ Switches: 15×/day  │ 12/15    │      │
│  │                                                 │      │
│  │ ☐ Facebook │ [site] │ Permanent          │● Blocked │      │
│  │                                                 │      │
│  │ ☐ Steam    │ [app] │ Hours: off 9–17    │ Active   │      │
│  │                                                 │      │
│  │ ☐ YouTube  │ [site] │ Limit: 30m/day     │ 22/30m   │      │
│  │            │    │ Temporary: 2h left │          │      │
│  │                                                 │      │
│  │ Showing 5 of 52    [< 1 2 3 ... 11 >]          │      │
│  │ Selected: 0  [Bulk: Block ▼] [Bulk: Delete]    │      │
│  │                                                 │      │
│  │ Click row to expand: note, usage, history       │      │
│  └─────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────┘
```

### 3.4 Screen: Blocking — Sub-tabs (By Hours / By Limit / etc.)

Each sub-tab shows only items with that block type, and lets you configure that type's settings.

```
┌──────────────────────────────────────────────────────────┐
│  Blocking > By Hours                               [⚙]  │
│  [All] [By Hours ●] [By Limit] [By Switches] [Permanent]│
│                                                          │
│  Items blocked by specific hours:                        │
│                                                          │
│  Name       │Type│ Blocked Hours        │ Status         │
│  ───────────┼────┼──────────────────────┼────────────    │
│  Reddit     │ [site] │ Mon-Fri 21:00–07:00  │ Currently: on  │
│  Steam      │ [app] │ Mon-Fri 09:00–17:00  │ Currently: off │
│  Instagram  │ [site] │ Every day 23:00–08:00│ Currently: on  │
│                                                          │
│  [+ Add item to hour-based blocking]                     │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│  Blocking > By Limit                               [⚙]  │
│  [All] [By Hours] [By Limit ●] [By Switches] [Permanent]│
│                                                          │
│  Items with daily time limits:                           │
│                                                          │
│  Name       │Type│ Daily Limit │ Used    │ Progress      │
│  ───────────┼────┼─────────────┼─────────┼───────────    │
│  Reddit     │ [site] │ 30m         │ 22m     │ ██████░░░     │
│  Discord    │ [app] │ 1h          │ 45m     │ ██████░░░     │
│  YouTube    │ [site] │ 30m         │ 22m     │ ██████░░░     │
│                                                          │
│  [+ Add item to time-limited blocking]                   │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│  Blocking > By Switches                            [⚙]  │
│  [All] [By Hours] [By Limit] [By Switches ●] [Permanent]│
│                                                          │
│  Items with daily switch/open limits:                    │
│                                                          │
│  Name       │Type│ Max Switches│ Used    │ Progress      │
│  ───────────┼────┼─────────────┼─────────┼───────────    │
│  Reddit     │ [site] │ 5×/day      │ 3×      │ █████░░░░     │
│  Discord    │ [app] │ 15×/day     │ 12×     │ ███████░░     │
│                                                          │
│  [+ Add item to switch-limited blocking]                 │
└──────────────────────────────────────────────────────────┘
```

**Note:** One item (e.g. Reddit) can appear in multiple sub-tabs if it has multiple block types stacked. The "All" tab shows everything with all their rules combined.

### 3.4b Screen: Blocking — App Browser (when adding apps)

When clicking "Browse apps..." in the Add Item section:

```
┌──────────────────────────────────────────────────────────┐
│  Browse Installed Apps                          [✕ Close]│
│                                                          │
│  [Search apps..._______________]                         │
│  Show: [All ▼]  Sort: [Most used ▼]  Category: [All ▼]  │
│                                                          │
│  [icon] Discord           │● Running│ Today: 45m · 47×  │
│         Discord Inc. · 150 MB · Distracting              │
│                                               [+ Add]   │
│                                                          │
│  [icon] Steam             │● Running│ Today: 2h · 5×    │
│         Valve · 800 MB · Neutral                         │
│                                               [+ Add]   │
│                                                          │
│  [icon] Spotify           │  Idle   │ Today: 30m · 3×   │
│         Spotify AB · 200 MB · Neutral                    │
│                                               [+ Add]   │
│                                                          │
│  [icon] League of Legends │  Idle   │ Last: 3 days ago  │
│         Riot Games · 12 GB · Distracting                 │
│                                               [+ Add]   │
│                                                          │
│  ─── 127 apps ─── [Page 1/13] [<] [>]                   │
└──────────────────────────────────────────────────────────┘
```

### 3.5 Screen: Screen Time & Stats

```
┌──────────────────────────────────────────────────────────┐
│  Screen Time                                             │
│  [Overview] [Apps] [Websites] [Switches] [Goals] [Limits]│
│                                        <- tabs           │
│  ── Overview Tab ────────────────────────────────────    │
│                                                          │
│  Today: 5h 42m active  │  Idle: 2h 15m  │  Limit: 8h   │
│  ████████████████████░░░░░░░░░░  71% of daily limit     │
│                                                          │
│  ┌─ Today's Timeline ────────────────────────────┐       │
│  │ 06  08  10  12  14  16  18  20  22  00        │       │
│  │ ░░░ ███ ███ ░░░ ██▓ ███ ▓▓░ ░░░ ░░░ ░░░      │       │
│  │                                                │       │
│  │ Hover any block to see: app name, duration,    │       │
│  │ category. Click to jump to that time in logs.  │       │
│  │                                                │       │
│  │ ██ productive  ▓▓ distracting  ░░ idle         │       │
│  └────────────────────────────────────────────────┘       │
│                                                          │
│  ┌─ Daily Screen Time — Last 30 Days ─────────────┐      │
│  │  8h ┤ · · · · · · · · · · · · · · daily limit  │      │
│  │  6h ┤     ██          ██    ██                   │      │
│  │  4h ┤  ██ ██ ██    ██ ██ ██ ██ ██               │      │
│  │  2h ┤  ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ░░         │      │
│  │  0h ┤──────────────────────────────────          │      │
│  │       1  5     10    15    20    25   30          │      │
│  │                                                  │      │
│  │  Avg: 5h 10m/day    Trend: ▼ 12% vs last week   │      │
│  │  [7 days] [30 days] [90 days] [All time]         │      │
│  └──────────────────────────────────────────────────┘      │
│                                                          │
│  ┌─ Hourly Heatmap ──────────────────────────────┐       │
│  │ Which hours are you most active / distracted?  │       │
│  │                                                │       │
│  │      06 07 08 09 10 11 12 13 14 15 16 17 ...  │       │
│  │  Mon ░░ ░░ ██ ██ ██ ██ ░░ ██ ██ ▓▓ ██ ██     │       │
│  │  Tue ░░ ░░ ██ ██ ▓▓ ██ ░░ ██ ██ ██ ██ ▓▓     │       │
│  │  Wed ░░ ░░ ██ ██ ██ ██ ░░ ▓▓ ██ ██ ██ ██     │       │
│  │  ...                                           │       │
│  │                                                │       │
│  │  ░░ low  ▒▒ medium  ██ high  ▓▓ distracted    │       │
│  │  Your peak hours: 09:00–11:00 (most productive)│       │
│  │  Your weak hours: 15:00–16:00 (most distracted)│       │
│  └────────────────────────────────────────────────┘       │
│                                                          │
│  ┌─ Top Apps Today ──────────────────────────────┐       │
│  │     App          Time     Switches  Category   │       │
│  │  1. Chrome       2h 15m   34×       Neutral    │       │
│  │  2. VS Code      1h 30m   12×       Productive │       │
│  │  3. Discord        45m    47×       Distracting│       │
│  │  4. Spotify        30m     3×       Neutral    │       │
│  │  5. File Explorer  12m     8×       Neutral    │       │
│  │                                                │       │
│  │  Sort by: [Time ▼] (Time / Switches / Name)    │       │
│  └────────────────────────────────────────────────┘       │
│                                                          │
│  ┌─ Category Breakdown (Pie) ────────────────────┐       │
│  │        ┌────┐                                  │       │
│  │       /  45% \ Productive (2h 34m)             │       │
│  │      | 30%   | Neutral    (1h 43m)             │       │
│  │       \ 25% /  Distracting(1h 25m)             │       │
│  │        └────┘                                  │       │
│  └────────────────────────────────────────────────┘       │
│                                                          │
│  ┌─ Week Comparison ─────────────────────────────┐       │
│  │  This week          Last week       Change     │       │
│  │  Total:    34h      Total:    38h   ▼ 10%     │       │
│  │  Productive: 22h    Productive: 20h ▲ 10%     │       │
│  │  Distracting: 8h    Distracting: 12h▼ 33%     │       │
│  │  Switches:  420     Switches:  580  ▼ 28%     │       │
│  │  Avg/day:  4h 51m   Avg/day:  5h 26m▼ 11%     │       │
│  └────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────┘
```

### 3.5b Screen: Screen Time — Switches Tab

```
┌──────────────────────────────────────────────────────────┐
│  Screen Time > Switches                                  │
│                                                          │
│  Total switches today: 83  │  Avg: 104/day  │  ▼ 20%    │
│                                                          │
│  ┌─ Most Switched Apps ──────────────────────────┐       │
│  │  App           Switches  Avg Time/Visit  Status│       │
│  │  Discord       47×       0m 58s          !! compulsive│
│  │  Chrome        34×       3m 58s          normal│       │
│  │  Slack         12×       2m 15s          normal│       │
│  │  Spotify        3×       10m 00s         good  │       │
│  │  VS Code        2×       45m 00s         deep  │       │
│  │                                                │       │
│  │  !! = high switches + low time = compulsive    │       │
│  └────────────────────────────────────────────────┘       │
│                                                          │
│  ┌─ Switch Pattern — Today ──────────────────────┐       │
│  │  08:00  ████  (4 switches)                     │       │
│  │  09:00  ██████████  (10 switches)              │       │
│  │  10:00  ████████  (8 switches)                 │       │
│  │  11:00  ██████████████  (14 switches) ← worst  │       │
│  │  12:00  ██  (2 switches) — lunch               │       │
│  │  13:00  ████████████  (12 switches)            │       │
│  │  14:00  ██████  (6 switches)                   │       │
│  │                                                │       │
│  │  Most chaotic hour: 11:00 (14 switches)        │       │
│  │  Longest uninterrupted focus: 45m (VS Code)    │       │
│  └────────────────────────────────────────────────┘       │
│                                                          │
│  ┌─ Switches — Last 7 Days ──────────────────────┐       │
│  │  150┤                                          │       │
│  │  100┤  ██          ██                           │       │
│  │   50┤  ██ ██ ██ ██ ██ ██ ░░                     │       │
│  │   0 ┤──────────────────────                     │       │
│  │      Mo Tu We Th Fr Sa Su                       │       │
│  └────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────┘
```

### 3.5c Screen: Screen Time — Goals Tab

```
┌──────────────────────────────────────────────────────────┐
│  Screen Time > Goals                                     │
│                                                          │
│  ┌─ Active Goals ────────────────────────────────┐       │
│  │                                                │       │
│  │  ◉ Reduce screen time to < 5h/day             │       │
│  │    Progress: 4 of 7 days this week  ████░░░    │       │
│  │    Current avg: 4h 51m  (target: 5h 00m) ✓    │       │
│  │                                                │       │
│  │  ◉ Keep Discord under 30 min/day              │       │
│  │    Progress: 5 of 7 days this week  █████░░    │       │
│  │    Current avg: 28m  (target: 30m) ✓           │       │
│  │                                                │       │
│  │  ◉ Reduce app switches to < 60/day            │       │
│  │    Progress: 2 of 7 days this week  ██░░░░░    │       │
│  │    Current avg: 92  (target: 60) ✗             │       │
│  │                                                │       │
│  │  ◉ No social media before 12:00               │       │
│  │    Progress: 6 of 7 days this week  ██████░    │       │
│  │    Streak: 3 days                              │       │
│  │                                                │       │
│  │  [+ Add Goal]                                  │       │
│  └────────────────────────────────────────────────┘       │
│                                                          │
│  ┌─ Goal Templates ──────────────────────────────┐       │
│  │  Digital Minimalist: < 4h/day, < 50 switches   │       │
│  │  Social Detox: 0 min social media for 7 days   │       │
│  │  Deep Worker: < 30 switches, 4h+ productive    │       │
│  │  Gradual Reduction: -10% screen time per week   │       │
│  │  [Apply Template]                               │       │
│  └────────────────────────────────────────────────┘       │
│                                                          │
│  ┌─ Milestones ──────────────────────────────────┐       │
│  │  🏆 100 hours saved (reached Sep 10)           │       │
│  │  🏆 14-day streak (reached Sep 15)             │       │
│  │  ○  30-day streak (16 days to go)              │       │
│  │  ○  500 hours saved (382h to go)               │       │
│  └────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────┘
```

### 3.6 Screen: Screen Time — Limits Tab

```
┌──────────────────────────────────────────────────────────┐
│  Screen Time > Limits                                    │
│                                                          │
│  ┌─ Overall PC Usage Limit ───────────────────────┐      │
│  │                                                 │      │
│  │     ╭───╮                                       │      │
│  │    ( 71% )   5h 42m / 8h 00m                    │      │
│  │     ╰───╯    2h 18m remaining                   │      │
│  │                                                 │      │
│  │  Daily limit: [8h 00m ▼]                        │      │
│  │  At limit:    ○ Warn only  ○ Soft lock  ● Hard lock   │
│  │  Warning at:  [80]% of limit                    │      │
│  └─────────────────────────────────────────────────┘      │
│                                                          │
│  ┌─ Per-App Limits ───────────────────────────────┐      │
│  │  App         │ Time Limit  │ Used   │ Progress  │      │
│  │──────────────┼─────────────┼────────┼───────────│      │
│  │  Chrome      │ 3h 00m      │ 2h 15m │ ███████░░ │      │
│  │  Discord     │ 1h 00m      │ 0h 45m │ ██████░░░ │      │
│  │  YouTube     │ 0h 30m      │ 0h 22m │ ██████░░░ │      │
│  │                                                 │      │
│  │  [+ Add time limit]                             │      │
│  └─────────────────────────────────────────────────┘      │
│                                                          │
│  ┌─ Per-App Switch Limits ────────────────────────┐      │
│  │  App         │ Max Switches│ Used   │ Progress  │      │
│  │──────────────┼─────────────┼────────┼───────────│      │
│  │  Discord     │ 15×/day     │ 12×    │ ███████░░ │      │
│  │  Reddit      │ 5×/day      │ 3×     │ █████░░░░ │      │
│  │                                                 │      │
│  │  [+ Add switch limit]                           │      │
│  └─────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────┘
```

### 3.7 Screen: Network Log

```
┌──────────────────────────────────────────────────────────┐
│  Network Log                 [Table] [Graph]  [● Live]   │
│                                                [Export]   │
│  Filter: [All processes ▼] [All domains ▼] [Search..]   │
│  Date:   [Today ▼]  Show: [All ▼] (All/Blocked/Allowed) │
│                                                          │
│  ── Table View ──                                        │
│  ┌────────────────────────────────────────────────┐      │
│  │ Time     │ Process      │ Domain/IP       │Port│ St  │
│  │──────────┼──────────────┼─────────────────┼────┼──── │
│  │ 14:32:01 │ chrome.exe   │ reddit.com      │443 │ ✗   │
│  │ 14:32:00 │ discord.exe  │ discord.gg      │443 │ ✓   │
│  │ 14:31:58 │ steam.exe    │ steampowered.com│443 │ ✓   │
│  │ 14:31:55 │ spotify.exe  │ scdn.co         │443 │ ✓   │
│  │ 14:31:52 │ svchost.exe  │ microsoft.com   │443 │ ✓   │
│  │ 14:31:50 │ chrome.exe   │ youtube.com     │443 │ ✗   │
│  │ ...                                              │    │
│  │ St: ✓ = allowed, ✗ = blocked                     │    │
│  │ Showing 1,247 connections today                   │    │
│  │                                                   │    │
│  │ Right-click any row:                              │    │
│  │   [Block this domain] [Block this app]            │    │
│  │   [Show all from this app] [Copy domain]          │    │
│  │   [Lookup domain info]                            │    │
│  └───────────────────────────────────────────────────┘    │
│                                                          │
│  ── Graph View (toggle) ──                               │
│  ┌────────────────────────────────────────────────┐      │
│  │  Connections per hour — Today                   │      │
│  │  200┤                                           │      │
│  │  150┤        ██                                  │      │
│  │  100┤  ██ ██ ██ ██       ██ ██                   │      │
│  │   50┤  ██ ██ ██ ██ ██ ██ ██ ██ ██ ░░             │      │
│  │   0 ┤──────────────────────────────               │      │
│  │      08 09 10 11 12 13 14 15 16                   │      │
│  │                                                   │      │
│  │  By process: ██ chrome  ██ discord  ██ steam      │      │
│  └───────────────────────────────────────────────────┘    │
│                                                          │
│  ── Summary Bar ──                                       │
│  Total: 1,247 │ Blocked: 89 │ Top: chrome (523)         │
│  Unique domains: 156 │ Unique apps: 23                   │
└──────────────────────────────────────────────────────────┘
```

### 3.8 Screen: Modes

```
┌──────────────────────────────────────────────────────────┐
│  Modes                                                   │
│                                                          │
│  Current: [Work Mode]  ● Active since 09:00              │
│                                                          │
│  ┌─ Built-in Modes ───────────────────────────────┐      │
│  │                                                 │      │
│  │  [icon] Work Mode          [Activate]           │      │
│  │  Blocks: social, gaming, streaming              │      │
│  │  Schedule: Mon-Fri 09:00–17:00 (auto)           │      │
│  │                                                 │      │
│  │  [icon] Study Mode         [Activate]           │      │
│  │  Blocks: everything except edu sites + notes    │      │
│  │  Schedule: none (manual)                        │      │
│  │                                                 │      │
│  │  [icon] Focus (Pomodoro)   [Start Session]      │      │
│  │  25 min work / 5 min break / 4 cycles           │      │
│  │  Blocks: ALL non-essential                      │      │
│  │                                                 │      │
│  │  [icon] Do Not Disturb     [Activate]           │      │
│  │  Blocks: everything except whitelist            │      │
│  │                                                 │      │
│  │  [icon] Relax              [Activate]           │      │
│  │  Blocks: nothing (tracking only)                │      │
│  │                                                 │      │
│  └─────────────────────────────────────────────────┘      │
│                                                          │
│  ┌─ Custom Modes ─────────────────────────────────┐      │
│  │  [icon] My Gaming Time     [Activate]  [Edit]   │      │
│  │  Blocks: social, work tools                     │      │
│  │                                                 │      │
│  │  [+ Create Custom Mode]                         │      │
│  └─────────────────────────────────────────────────┘      │
│                                                          │
│  ┌─ Mode Schedule ────────────────────────────────┐      │
│  │  Mon-Fri: Work 09:00–17:00, Relax 17:00–23:00  │      │
│  │  Sat-Sun: Relax all day                         │      │
│  │  [Edit Schedule...]                             │      │
│  └─────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────┘
```

### 3.9 Screen: Anti-Bypass (Lock Tab)

All settings here are **viewable** at any time but **editable only after passing the current challenge**. Controls are greyed out with a 🔒 icon until unlocked.

```
┌──────────────────────────────────────────────────────────┐
│  Anti-Bypass                                 🔒 Locked   │
│  [Methods] [Strictness] [Status]        <- sub-tabs     │
│                                                          │
│  ── Active Methods (stacked — ALL must pass) ────       │
│                                                          │
│  ☑ Specific hours only                                   │
│     Window: [Sunday 06:00–07:00 ▼]         🔒            │
│                                                          │
│  ☑ Grid challenge (anti-macro)                           │
│     Phrase length: [30 chars ▼]                          │
│     Lockout on error: [15 sec ▼]         🔒              │
│                                                          │
│  ☑ Delay unlock                                          │
│     Delay: [30 minutes ▼]               🔒               │
│                                                          │
│  ☐ Solve math problems                  🔒               │
│  ☐ Random password lock                 🔒               │
│  ☐ Nuclear mode — Until: [date]         🔒               │
│                                                          │
│  ── Strictness (sub-tab) ──                              │
│  ☑ Block Task Manager                   🔒               │
│  ☑ Prevent time/date changes            🔒               │
│  ☑ Block alternative browsers           🔒               │
│  ☐ Block system utilities               🔒               │
│  ☑ Block on login/wake                  🔒               │
│                                                          │
│  ── Status ──                                            │
│  Currently: 🔒 LOCKED                                    │
│  Next edit window: Sunday 06:00                          │
│  Methods active: 3 (hours + grid + delay)                │
│                                                          │
│  [🔓 Unlock to edit] ← starts the challenge sequence    │
└──────────────────────────────────────────────────────────┘
```

### 3.10 Screen: Notifications

Dedicated tab for all notification/alert settings — what you receive, when, and how.

```
┌──────────────────────────────────────────────────────────┐
│  Notifications                                     [⚙]  │
│  [Settings] [Log]                           <- sub-tabs │
│                                                          │
│  ── Settings Tab ──                                      │
│                                                          │
│  ┌─ Limit Alerts ────────────────────────────────┐       │
│  │  Warn when approaching limit:  [ON]            │       │
│  │  Warning threshold:            [80]%           │       │
│  │  Warn when limit reached:      [ON]            │       │
│  │  Warn on blocked site visit:   [ON]            │       │
│  │  Format: ○ Windows toast  ● In-app  ○ Both     │       │
│  └────────────────────────────────────────────────┘       │
│                                                          │
│  ┌─ Blocked Visit Alerts (see 1.1) ──────────────┐       │
│  │  Notify when I open a site that is:            │       │
│  │    ☑ Permanently blocked                       │       │
│  │    ☑ Over its daily limit / switch limit       │       │
│  │    ☑ Outside its allowed hours                 │       │
│  │  Message per reason: [Reddit is blocked      ] │       │
│  │    placeholders: {site} {reason} {until}       │       │
│  │  Don't repeat for same site within: [5 min ▼]  │       │
│  │  Format: ○ Windows toast  ● In-app  ○ Both     │       │
│  │  Per-item override: in item's expanded row     │       │
│  └────────────────────────────────────────────────┘       │
│                                                          │
│  ┌─ Reports ─────────────────────────────────────┐       │
│  │  Daily report:    [ON]   Time: [21:00]         │       │
│  │  Weekly report:   [ON]   Day: [Monday 09:00]   │       │
│  │  Monthly report:  [OFF]                        │       │
│  │  Format: ○ Windows toast  ○ In-app  ● Both     │       │
│  │  Include: ☑ Screen time  ☑ Blocks triggered    │       │
│  │           ☑ Switches     ☑ Goals progress      │       │
│  │           ☑ Streak       ☑ Comparison          │       │
│  └────────────────────────────────────────────────┘       │
│                                                          │
│  ┌─ Reminders ───────────────────────────────────┐       │
│  │  Sleep reminder:   [ON]  Bedtime: [23:00]      │       │
│  │  Break reminder:   [ON]  Every: [45 min]       │       │
│  │  Custom reminders:                              │       │
│  │    ☑ Drink water     Every 60 min    [✎] [✕]   │       │
│  │    ☑ Stretch         Every 90 min    [✎] [✕]   │       │
│  │    ☑ Wash up         At 21:00        [✎] [✕]   │       │
│  │    ☑ Workout         At 07:00        [✎] [✕]   │       │
│  │  [+ Add custom reminder]                       │       │
│  │  Format: ○ Windows toast  ● In-app  ○ Both     │       │
│  └────────────────────────────────────────────────┘       │
│                                                          │
│  ┌─ Notification Topics ─────────────────────────┐       │
│  │  Choose what you get notified about:           │       │
│  │  ☑ Blocking events (site/app blocked)          │       │
│  │  ☑ Limit warnings (approaching limit)          │       │
│  │  ☑ Mode changes (auto-switch)                  │       │
│  │  ☑ Streak milestones                           │       │
│  │  ☑ Goal achievements                           │       │
│  │  ☑ Anti-bypass attempts                        │       │
│  │  ☐ Service status changes                      │       │
│  └────────────────────────────────────────────────┘       │
│                                                          │
│  ── Log Tab ── (history of all notifications)            │
│  ┌────────────────────────────────────────────────┐       │
│  │ Time     │ Type        │ Message                │      │
│  │──────────┼─────────────┼────────────────────────│      │
│  │ 14:30    │ Limit warn  │ Reddit: 80% of limit   │      │
│  │ 14:15    │ Block       │ youtube.com blocked     │      │
│  │ 13:00    │ Reminder    │ Drink water             │      │
│  │ 12:00    │ Mode change │ Auto: Work → Relax      │      │
│  │ 09:00    │ Report      │ Daily report ready      │      │
│  │ ...      │             │                         │      │
│  │                                                  │      │
│  │ Filter: [All types ▼]  [Search...]  [Export]     │      │
│  └──────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────┘
```

### 3.11 Screen: Settings

General app settings (lock and notification settings have moved to their own tabs).

```
┌──────────────────────────────────────────────────────────┐
│  Settings                                                │
│  [General] [Import/Export] [About]              <- tabs  │
│                                                          │
│  ── General ──────────────────────────────────────       │
│  Theme:              [Dark ▼]                            │
│  Start with Windows: [ON]                                │
│  Minimize to tray:   [ON]                                │
│  Start minimized:    [OFF]                               │
│  Keyboard shortcuts: [Configure...]                      │
│                                                          │
│  ── Import / Export ──────────────────────────────       │
│  [Import blocklist from URL...]                          │
│  [Import from GitHub (popular lists)...]                 │
│  [Export my settings...]                                 │
│  [Export logs (CSV)...]                                  │
│  [Import community blocking plan...]                     │
│                                                          │
│  ── About ────────────────────────────────────────       │
│  Lockdown v1.0.0                                        │
│  Service status: ● Running                               │
│  Watchdog status: ● Running                              │
│  [Uninstall...] (requires challenge)                     │
└──────────────────────────────────────────────────────────┘
```

### 3.12 System Tray
- App minimizes to system tray (taskbar corner icon)
- Right-click menu:
  - Open Lockdown
  - Current mode: Work Mode ●
  - Switch mode → [Work / Study / Focus / DND / Relax / Custom...]
  - Start Focus Session (Pomodoro)
  - Quick-block → [enter URL]
  - Status: 47 sites blocked, 5 apps blocked
  - Screen time today: 4h 23m
  - Exit (requires challenge if lock is on)
- Tray icon shows status color (green = enforcing, yellow = relax mode, red = service down)
- Tooltip on hover: "Lockdown — Work Mode — 4h 23m today"

### 3.13 UX / Quality of Life

**First-time onboarding wizard:**
1. Welcome screen — "What do you want to focus on?" (work / study / general productivity)
2. Quick-block popular sites — checkboxes by category
3. Set a daily screen time goal
4. Pick a mode — choose a default starting mode
5. Set bedtime + break reminders
6. Choose lock strength (none / gentle / medium / strict / nuclear)
7. Done — dashboard opens

**Global UX features:**
- **Global search** — search bar accessible from any screen (Ctrl+K): search sites, apps, settings, logs — jump to anything
- **Undo bar** — after any action (block, unblock, delete), show a 5-second "Undo" toast at bottom — prevents accidental changes
- **Batch operations** — select multiple sites/apps with checkboxes, bulk block/unblock/delete/change mode
- **Drag & drop** — drag a URL from the browser into the Lockdown window to quick-block it
- **Notes on blocks** — add a personal note to any block ("this wastes my mornings", "only for weekends")
- **Keyboard shortcuts** — Ctrl+N = add block, Ctrl+F = search, Ctrl+1/2/3 = switch sidebar tabs, Esc = close popups
- **Color-coded categories** — each category (Productive/Neutral/Distracting) has a consistent color across all views
- **Tooltips everywhere** — hover any stat card, chart bar, or setting to see extra context
- **Clickable stat cards** — every card on the dashboard links to its detail screen
- **Date picker on all stats** — switch between Today / Yesterday / This week / Custom date range on any stats view
- **Progress rings** — circular progress indicators for limits (more visual than progress bars)
- **Smooth animations** — transitions between views, satisfying feedback when blocking/unblocking
- **Compact mode** — smaller window option for keeping Lockdown visible while working (shows just timer + key stats)
- **Floating focus timer** — small always-on-top widget during Pomodoro showing time remaining (draggable, minimal)
- **Notification center** — in-app log of all reminders, block triggers, and events (bell icon in header)

**Design note:** GUI layout and visuals will be designed by Claude Design. During development, create design prompts describing each screen's purpose, components, and data so the designer can produce proper mockups.

---

## 4. Tech Stack

| Component | Technology |
|---|---|
| **GUI** | Python + `customtkinter` (modern-looking tkinter) |
| **Charts/Graphs** | `matplotlib` embedded in tkinter (or `tkchart` for lighter option) |
| **Background service** | Python + `pywin32` (Windows Service) |
| **Database** | SQLite (blocklists, logs, settings, screen time data) |
| **Network monitoring** | `psutil` (process + connection enumeration) |
| **Screen time tracking** | `win32gui` (foreground window) + `ctypes` (input activity) |
| **App enumeration** | `winreg` (registry) + `psutil` (running processes) |
| **Blocking** | Hosts file manipulation + process killing |
| **Packaging** | `PyInstaller` (single `.exe` for GUI + service) |
| **Firewall** | `netsh advfirewall` commands (for app-level blocking) |
| **Icons** | `win32api` / `PIL` for extracting app icons from `.exe` files |

---

## 5. Architecture

> **Session 0 rule:** the service runs as SYSTEM in Windows' isolated "session 0" — it has no access to the
> user's desktop. So it **cannot** show notifications/popups, see the foreground window, or detect mouse/keyboard
> activity. Everything that needs the desktop lives in the **tray agent** (the GUI process, running as the user,
> auto-started at login). The service does enforcement; the tray agent does tracking + notifications and
> reports to the service through `config.db`.

```
┌─────────────────────────────────────────────────┐
│       GUI App + Tray Agent (one process)        │
│  (customtkinter — runs as user, starts at login,│
│   single instance, window hides to tray)        │
│                                                 │
│   Tray agent (always running):                  │
│   - Shows notifications (blocked visits, limits)│
│   - Tracks foreground window + switches         │
│   - Tracks mouse/keyboard activity (idle)       │
│   - Fires reminders (sleep/break/custom)        │
│   - Shows lock/limit overlays                   │
│                                                 │
│  ┌─────────┐ ┌───────┐ ┌──────┐ ┌───────────┐  │
│  │Dashboard│ │Block  │ │Screen│ │  Network  │  │
│  │  Stats  │ │List   │ │Time  │ │  Log View │  │
│  │  Graphs │ │Apps   │ │Limits│ │           │  │
│  └────┬────┘ └───┬───┘ └──┬───┘ └─────┬─────┘  │
└───────┼──────────┼────────┼───────────┼─────────┘
        │          │        │           │
        ▼          ▼        ▼           ▼
┌─────────────────────────────────────────────────┐
│            config.db (SQLite)                   │
│  blocked_sites | blocked_apps | schedules |     │
│  settings | network_logs | screen_time |        │
│  app_usage | modes | reminders                  │
└───────────────────┬─────────────────────────────┘
                    │
        ┌───────────▼─────────────────────────────┐
        │   Lockdown Service                      │
        │   (SYSTEM account)                      │
        │                                         │
        │   - Enforces hosts file                 │
        │   - Locks browser DoH/QUIC policies     │
        │   - Closes connections to blocked sites │
        │   - Blocked-visit listener (127.0.0.1)  │
        │   - Kills blocked processes             │
        │   - Monitors connections (psutil)       │
        │   - Logs network activity               │
        │   - Guards config file                  │
        │   - Enforces screen time limits         │
        │     (decides; tray agent shows overlay) │
        │   - Relaunches tray agent if killed     │
        │   - Auto-restarts on kill               │
        └───────────┬─────────────────────────────┘
                    │
        ┌───────────▼─────────────────────────────┐
        │   Watchdog Service                      │
        │   (backup — restarts main)              │
        └─────────────────────────────────────────┘
```

---

## 6. Additional Ideas

- **Whitelist mode** — block ALL websites/apps except a configured whitelist (inverse mode)
- **Categories** — pre-built lists: "Social Media", "Gaming", "News", "Streaming" — block a whole category with one click
- **Motivational block page** — when you try to visit a blocked site, show a custom message/quote instead of a generic error
- **Gradual reduction** — slowly reduce allowed daily time over weeks (e.g. 60 min → 45 → 30)
- **Streak tracker** — "You've stayed focused for 14 days straight!" with streak-break warnings
- **Emergency bypass** — allows unblocking BUT: logs it prominently, resets your streak, sends you a shame email
- **Export reports** — weekly/monthly PDF/CSV of your usage patterns
- **Startup enforcement** — service starts with Windows, GUI is optional
- **Block page redirect** — instead of just failing to load, redirect to a locally-hosted page with your custom message
- **Clipboard monitoring** — detect if user copies a blocked URL and warn them
- **Browser history scanning** — check if blocked sites were visited via other means (VPN, proxy)
- **Gamification** — earn points for staying focused, lose points for bypassing; leaderboard with friends?
- **Weekly digest** — popup/notification every Monday with last week's stats summary
- **Heatmap** — calendar heatmap showing productive vs unproductive days (like GitHub contributions)
- **App categorization AI** — auto-categorize newly installed apps as productive/neutral/distracting
- **Frozen mode (optional, off by default)** — puts the PC into sleep mode (not shutdown — doesn't close apps) and re-sleeps if you try to wake it, until the timer expires. **Warning:** risky for people who work — if bad apps are already blocked, there's usually no need to block everything. Only for extreme use cases. Should have a safety whitelist (e.g. always allow work apps even in frozen mode).
  - **Choose duration** — pick how long to freeze: 30 min, 1h, 2h, 3h, custom. Example use case: "I want to go to sleep, freeze for 3 hours."
  - **Escalating safety for longer durations** — the longer the freeze, the more warnings/confirmations:
    - ≤30 min: simple confirm dialog
    - 1–2h: confirm + "Are you sure?" + countdown (10 sec to cancel)
    - 3h+: confirm + type a phrase + 30 sec countdown + final "LAST CHANCE" popup
  - **Emergency unlock** — always available, but intentionally extremely annoying:
    - Must type a very long, tedious phrase perfectly (e.g. a 200-character sentence, no typos allowed)
    - Phrase gets harder/longer the longer the freeze duration was
    - Emergency unlock is logged, counts against your streak, shown in stats as a "freeze break"
  - Uses **sleep mode** (not shutdown) — apps stay open, no data loss, just locks you out for the duration
- **Launch count limits** — not just time limits: "you can only open Discord 3 times today", "Reddit can only be opened 5 times"
- **PC unlock limit** — "you can only unlock your computer 10 times today" — fights compulsive checking
- **Block strictness settings panel** — centralized panel to toggle bypass prevention: block Task Manager, block time changes, block alt browsers, block system utils
- **Wi-Fi / network-based rules** — different blocking profiles depending on which Wi-Fi you're connected to (home vs work vs coffee shop)
- **Community-shared blocking plans** — export/import blocking profiles, share with others online, browse community presets
- **AI coaching** — analyze usage patterns, detect burnout risk, suggest schedule changes, personalized weekly tips (like FocusMe's AI Coach)
- **Accountability partner** — send weekly reports to a friend/partner via email; they see your stats and bypass attempts
- **Panic button** — one-click "block everything NOW for 2 hours" when you feel willpower slipping
- **Reward system** — after completing a focus session or hitting a streak milestone, unlock a small reward (e.g. 10 min guilt-free browsing)
- **Browser tab limiter** — limit max number of open tabs (e.g. max 5 tabs) to reduce distraction sprawl
- **New app watcher** — detect when a new app is installed and prompt: "Categorize as Productive / Neutral / Distracting?" — auto-block if distracting
- **Notification blocker** — suppress Windows toast notifications from distracting apps during focus/work modes
- **Keyword blocking** — block pages containing certain keywords (e.g. block any page with "memes", "funny", "shorts")

---

## 7. Implementation Phases

### Phase 1 — MVP (Core blocking)
- [x] Project setup (Python 3.13 venv, folder structure, git)
- [x] SQLite database schema (`blocked_items`, `block_rules`, `settings` — other tables arrive with their phases)
- [x] Hosts file blocking (add/remove entries)
- [x] Background service that enforces hosts file — runs as a SYSTEM scheduled task (`scripts/install_service.ps1`); proper Windows Service moves to Phase 7
- [x] Basic GUI: sidebar + add/remove sites, see blocklist (text-only sidebar; other tabs are placeholders)
- [x] System tray icon
- [x] Popular websites quick-list

**Phase 1 decisions:** data in `C:\ProgramData\Lockdown\` (Users get modify rights for now, locked down in Phase 7); only Permanent rules; unblocking is instant (no challenge) until Phase 7; hosts file has no wildcards, so sites list their hostnames explicitly (`www.` added automatically).

### Phase 2 — Scheduling & Limits
- [x] DoH lock: disable DNS-over-HTTPS in Chrome/Edge/Brave/Firefox via locked policies, re-applied by the service
- [x] Disable QUIC/HTTP3 in Chrome/Edge/Brave (+ Firefox via locked pref) so connections can be closed
- [x] Close open connections to a site when it gets blocked (resolve real IPs, `SetTcpEntry`; keeps closing for 3 min)
- [x] Blocked visit notifications (localhost listener + reason: permanent / outside hours / temporary; customizable per reason, cooldown, format, per-item override) — "limit reached" reason comes with daily limits
- [x] Tray agent auto-starts at login (hidden in tray) + single instance (second launch just shows the window)
- [x] Schedule-based blocking (time ranges, days of week, overnight windows)
- [x] Daily time limits per site — time measured by reading the active browser tab's URL via Windows UI Automation (Chrome/Edge/Brave/Firefox), counted by the tray agent, enforced by the service, resets at midnight; "limit reached" alert reason
- [x] Temporary blocks (block for 15 min … 24 h; expired ones removed automatically)

### Phase 2b — Blocking UX rework + clock protection (requested 2026-09-18)
- [x] Hours rules: switch per rule "Allow only during" (default) / "Block during"; multiple time windows per rule, each with its own days + from/to (custom hours per day)
- [x] Blocking tabs: each tab (By Hours / By Limit / Permanent / Temporary) has its own add + edit form and list; a site added in "By Hours" gets an hours rule, etc.
- [x] "All" tab = overview of every site with all its rules/times; per rule an Edit button that jumps to that tab with the site loaded + highlighted; Remove (whole site) with "Are you sure?" confirmation
- [x] Save system: one Save button at the top for all pages, highlighted only when something really differs from the saved state (Edit → Cancel doesn't count); Discard; unsaved items show "Not applied" and aren't enforced
- [x] Auto-save option (switch next to Save; when on, every change is saved immediately) — default off
- [x] "+ Popular sites" button next to Add: popup with popular sites by category (with site icons); clicking one fills URL + name; the old checkbox grid is removed
- [x] Site icons: favicons downloaded + cached locally; letter icon fallback when offline/unavailable (never a broken image)
- [x] Suggestions while typing a site: popular sites + sites you blocked before; "Clear my suggestions" button
- [x] Clock-change protection: service keeps its own trusted time (network time + tick counter), so changing the Windows clock doesn't unlock anything; clock changes are logged
- [x] Notification format default = Windows notification only (choice stays on the Notifications page)

### Phase 3 — App Blocking + Groups + Warnings (decisions 2026-09-18)
- [x] Apps are blocked items like sites (same tabs + rules); matched by exe name (survives app updates)
- [x] Per app "block type": Close app / Block internet (Windows Firewall rule) / Both — default Close app
- [x] Closing: polite first (tray agent sends a normal close, so the app can save), force-kill by the service after 10 s
- [x] Process monitoring every ~1 s in the service; launching a blocked app closes it again; "app closed" alert
- [x] App browser popup ("Browse apps" next to "+ Popular sites"): Start Menu apps + running apps, icons from the exe, search
- [x] Daily limits for apps: time counted while the app is in the foreground (tray agent)
- [x] Hours rules: optional allowance "during blocked hours allow N minutes" (e.g. messenger free all day, 5 min in the night schedule); allowance resets each blocked window
- [x] Groups (new "Groups" tab): named group (e.g. "Night schedule", "Games") with any combination of rules (hours + shared daily limit + permanent + temporary); sites/apps as members
  - members inherit the group's rules; editing the group changes all members
  - per-member customization (e.g. one "emergency" app gets a 5-min allowance in the night block)
  - a group daily limit is one shared total for all members
  - an item can be in many groups and have own rules; blocked when ANY rule/group blocks it
  - All tab shows each item's groups
- [x] Warnings before a block starts: notification N minutes before (default 5, configurable, can be turned off)
- [x] While you're using the app/site inside the warning window: repeat the reminder every X minutes ("closes in 12 min")
- [x] "Block started" notification (on/off)
- [x] Group warnings/notifications combined into one ("Night schedule starts in 5 min: Discord, Steam, YouTube")

**Added 2026-09-18 (evening):**
- [x] Remove buttons: click twice (turns into a red "Confirm" for 3 s) instead of a confirmation dialog
- [x] Apps started while blocked are killed immediately (checked 4x per second); the polite 10 s close is only for apps already open when their block begins
- [x] Apps: "Minimize" block option (keep running, minimize whenever it comes to the front) + "Also block its internet"
- [x] Temporary blocks: custom duration (minutes / hours / days, max 30 days)
- [x] Auto-save on by default; Save/Discard hidden while it's on
- [x] Design brief: design/DESIGN.md + screenshots (Dashboard + Screen Time first)

### Phase 3b — Blocking page rework (approved 2026-09-18)
- [x] Blocking has 3 tabs: **Overview** (everything, sortable: date added / next block / blocked now / name; Edit + Remove), **Groups**, **Add** (pick a site/app, then tick any number of blockers at once: hours, limit, switches, permanent, temporary); Edit from Overview opens the Add form with the item loaded
- [x] Hours editor: no "Remove window" button — a time window with no days ticked is ignored
- [x] Switch limits: "max N openings per day" for a site/app (group: shared total); the next opening after the limit is blocked
- [x] Design brief: add the new Blocking page
- [x] "When blocked" for apps: three checkboxes Close app / Minimize / Block internet (Close and Minimize exclude each other)
- [x] Status colours: blocked now = red, allowed now = green
- [x] Opening limit counts "launches / new visits" by default (apps: program starts; sites: coming back after N min away, default 5), or "every switch" - chosen per blocker
- [x] Faster: service checks rules every 2 s (was 5), Overview counters/status update every 2 s
- Dropped (2026-09-18): "prevent blocked apps from starting at all" (IFEO registry) — the instant kill on launch is enough
- [x] Fix: grey hint text in the site/app and Display name fields (and group name) missing until the field was clicked
- [x] Short confirmation next to the tabs after adding/saving ("✓ YouTube blocker added", "✓ Group Night added"), gone after 5 s

### Phase 3c — Limit reset time, emergency unlock, weekly/monthly limits (approved 2026-09-18)
- [x] Setting: when limits reset (e.g. 04:00 instead of midnight); only limits - stats keep calendar days;
  weeks start Monday / months on the 1st at that time
  - a change never gives a free reset: the current limit day gets longer, never shorter (the running week/month
    are held too); can be changed any number of times (once-a-week lock removed 2026-09-18)
- [x] Emergency unlock: button at the top of Overview → list of everything blocked right now → tick one or more
  (= one use) → confirm → unblocked. Settings: on/off, duration (default 20 min), uses (default 3 per week; per day/week)
  - dropped: "time used during an unlock counts toward limits" option (not needed)
- [x] Time limits and opening limits per day / week / month, stackable (e.g. 2 h a day + max 8 h a week)

### Requested 2026-09-18
- [x] Blocking apps: option to also close the app's background processes - off by default, only with Close app
  (the app closes first, then its child processes and whatever runs from its install folder)

### Phase 4 — Screen Time & Stats (UI after the Claude Design mockups)
Design received 2026-09-18: `design/Lockdown Dashboard & Screen Time.dc.html` (foundations, Dashboard dark/light,
Screen Time Overview/Apps/Websites/Switches, empty state, Blocking Overview/Groups/Add). Decisions (2026-09-18):
blocked = red / allowed = green (user's rule wins over the design); categories default distracting for blocked
items, neutral otherwise, click to change; time saved = attempts x usual visit length; daily goal setting (5 h);
Appearance setting (dark default); fonts bundled; Start service button asks for admin.
- [x] Batch 1: new look everywhere (tokens dark + light, Inter / Barlow Condensed, Lucide sidebar icons) + Dashboard + Screen Time tabs
- [x] Batch 2: Blocking page restyle (rule chips, collapsible blocker cards, groups split view)
- [ ] Later, with the next designs: Settings / emergency panel / Notifications / other pages in the new style (not designed yet)
- [x] Foreground window tracking (per-app time) — data collection: per-minute seconds per app/site (`activity` table)
- [x] Mouse/keyboard activity detection (active vs idle) — active = input in the last 5 min
- [x] Switch counting data (`switch_events` table)
- [x] Overall screen time calculation
- [x] Screen Time page: Overview / Apps / Websites / Switches tabs (per design/DESIGN.md)
- [x] Per-app usage graphs (bar chart, pie chart)
- [ ] Daily/weekly/monthly trend line graphs (7/30-day bars done; trend lines not yet)
- [x] Category breakdown (productive/neutral/distracting)
- [ ] Screen time limits (warning, soft lock, hard lock)
- [x] Dashboard with stat cards and graphs
- [x] Dashboard: "limits today" list — every time/switch limit with a progress bar, time left and % used (requested 2026-09-18)
- [ ] Weekly block calendar: week view with coloured bars per site/app showing when each is blocked (requested 2026-09-18)
- [x] Graphs (weekly / history): mark when emergency unlocks were used (requested 2026-09-18; blue dot on day bars)
- [x] Polish (requested 2026-09-19): rounded tab highlight, smooth donut/charts, heatmap legend not cut, hover
  tooltips on charts, no giant single bars, faster tab switching
- [x] Categories: pick from a small menu, add own categories with custom colours (requested 2026-09-19)

### Phase 5 — Network Logging
Approved 2026-09-19: the service (SYSTEM, sees every app) lists TCP connections with their app every 2 s via
Windows' own tables (no psutil - the service stays standard-library only), names IPs from the Windows DNS cache,
stores one row per app + address + port per minute (with a count). Keeps only the last hour (user: "it is to see
sites that you have visited"). Windows' own + local traffic hidden by default; built in the current style.
- [x] Connection enumeration (GetExtendedTcpTable IPv4 + IPv6, owning process)
- [x] DNS names (Windows DNS cache: IP -> domain the app looked up; Chromium built-in DNS client turned off by policy)
- [x] Log storage in SQLite (network_log, 1 hour)
- [x] Network log viewer in GUI (table / graph, filter, search, live, export CSV)
- [x] Click a row: block site / app (opens Add filled in), copy site / app name, show only this app
- Dropped: UDP (no remote address in Windows' table; browsers use TCP since QUIC is off), "Lookup domain info" (WHOIS)

### Phase 6 — Modes & Reminders
Approved 2026-09-19 (all suggestions OK): 6a Modes, then 6b Reminders. A mode blocks a category (e.g. Distracting)
plus picked sites/apps/groups while it's on; one mode at a time; start from the Modes page or tray, for a time /
until a time / until stopped (optionally locked), or on a schedule. Relax keeps the normal blockers ("allow only
X" isn't possible with hosts-file blocking). Reminders: sleep, breaks, custom (interval / fixed time / random),
Done / Snooze, "did you really do it?" check, quotes; popups wait while a full-screen app is in front (toast instead),
sleep / forced-break overlays still show.
- [x] Built-in modes (Work, Study, Focus/Pomodoro, DND, Relax)
- [x] Custom mode creation
- [x] Mode scheduling (auto-switch by time/day)
- [x] Quick-switch from tray
- [x] Sleep reminder (bedtime, escalating alerts)
- [x] Break reminder (interval, forced break option)
- [x] Custom reminders

### Protection lists (requested 2026-09-19)
- [x] Always-on lists of scam / phishing / malware / adult sites (on by default), gambling optional; community lists
  updated daily by the service; separate from your own blocklist; "allowed anyway" exceptions; "check a site";
  not affected by modes or the emergency unlock; blocked visits name the list ("on the scam list")
- [x] DNS filter in the service instead of the hosts file for these lists (approved 2026-09-19; the hosts file with
  ~237k domains broke Windows DNS) - lookups in memory (millions of domains, whole subdomains); adapters' DNS =
  "127.0.0.1, <own DNS>" so the internet keeps working if the service stops; originals restored on uninstall
- [x] Bigger lists if not laggy (requested 2026-09-19): ~4.3M sites (HaGeZi + Block List Project), 34 MB, ~15 µs/lookup
- [x] Download progress on the Protection tab (requested 2026-09-19)
- [x] Fix: "Start service" did nothing while a stuck copy was running (now ends it first) (reported 2026-09-19)
- [x] "Bad words" check (approved 2026-09-19: both parts, close tab or go back as an option, English + Polish):
  (1) forced SafeSearch on Google / Bing / DuckDuckGo + YouTube Restricted Mode (DNS filter + browser policies);
  (2) check of the active tab's address and title every second (whole words / word* / phrases, built-in adult list
  + your own words, exceptions) -> close the tab or go back + notice; loosening goes through Anti-Bypass
- [x] Word lists instead of words shown one by one: ready-made "Adult words - English" (102) and "Adult words -
  Polish" (45) lists you switch on/off, "Your words" and "Exceptions" - each opens a window to tick off / remove /
  add words (requested 2026-09-19)
- [x] Pages with a lot of info lagged when switching to them (Network Log, Notifications) - rows in pages, sections
  you open, no needless redraws, pages built in the background after start (requested 2026-09-19)
- [x] Notifications show the Lockdown logo and name (not a red dot and "Python"); windows / taskbar use the logo
  (requested 2026-09-19)
- [x] Blocked-word notices came up to ~20 s late after many tabs: the first shows at once, the rest as one summary;
  notices replace each other instead of queuing; tab checked twice a second (requested 2026-09-19)
- [x] Network Log scrolling glitch: the table is one native Treeview (requested 2026-09-19)
- [x] Anti-Bypass option "Real keyboard only" (blocks macros / auto-typers in the phrase box), off by default
  (requested 2026-09-19)
- [x] App browser: searching was slow / apps missing (Steam games have no .lnk), odd scrollbar after searching
  (requested 2026-09-19)
- [x] Steam games: found from Steam's libraries, blocking one closes everything in its folder (requested 2026-09-19)
- [x] "+ Add" button not orange until a blocker is ticked - repainted when the tab is shown (couldn't reproduce;
  requested 2026-09-19)
- [x] "By hours" renamed "By time", all days selected by default, × removes a time window (requested 2026-09-19)
- [x] Anti-Bypass option "3×3 grid" (one word at a time into a random box you click), off by default
  (requested 2026-09-19)
- [x] Window didn't come back from the taskbar / Alt+Tab after minimizing while a pop-up was open (the pop-up's
  focus grab blocked the restore) - the grab is let go while minimized (reported 2026-09-19)
- [x] Shortcuts: Esc closes pop-ups; in text boxes Ctrl+Z / Ctrl+Y, Ctrl+Backspace / Ctrl+Delete, Ctrl+A
  (requested 2026-09-19)
- [x] Browse apps: typing "steam" lists every Steam game under Steam; "running" tag small and green
  (requested 2026-09-19)
- [x] Search tolerates small typos - Browse apps, site suggestions, Network Log, word lists (requested 2026-09-19)
- [x] Dashboard "Blocked visits today": only the count by default, "Show" opens the list (remembered)
  (requested 2026-09-19)
- [x] Site suggestions: flashed while typing, box too big, stayed on screen over other apps - one window updated
  in place, sized to its rows, hides when the focus leaves or another app is in front (reported 2026-09-19)
- [x] "+ Popular sites" button removed (the site box suggests them) (requested 2026-09-19)
- [x] "Real keyboard only" Anti-Bypass option removed (requested 2026-09-19)
- [x] 3×3 grid: continues by itself once every word is typed (requested 2026-09-19)
- [x] Apps in the same search box as sites (sites + installed apps + Steam games), "Browse apps" kept
  (approved 2026-09-19)
- [x] Grey hint lines -> "?" that shows the text on hover (important hints / half-titles stay) (requested 2026-09-19)
- [x] Smoother page / tab switching: the new page is drawn behind a cover, then shown at once (requested 2026-09-19)
- [x] Locked modes can be stopped (or replaced) with the Anti-Bypass challenge - always asks for the phrase
  (requested 2026-09-19)
- [x] Themes (Dark / AMOLED / Light / Match Windows) and accent colour (8 swatches + colour picker), restart button
  (requested 2026-09-19)
- [ ] Better app icon + tray icon - added to the designer brief (design/DESIGN.md 7c, requested 2026-09-19)
- [ ] Light mode: white elements blend in (switch knobs, outlined buttons, selected nav item) - added to the next
  designer prompt (design/DESIGN.md 7b, requested 2026-09-19)

### Phase 7 — Anti-Bypass
Approved 2026-09-19 with changes: challenges are only "type a random phrase" and "only during these hours" (no
waiting, no math). Anything that loosens a block goes through them; tightening is always instant. Emergency unlock
stays outside (own limit). Built: 7a + the hardening that doesn't change how settings are stored.
- [x] Settings change window (specific hours only) - "Only during these hours" (several windows, days + times)
- [x] Type-a-phrase challenge (random letters/digits, no pasting; passing unlocks loosening for 5 min; "Lock now")
- Dropped (2026-09-19): delay unlock mechanism, math problem challenge
- [x] What needs the challenge: removing items / groups / members / sites, weaker rules (higher or no limits, other
  hours, more allowance, shorter temporary block, gentler app block type), protection list off / site allowed,
  emergency unlock more / longer / on, tray Exit, weakening Anti-Bypass itself; "Anti-Bypass" page
- [x] Tray agent brought back within a minute if it's closed/killed (per-user watchdog task, logged); not after Exit
- [x] Tray "Exit" requires the anti-bypass challenge (closing the window still just hides to tray)
- [x] Clock protection hardening: a new time zone counts only after 24 h (summer/winter time at once); clock rolled
  back while offline across a reboot was already covered (never earlier than the last trusted time)
- [x] Lock the emergency-unlock settings behind the anti-bypass (requested 2026-09-18); the limit reset time stays
  free (the user said changes are always the longer period, so it can't shorten a limit)
- [x] Watchdog: "Lockdown Watchdog" SYSTEM task starts the service every minute if it's stopped (= scheduled task backup)
- [x] Uninstall protection: uninstall_service.ps1 asks for the challenge (`main.py --challenge`)
- [ ] Waiting for OK (asked 2026-09-19): service owns the settings - the GUI asks it over a local pipe and
  `C:\ProgramData\Lockdown\` becomes read-only for Users (so editing config.db by hand can't loosen anything);
  covers "Service permission lockdown" + "Lock down ProgramData ACLs"
- Moved to Phase 8: convert the enforcement scheduled task into a real Windows Service (with PyInstaller packaging)

### Phase 8 — Import & Polish
Approved 2026-09-19 ("complete everything", my defaults: streaks = within goal + no emergency unlock, weekly
summary Sunday 19:00). 8a built; 8b (packaging) waits for OK to install PyInstaller + pywin32 (downloads).
- [x] GitHub blocklist import (StevenBlack, oisd, Energized) - done by the protection lists + your own lists
- [x] Import from custom URL - "Add your own list" on the Protection tab (hosts / domains / adblock format)
- [x] Import preview (count, sample domains) before adding
- [x] Export settings / logs - Settings > Backup & export (settings backup JSON + import, screen-time CSV;
  Network Log CSV was already there)
- [x] Weekly digest notification - Notifications > Weekly Summary (day + time)
- [x] Streak tracker - Dashboard "At a glance": days within the goal, days without an emergency unlock
- [x] Heatmap calendar view - Screen Time > Calendar (a month at a time, over-goal days marked)
- [x] SVG sidebar/UI icons (Lucide, pre-rendered to PNG) - done in the design batch
- [x] Per-page display settings (⚙ on Dashboard / Screen Time: show / hide Dashboard cards, Screen Time's opening
  tab and range) - no reordering (asked about 2026-09-19)
- [x] PyInstaller packaging (.exe): Lockdown.exe + LockdownService.exe (installer/lockdown.spec, scripts/build.ps1,
  `--selftest` build check)
- [x] Real Windows Service (moved from Phase 7): "Lockdown Enforcer" (LockdownEnforcer), auto start, restarted on
  failure, "Lockdown Watchdog" task starts it if stopped
- [x] Installer with service registration: LockdownSetup.exe (install / update / uninstall, Program Files, shortcuts,
  Apps & features entry; uninstall needs the Anti-Bypass challenge)
- [x] Updates keep everything (requested 2026-09-19): data stays in C:\ProgramData\Lockdown; the installer only
  replaces the program; older databases get new columns on open (tested)
- [x] Protection lists: "Update automatically" option (on by default; every 6 h / 12 h / daily / weekly - daily
  default) (requested 2026-09-19)

### Later (not planned soon)
- [ ] macOS and Linux support (requested 2026-09-19) - blocking (hosts / DNS), apps and the service differ per OS
- [ ] Automatic app updates (download a new version and install it)

---

## 8. File Structure (Planned)

```
Lockdown/
├── PLAN.md
├── CHANGELOG.md
├── README.md
├── requirements.txt
├── setup.py
├── scripts/
│   ├── install_service.ps1    # Registers the enforcement service (admin, once)
│   └── uninstall_service.ps1
├── tests/                     # pytest
├── src/
│   ├── main.py                # GUI entry point
│   ├── paths.py               # Shared file locations (ProgramData, hosts)
│   ├── service.py             # Enforcement service
│   ├── watchdog.py            # Watchdog service
│   ├── db.py                  # SQLite database layer
│   ├── rules.py               # Block rule evaluation (permanent / hours / temporary / daily limit)
│   ├── trusted_time.py        # Clock-change protection (internet time + tick counter)
│   ├── alerts.py              # Blocked-visit alert settings + message formatting
│   ├── blocker/
│   │   ├── hosts.py           # Hosts file manipulation
│   │   ├── browser_policy.py  # Locked browser policies (DoH off, QUIC off)
│   │   ├── connections.py     # Close open TCP connections to blocked sites
│   │   ├── listener.py        # 127.0.0.1:80/443 blocked-visit listener
│   │   ├── apps.py            # Process list / terminate (service)
│   │   └── firewall.py        # Firewall rule management
│   ├── monitor/
│   │   ├── network.py         # Network connection logging
│   │   ├── dns.py             # DNS query monitoring
│   │   ├── browser_url.py     # Active tab URL via UI Automation
│   │   ├── win.py             # Foreground app, idle time, polite app close (tray agent)
│   │   ├── usage.py           # Time on limited sites (tray agent)
│   │   ├── screentime.py      # Foreground window + activity tracking
│   │   └── applist.py         # Installed app enumeration (registry)
│   ├── gui/
│   │   ├── app.py             # Main window + sidebar + global search
│   │   ├── dashboard.py       # Dashboard / Home view
│   │   ├── blocking.py        # Unified blocking view (sub-tabs by type)
│   │   ├── app_browser.py     # App browser popup (for adding apps)
│   │   ├── groups.py          # Groups tab (group editor, member customization)
│   │   ├── rule_editors.py    # Hours / limit / temporary / permanent editors
│   │   ├── target_picker.py   # Site-or-app input (suggestions, popular, browse apps)
│   │   ├── antibypass.py      # Anti-bypass / Lock settings view
│   │   ├── screentime.py      # Screen time, switches, goals views
│   │   ├── limits.py          # Usage limits view
│   │   ├── netlog.py          # Network log viewer
│   │   ├── modes.py           # Modes view
│   │   ├── notifications.py   # Notifications settings + log view
│   │   ├── settings.py        # Global settings view
│   │   ├── charts.py          # Chart/graph widgets
│   │   ├── page_settings.py   # Per-page display settings panel
│   │   ├── single_instance.py # One GUI/tray process only
│   │   ├── draft.py           # Unsaved changes + Save/Discard/Auto-save
│   │   ├── site_picker.py     # Site suggestions + popular sites popup
│   │   ├── icons.py           # Favicon cache + letter fallback
│   │   └── tray.py            # System tray icon
│   ├── lock/
│   │   ├── challenges.py      # Type-phrase, math problems, grid challenge
│   │   ├── delay.py           # Delay unlock logic
│   │   └── guard.py           # Config file protection
│   ├── modes/
│   │   ├── manager.py         # Mode switching logic
│   │   ├── pomodoro.py        # Focus/Pomodoro timer
│   │   └── presets.py         # Built-in mode definitions
│   ├── reminders/
│   │   ├── sleep.py           # Sleep reminder logic
│   │   ├── breaks.py          # Break reminder logic
│   │   └── custom.py          # Custom reminders
│   └── importer/
│       ├── github.py          # GitHub blocklist fetcher
│       ├── hosts_parser.py    # Hosts file format parser
│       └── popular.py         # Built-in popular site lists
└── assets/
    ├── icon.ico               # App icon
    ├── tray_green.ico         # Tray icon — active
    ├── tray_yellow.ico        # Tray icon — relax
    ├── tray_red.ico           # Tray icon — service down
    └── icons/                 # SVG icons (Lucide or Phosphor)
        ├── dashboard.svg
        ├── block.svg
        ├── lock.svg
        ├── clock.svg
        ├── network.svg
        ├── layers.svg         # modes
        ├── bell.svg           # notifications
        ├── settings.svg
        ├── search.svg
        └── ...                # etc.
```

---

## 9. Database Schema (Planned)

```sql
-- Unified blocked items (sites + apps together)
CREATE TABLE blocked_items (
    id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL,   -- friendly name: "Reddit", "Discord"
    target TEXT NOT NULL,         -- sites: space-separated hostnames ("x.com twitter.com"); apps: exe path
    item_type TEXT NOT NULL,      -- "site" or "app"
    block_type TEXT,              -- kill, firewall, both (apps only)
    note TEXT,                    -- user's personal note
    source TEXT,                  -- manual, import, quick-list
    notify TEXT,                  -- blocked-visit alerts override: NULL = default, 'on', 'off'
    app_path TEXT,                -- apps: exe path (firewall rule, icon); target = exe name
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Block rules (multiple per item — stacking)
CREATE TABLE block_rules (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES blocked_items(id),
    rule_type TEXT NOT NULL,      -- permanent, scheduled, time_limit, switch_limit, temporary
    daily_limit_min INTEGER,      -- for time_limit
    daily_switch_limit INTEGER,   -- for switch_limit
    schedule TEXT,                -- JSON: days + hours for scheduled
    temp_until DATETIME,          -- for temporary
    allowance_min INTEGER,        -- scheduled: minutes still allowed during blocked hours
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Attempts to open a blocked site (service listener writes, tray agent notifies)
CREATE TABLE block_events (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME,
    hostname TEXT,
    item_id INTEGER,
    display_name TEXT,
    reason TEXT,                  -- permanent, schedule, temporary
    until DATETIME
);

-- Seconds used per owner ("item:<id>" / "group:<id>") and bucket ("day:<date>" / "win:<rule>:<end of blocked stretch>")
CREATE TABLE usage (
    owner TEXT NOT NULL,
    bucket TEXT NOT NULL,
    seconds INTEGER NOT NULL DEFAULT 0,
    day TEXT NOT NULL,
    PRIMARY KEY (owner, bucket)
);

-- Groups: shared rule sets; members inherit them (with optional per-member customizations)
CREATE TABLE block_groups (id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at DATETIME);
CREATE TABLE group_rules (id INTEGER PRIMARY KEY, group_id INTEGER REFERENCES block_groups(id), rule_type TEXT,
                          daily_limit_min INTEGER, schedule TEXT, temp_until DATETIME, allowance_min INTEGER);
CREATE TABLE group_members (group_id INTEGER, item_id INTEGER, overrides TEXT,   -- JSON {rule_type: rule}
                            PRIMARY KEY (group_id, item_id));

-- Sites blocked before (add-site suggestions; clearable)
CREATE TABLE site_history (
    hostname TEXT PRIMARY KEY,
    display_name TEXT,
    last_used DATETIME
);

-- Network log
CREATE TABLE network_log (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    process_name TEXT,
    process_pid INTEGER,
    remote_addr TEXT,
    remote_domain TEXT,
    remote_port INTEGER,
    protocol TEXT,
    status TEXT
);

-- Screen time (per-minute snapshots)
CREATE TABLE screen_time (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    foreground_app TEXT,
    foreground_title TEXT,
    is_active BOOLEAN,           -- mouse/keyboard activity detected
    mode TEXT                    -- which mode was active
);

-- App usage (aggregated daily)
CREATE TABLE app_usage_daily (
    id INTEGER PRIMARY KEY,
    date DATE,
    app_name TEXT,
    category TEXT,               -- productive, neutral, distracting
    active_minutes INTEGER,
    UNIQUE(date, app_name)
);

-- Modes
CREATE TABLE modes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    is_builtin BOOLEAN DEFAULT 0,
    blocked_sites TEXT,          -- JSON array of domain patterns
    blocked_apps TEXT,           -- JSON array of app names
    blocked_categories TEXT,     -- JSON array of categories
    is_active BOOLEAN DEFAULT 0
);

-- Settings
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Reminders
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY,
    type TEXT,                   -- sleep, break, custom
    enabled BOOLEAN DEFAULT 1,
    config TEXT                  -- JSON with type-specific settings
);

-- Notifications log
CREATE TABLE notification_log (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    type TEXT,                   -- limit_warn, block, reminder, mode_change, report, streak, bypass_attempt
    message TEXT,
    seen BOOLEAN DEFAULT 0
);

-- Switch tracking (per focus-switch event)
CREATE TABLE switch_events (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    app_name TEXT,
    window_title TEXT
);

-- Anti-bypass config
CREATE TABLE antibypass (
    id INTEGER PRIMARY KEY,
    method TEXT NOT NULL,         -- specific_hours, grid_challenge, delay, math, random_password, nuclear
    enabled BOOLEAN DEFAULT 0,
    config TEXT                   -- JSON with method-specific settings
);

-- Page display preferences
CREATE TABLE page_display (
    page TEXT PRIMARY KEY,        -- dashboard, blocking, screentime, netlog
    config TEXT                   -- JSON: which widgets visible, order, chart types
);
```
