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

- **Weekly block calendar** (idea): a week view with coloured bars per site/app showing when each is blocked.
- Anti-Bypass, Network Log, Modes, Settings pages.

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
