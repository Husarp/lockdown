# Designer brief — Lockdown Mobile (Android), full-feature redesign

## What the app is
Lockdown Mobile is a self-control app for Android: it blocks apps, websites and search keywords, enforces
time limits and schedules, and makes it hard to bypass your own rules. It's the phone version of an existing
Windows app; we're bringing the Windows app's full feature set to the phone, so the current 7-tab layout is
now too small and feels chaotic. **Design a clean information architecture and the key screens.**

## Platform & style
- Native Android, **Jetpack Compose + Material 3 / Material You** (colours follow the wallpaper; dark & light).
- Phone-first (≈360–420 dp wide). Reference look: clean, modern, calm — like a well-made Google app.
- The app is used *by the person restricting themselves*, so the tone is supportive, not punishing. Blocked
  screens and challenges should feel like a friction speed-bump, not a scold.

## Design tension to solve
There are ~7 feature areas and dozens of options. The current bottom-nav (Home, Blocking, Modes, Insights,
Reminders + Guardrails/Settings in the top bar) can't hold it cleanly. **Propose the IA**: what's in the
bottom nav (max 5), what's a sub-screen, what's grouped into cards, what's progressive-disclosure (advanced
options hidden until needed). Blocking especially can hold thousands of entries — search and grouping matter.

## Every feature to place (group them sensibly; not all need to be top-level)

**Blocking**
- Blocked apps and blocked sites (thousands possible → needs search, sections, maybe folders/groups).
- Groups: named sets of apps/sites that share one rule set (e.g. one shared daily limit); per-member tweaks.
- Per rule: Permanent / Scheduled hours (allow or block windows, per weekday, can cross midnight) /
  Time limit (min per day, week, month) / Opening limit (opens per day/week/month; "new visit" vs "every open") /
  Temporary (until a date/time) / Allowance (X min of grace inside a blocked stretch).
- A "limit day" can start at a custom time (e.g. 3 a.m.), not midnight.
- Rule status at a glance: blocked / "soon" (nearly out of time) / allowed.
- How an app is blocked: close it / also kill background / minimise it / cut its internet.
- How a site is blocked: can't load / close the tab / go back.

**Protection lists** (bundled, all shipped in the app, just toggle): Scam, Phishing, Malware, Adult,
Gambling, Manga & anime. Plus your own list by URL / your own entries, and an "allowed" exceptions list.

**Keywords** (shipped on by default): block browser searches/pages containing words; built-in Adult
(English) and Adult (Polish) lists (toggle, remove single words); your own words (with `*` wildcard,
phrases, whole-word, accent-insensitive); exceptions (a site or a word). Forced SafeSearch (Google/Bing/
DuckDuckGo) and YouTube Restricted Mode toggles.

**Modes**: a mode blocks a category (e.g. everything "Distracting") + picked apps/sites/groups on top of
normal rules. Start by hand (for a duration / until a time / until stopped; optionally locked until it ends)
or by schedule. Mute notifications option. Focus = Pomodoro (work/break/rounds/long break; blocked only in
focus rounds). A few built-in modes.

**Insights / Screen time**: per-app & per-site time; active vs idle; switches, visits, sessions, longest
focus; day timeline, hourly chart, heatmap; categories (productive/neutral/distracting) per app/site;
ranges (today/yesterday/7/30/custom); daily goal + streaks; time saved; a network log.

**Reminders**: bedtime (heads-up + escalating nudges + full-screen bedtime overlay + turn on a mode at
bedtime), breaks (interval, length, strict break, snooze, 20-20-20), your own reminders (interval / fixed
times / random once-a-day; days; hours window; "did you actually do it?" check; stop after N/day). Any
reminder can be "Important" (dismissing needs the challenge). Sensible pacing/back-off so it doesn't nag.

**Guardrails (anti-bypass)**: a challenge before any loosening change (tightening is instant). Challenge
types: type-a-phrase (random or your own), allowed-hours-only, wait/cool-off. Uninstall protection.
Emergency unlock: unblock chosen items for N minutes, limited uses per day/week, with history. Trusted time
(clock changes can't unlock). A "keep running / can't be killed by accident" reliability section with a
tamper warning if protection gets switched off. Master on/off.

**Settings**: appearance/theme, updates, import/export config, site history (suggest previously-blocked
sites), the extras toggles (pause-before-open, bedtime grayscale, widgets, weekly digest).

**Home widgets & Quick Settings tile**: today's screen time / time left; toggle Lockdown.

## Deliverables from the designer
1. A proposed **information architecture** (nav model + screen map) that makes all of the above findable and
   calm — call out what's progressive-disclosure/advanced.
2. High-fidelity mockups (light + dark) for: Home, Blocking (list + one rule editor), Insights, a Mode
   editor, Reminders, Guardrails, and the block/challenge full-screen moments.
3. A component kit (cards, list rows, rule chips, the block overlay, the challenge screen).

Keep it implementable in Compose/Material 3 — no custom-drawn UI that fights the platform.
