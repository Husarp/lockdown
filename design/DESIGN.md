# Lockdown — Design Brief

Hand this file plus the `screenshots/` folder to the designer. Everything the designer needs is here; the code
is not needed.

---

## 1. What Lockdown is

A Windows desktop app for digital wellbeing. It blocks websites and apps (permanently, during certain hours,
after a daily time limit, or temporarily), tracks screen time, and makes it hard to cheat your own rules.
It lives in the system tray, starts with Windows, and sends notifications ("YouTube will be blocked in 5 min").

**Who uses it:** one person on their own PC who wants to spend less time on distracting sites/apps and games.
The tone should be calm, supportive and clear — not preachy, not "hacker" styled.

## 2. Technical constraints (important)

The app is built with **Python + customtkinter** (a modern-looking Tk toolkit). Designs must be buildable with it:

- **Available:** flat rectangles and frames with rounded corners, one border colour, buttons, segmented buttons
  (pill-shaped tab switchers), switches, checkboxes, dropdowns, text entries, sliders, progress bars, scrollable
  areas, small images/icons (PNG), plain text in one font family with different sizes/weights.
- **Charts:** simple bar, line, pie/donut, horizontal bar lists, and grid heatmaps (drawn on a canvas).
- **Not available:** shadows, blur/glass effects, gradients inside widgets, animations beyond simple show/hide,
  custom fonts are possible but must be a single downloadable TTF (e.g. Inter).
- **Window:** default 1100 × 720 px, minimum 900 × 560, resizable. Must also work maximized.
- **Themes:** dark (default) and light. Please provide both, or at least dark + the colour tokens for light.
- **Icons:** Lucide or Phosphor icon set (free, consistent line icons). Site/app icons are real favicons / exe icons.

## 3. Global layout (exists today — see screenshots 01–06)

- **Left sidebar** (190 px): app name, 8 pages — Dashboard, Blocking, Anti-Bypass, Screen Time, Network Log,
  Modes, Notifications, Settings — and a service status at the bottom ("● Service running" green /
  "● Service not running" red). Icons should be added in front of the page names.
- **Top bar:** "Auto-save" switch (on by default). When it's off, "Discard" + "Save changes" buttons appear;
  "Save changes" is highlighted only when something actually changed.
- **Content area:** page title + page content.

Status colours used today: red = blocked now, green = allowed now / service running, orange = pending / not applied,
red = errors / "Confirm" state of a Remove button (Remove needs two clicks: the button turns red "Confirm" for 3 s).

## 4. Please design first: Dashboard + Screen Time

These two pages don't exist yet, so nothing has to be redone. The data behind them is already being collected.

### 4.1 Dashboard (home page)

Purpose: "How am I doing today?" at a glance. Every card is clickable and opens its detail page.

Data available:
- screen time today (active vs idle), and per day for past days → trend vs yesterday / last week
- time per app and per website today
- switches today (how often you jumped to another app/site), and which apps/sites you switch to most
- blocked items: how many are blocked right now, what's coming next ("Night schedule starts at 22:00")
- **limits in progress** (user's idea): every site/app/group with a daily limit, as a list with progress bars —
  used / limit, time left, percentage. E.g. "Discord 23 min of 1 h — 37 min left".
- blocked visits today (attempts to open a blocked site/app)

Wanted elements (from the plan, adjust freely):
- stat cards row: Screen time today · Blocked now · Switches today · Time saved (optional)
- "Limits today" list with progress bars (see above)
- "Coming up" — next blocks starting/ending today
- today's timeline: a horizontal bar through the day, coloured by what you did (active app / idle)
- 7-day screen time bar chart with a daily-limit line
- quick glance: top app, most switched app

### 4.2 Screen Time page

Tabs: **Overview · Apps · Websites · Switches** (Goals and Limits later).
- **Overview:** today's active/idle totals, timeline, 7/30-day bar chart, hourly heatmap (days × hours, which hours
  you're most active), category split (productive / neutral / distracting — user can assign a category per app).
- **Apps / Websites:** ranked list: icon, name, time today, % bar, switches, category; date range picker
  (Today / Yesterday / 7 days / 30 days).
- **Switches:** switches per hour today (bar list), most-switched apps with "average time per visit"
  (many switches + short visits = compulsive checking — highlight these gently).

### 4.3 States to cover
- **Empty:** first day, no data yet ("Tracking started — come back in a few minutes").
- **Service not running:** blocking isn't enforced — a clear but calm warning banner.
- **Loading:** lists that load in the background (e.g. the app list takes a few seconds).

## 5. Next: the Blocking page (structure is final, visuals are not)

Three tabs (see screenshots 01–05):
- **Overview:** every blocked site/app — icon, name, what it is (hostnames or `app · discord.exe · closed`), all its
  rules (its own + the ones from groups, e.g. "[Night schedule] Blocked: Every day 22:00-07:00"), status
  (Blocked now / Allowed now / Not applied), per-item alerts dropdown, Edit, Remove (two clicks). Sort by: blocked
  now first / next block / date added / name. "+ Add" button.
- **Groups:** list of groups (name, members, rules, "2 of 3 blocked now") + a group editor (name, rules that can be
  combined, members, per-member "Customize").
- **Add:** choose a site (with suggestions, "+ Popular sites" with icons) or an app ("Browse apps" with icons);
  for apps "When blocked": checkboxes Close app / Minimize / Block internet (Close and Minimize exclude each other). Then tick any
  number of **blockers**, each with its own small settings: By hours (allow-only / block-during, time windows per
  day, minutes still allowed during blocked hours), Time limit (per day / week / month, stackable), Opening limit
  (per day / week / month; launches / new visits, or every switch), Permanent, Temporary
  (preset or custom duration). The same form edits an existing item.

Overview also has an **Emergency unlock** button: a panel listing everything blocked right now with checkboxes,
"2 of 3 left this week", and "Unlock for 20 min" (two clicks to confirm). Unlocked items show a blue
"Emergency unlock - 12m left" status. Graphs should later mark when emergency unlocks were used.
A **Settings** page exists too (limit reset time, emergency-unlock settings).

The editor can get long when several blockers are ticked — a cleaner layout (e.g. collapsible sections or
chips/cards per blocker) would help.

## 6. Later (don't design yet)

Moved to **section 9 (round 2)** - everything is ready to be designed now.

## 7. What I'd like back

1. Mockups (dark + light) of Dashboard and each Screen Time tab, at 1100 × 720.
2. Colour tokens (background, surface, border, text, muted text, accent, green/orange/red status) and the type
   scale (sizes/weights).
3. Spacing rules (padding inside cards, gaps between cards) and corner radius.
4. The chosen icon for each sidebar page.

## 7b. Next design round: fix light mode (requested 2026-09-19)

Several elements don't stand out in the **light** theme - mostly white things on white / very light grey:
- **Switches**: the white knob disappears on white cards and on the light background (Auto-save at the top right,
  every switch in cards); an *off* switch is a pale grey track + white knob and is almost invisible
  (`light_anti-bypass.png`, `light_blocking_protection.png`, `light_settings.png`).
- **Outlined / secondary buttons** ("Add", "Change", "Edit", "Choose categories for your apps"): white on white with
  a faint border (`light_settings.png`, `light_modes.png`, `light_dashboard.png`).
- **Selected sidebar item**: white on a light grey sidebar - weak contrast.
- **Mode cards**: the top edge / corners look clipped (`light_modes.png`).
- **Settings page**: its sections don't use the same card style (title size, padding) as the other pages.
Please give light-mode tokens (switch track on/off, knob, secondary button fill/border, selected nav item) that
keep enough contrast, and check every page in light mode. Screenshots: `screenshots/light_*.png`.

## 7c. Next design round: app icon + tray icon (requested 2026-09-19)

- **App icon** (window title bar, taskbar, Windows notifications, desktop shortcut): today a placeholder I drew -
  a white shield with a check on the orange accent (`assets/lockdown.png`). Please design a proper one that reads well
  from 16 px (title bar) to 256 px, in dark and light Windows themes. Deliver as SVG + PNGs (16, 24, 32, 48, 64, 128,
  256) or a multi-size .ico.
- **Tray icon** (next to the clock, 16-24 px): today just a plain green dot (service running / blocking) or red dot
  (service not running). Please design a small Lockdown mark with those two states (and maybe a third: a mode is on)
  that is still clear at 16 px on both a dark and a light taskbar.

## 8. Screenshots (current app)

| File | Shows |
|---|---|
| `screenshots/01_blocking_overview.png` | Blocking → Overview: everything blocked, rules, status, alerts, Edit/Remove, sort |
| `screenshots/02_blocking_groups.png` | Blocking → Groups list |
| `screenshots/03_add_multiple_blockers.png` | Blocking → Add: site + several blockers ticked (hours with allowance + temporary) |
| `screenshots/04_edit_item.png` | Blocking → Add in edit mode (an item's blockers loaded) |
| `screenshots/05_group_editor.png` | Group editor: name, combined rules, members |
| `screenshots/06_notifications.png` | Notifications settings |
| `screenshots/light_*.png` | Light theme: Dashboard, Blocking Overview / Protection, Anti-Bypass, Modes, Settings (see 7b) |

---

## 9. Round 2 - the whole app (requested 2026-09-19)

**Prompt for the designer (copy from here):**

> Round 1 gave Lockdown its look (`Lockdown Dashboard & Screen Time.dc.html`: tokens dark + light, Inter + Barlow
> Condensed, Lucide icons, orange accent). That look is built and in use on every page, but only Dashboard and
> Screen Time were actually designed - every other page was styled by me. Please design **the whole app** in the
> same system, dark **and** light, at 1280 × 800 (must still work at the 900 × 560 minimum). Current screenshots of
> every page, tab and pop-up are in `screenshots/round2/dark/` and `screenshots/round2/light/` (same names, table
> below) - they show real data and the real structure, which is final; the visuals are not. Keep the constraints
> in section 2 (customtkinter: no shadows, blur, gradients or animations beyond show/hide; one border colour).
> Users can also pick **AMOLED** (pure black) and their own **accent colour** (8 presets + custom), so please don't
> rely on orange meaning something that another accent would break - status colours stay red = blocked / green =
> allowed or running / yellow = warning.

### 9.1 Pages to design (not designed yet)

- **Blocking**: Overview (unified site + app list with a small **type badge** `site` / `app` next to the name),
  Groups, Add (the blocker cards get long when several are ticked - please give a tidier layout), the site/app
  **suggestion dropdown** (sites and apps in one search box; Steam games listed under Steam), **Browse apps**
  pop-up (icon, name, green "running" tag), **Protection** tab (list switches with counts and "updated 3 h ago",
  auto-update switch + interval, "add your own list" by URL with a preview, Safe search card, Blocked words card
  with word lists you open in a small window).
- **Anti-Bypass**: challenge choices (type a phrase / 3×3 word grid / only during allowed hours with time
  windows), what's currently protected. Plus the three **challenge windows** (grid, phrase, "closed - come back
  at 18:00").
- **Locked / view-only state** (from the plan): while Anti-Bypass is on, settings that would loosen your blocks are
  still **visible** but need the challenge to edit. Please design how that looks - greyed controls with a small
  lock icon, and a clear "Unlock to edit" affordance - and the short "unlocked for 5 min" state after passing.
- **Network Log**: table view (lots of rows - keep it dense and readable) and graph view.
- **Modes**: mode cards (Work, Study, Focus/Pomodoro, Do Not Disturb, Relax, your own), the "Now" card with a
  running mode - including a **locked** mode ("Stop (locked until 07:46)") - and the Reminders tab.
- **Notifications**: alert settings, message templates (collapsible), weekly summary.
- **Settings**: Appearance (theme + accent swatches), daily goal, when limits reset, emergency unlock,
  backup & export. Sections must use the same card style as the rest of the app (they don't today).
- **Small windows**: per-page **display settings** (gear icon in a page header: show/hide cards), the in-app
  **pop-up notification** (bottom-right), the **setup / installer window** (`LockdownSetup.exe`: install, update,
  uninstall with an "also delete my data" tick) - no screenshot of the last one, it's a plain small window today.

### 9.2 New things to add to existing pages

- **Trend line graphs** (Screen Time → Overview): daily / weekly / monthly totals as a line - "are you improving?"
  with a trend figure (e.g. "▼ 12 % vs last week"). Today there are only 7/30-day bars.
- **Weekly block calendar** (Blocking): a week view (Mon-Sun × 24 h) with a coloured bar per site/app/group
  showing when each is blocked; tapping a bar opens that item.
- Graphs should mark when an **emergency unlock** was used (a small marker on that day / hour).

### 9.3 Light mode (see 7b - still open)

Every point in 7b still applies. Please check each round-2 screenshot in `light/` - switches, outlined buttons,
the selected sidebar item, and cards on the light-grey background.

### 9.4 Icons (see 7c - still open)

- **App icon**: title bar, taskbar, Start menu, notifications, desktop shortcut and the installer - 16 px to 256 px,
  on dark and light Windows. SVG + PNGs (16, 24, 32, 48, 64, 128, 256) or a multi-size `.ico`.
- **Tray icon** (16-24 px) in **three states**: green = blocking is enforced, yellow = a mode / Relax is on,
  red = the service is down. Clear on both dark and light taskbars.
- **Type badges** `site` / `app` (see 9.1), a **lock** icon for the locked state (9.1), and any other icons you
  add - all from Lucide so they match the sidebar.

### 9.5 What I'd like back

1. Mockups (dark + light) of every page / tab / window in 9.1 and the additions in 9.2.
2. Any new tokens (e.g. locked/disabled state, badge colours, light-mode fixes from 7b).
3. The app icon + tray icon files (9.4).

### 9.6 Round 2 screenshots (`screenshots/round2/dark/` and `.../light/`)

| File | Shows |
|---|---|
| `01_dashboard` / `02_dashboard_visits_open` | Dashboard; blocked visits hidden (count only) and opened |
| `03_blocking_overview` | Blocking → Overview |
| `04_blocking_groups` | Blocking → Groups |
| `05_blocking_add` / `06_blocking_add_bottom` | Blocking → Add with "By time" + "Time limit" ticked |
| `07_site_app_suggestions` | The suggestion dropdown (sites + apps in one search) |
| `08_browse_apps` | Browse apps pop-up |
| `09_protection_top` / `10_…middle` / `11_…bottom` | Blocking → Protection (lists, safe search, blocked words) |
| `12_word_list_window` | A word list opened |
| `13_antibypass` / `14_antibypass_bottom` | Anti-Bypass page |
| `15_challenge_grid` / `16_challenge_phrase` / `17_challenge_closed_hours` | Challenge windows |
| `18`-`23_screentime_*` | Screen Time: Overview, Apps, Websites, Switches, Calendar, Overview bottom |
| `24_networklog_table` / `25_networklog_graph` | Network Log |
| `26_modes` / `27_modes_reminders` | Modes (a locked Focus mode running) and Reminders |
| `28_notifications` | Notifications |
| `29_settings` / `30_settings_bottom` | Settings |
| `31_display_settings` | Display settings window (gear icon) |
| `32_popup_notification` | In-app pop-up notification |
