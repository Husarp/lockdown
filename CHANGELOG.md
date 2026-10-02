# Changelog

## 0.84.8 - 2026-10-02 23:35
- **Pause my blocks: every blocker of yours off for a while you choose, then back by itself.** Adam asked for a way to unlock all his own blocks for a chosen time. New card **Pause my blocks** on the Anti-Bypass page (and **Pause my blocks...** in the tray menu, which opens it): pick **30 min / 1 h / 2 h / 4 h / Rest of the day** (until the next reset time - Settings' "limits reset at"; never more than a day) and press Pause. Every site and app blocked by your items, groups, modes and categories - hours, time limits, opening limits, temporary and permanent blocks - stops blocking: the hosts entries, firewall rules and app closing all come down, and when the pause ends they are all back on the dot (the service's next 2-second pass). **Not lifted:** the protection lists (scam / adult / ... - the DNS filter), the blocked words, SafeSearch / YouTube Restricted. **The time you use still counts** toward your limits and allowances (counting already uses `rules.counted_rules`, enforced or not), so a limit is just as used up when the pause ends. How: a running pause is one setting (`pause.blocks`, new `src/pause.py`) in trusted time, like the emergency unlocks; `db.usage_lookup` puts its end into the `Usage` every rule check gets (`Usage.paused`), `rules.item_block` / `next_block` treat it like an unlock of every item and `db.blocks` returns nothing while it runs - so the service, the tray's minimise / tab-closing and every page agree. It survives a restart, and it only counts between its start and its end (a clock that reads earlier than when it began does not count as paused; never longer than a day whatever is written), so a clock change can't stretch it.
  - **Starting it needs the Anti-Bypass challenge**, exactly like any other loosening change (`LockdownApp.pause_blocks` through `app.guard`; with a locked mode on it asks even with no challenge set, as stopping that mode does, since the pause would lift the mode's blocks too). **Ending it early is free:** **Resume now** on the banner, **Resume blocking** in the tray and on the card.
  - **Silence all notifications** (a tick on the card): for the same while no warnings, block notices, reminders, bedtime heads-up or "Time for bed" screen, break prompts or strict breaks, pop-ups or Windows notifications (`App._show` stays quiet, force or not; the reminders engine treats it like an emergency's alert pause and holds your own reminders too - one on screen goes). When the pause ends they all come back as normal; your bedtime and reminder settings are never changed.
  - While paused: a slim banner over every page, **"Blocking paused until HH:MM · Resume now"**, Blocking shows "● Paused · 40m left" on each item, the tray icon turns **blue** with "Blocking paused until HH:MM", and a few minutes before the end (unless silenced) "Your blocks are back on in 5 min (23:00): ... will be blocked again."
  - It is not the whole-app off switch (Turn Lockdown off stops everything, the protection lists and reminders too, until you turn it back on) and not an emergency unlock (that picks items, spends one of its uses and stays for limits/hours only) - a pause spends no emergency use. The service logs when a pause starts and ends.
- **Updates, in line with the app standards (APP-STANDARDS.md 2-4):**
  - **The banner's ✕ hides it until Lockdown is next started**, not "skip this version for good". Closing the window to the tray and opening it again doesn't bring it back; a fresh start, or a newer version, does. The dot on About and the tray's "Update available" stay. The "skip this version" link is gone, and an old skip mark saved by 0.84.7 no longer hides anything (`updates.skip` / `skipped` removed; `LockdownApp.update_close` keeps it in memory).
  - **A failed in-app update says so with Try again and GitHub** on the About page - nothing opens by itself. Try again starts the installer that already downloaded (when it was starting it that failed - e.g. No on the admin prompt) or downloads it again.
  - **The downloaded installer `LockdownSetup-X.Y.Z.exe` is deleted from the temp folder at the next start** (`updates.remove_downloads`; one still running waits for the start after).
  - The installer is found on a release by its `.exe` ending, whatever its name - versioned (`LockdownSetup-0.84.9.exe`), upper-case, next to an `.apk` or a checksum file; the old `LockdownSetup.exe` still works.
  - **About's update section** now reads: Check for updates automatically (on/off), the version with **Check now**, **GitHub** (the release found, else the releases page) and **Get update** once there is one.
- Review fixes:
  - **"Rest of the day" could pause everything for up to two days.** It ran to the end of the running limit day, and changing the reset time (which needs no challenge, since it never shortens a limit day) stretches that day by up to two days: reset moved to 23:00 at 22:00 made "Rest of the day" a 25-hour pause, 00:01 -> 23:59 a 48-hour one. Now it ends at the next reset time, always within a day (`pause.end_for`), and a pause longer than a day is not a pause at all (`pause.MAX_SPAN`, was two days). Test: `test_pause_blocks.py::test_rest_of_the_day_is_never_a_two_day_pause`, `test_a_pause_can_never_run_longer_than_a_day`.
  - **A backup no longer carries a running pause.** `pause.blocks` was exported with the settings and written back on import - an old backup could end a running pause, or one exported mid-pause bring it back. It is machine state now (`backup.RUNTIME_KEYS`), like the mode that is on. Test: `test_a_backup_neither_carries_a_pause_in_nor_ends_one`.
  - About: after a failed update, **Check now** put Get update back next to the Try again still showing; a fresh answer now takes Try again away. Test: `test_update_standards.py::test_check_now_after_a_failure_leaves_one_way_to_get_it`.
- README: Pause my blocks; the update section (it said every 3 h / an hour on open - it is every 6 h / at most every 5 min on open, since 0.84.4).
- Tests: new `tests/test_pause_blocks.py` (23 with the review fixes): every kind of block (hours, used-up time and opening limits, temporary, permanent site, group, category, mode) is lifted by the service and the GUI alike and comes back - and the apps are closed - when the pause ends; time keeps counting toward a limit and blocks at the end; the protection lists, the words and SafeSearch are untouched; it is not the off switch and spends no emergency; resuming early blocks at once; a restart in the middle keeps it and its end; Windows' clock set back doesn't stretch it, a time before its start doesn't count, it can't run over a day; the five lengths and "rest of the day" with a 04:00 reset; the "back on in 4 min" warning; the challenge is required (nothing changes until it is passed) and resuming needs none; a locked mode makes it ask always; silenced: the bedtime screen closes and nothing comes up until the end, then it is back; a pause without silence keeps the reminders; `_show` stays quiet (urgent too) and speaks again after; the tray turns blue and back. New `tests/test_update_standards.py` (7 with the review fix): the installer by its ending with versioned / upper-case names next to an .apk and a checksum; the download named with its version and deleted at the next start (only ours); one that can't be deleted waits; a failed download offers Try again + GitHub and opens nothing, Try again downloads again; an installer that didn't start is started again from the same file; the GitHub link. `tests/test_update_notice.py`: `test_the_x_hides_the_banner_until_the_next_start`, `test_nothing_hides_an_update_for_good` (replaces the skip test), the window wiring and the narrow-banner test without "skip this version".

## 0.84.7 - 2026-10-02 20:40
- **The emergency unlock now works whatever the time - also on something that isn't blocked yet.** You had 15 minutes allowed inside a blocked stretch, and the emergency wouldn't let you in until those 15 minutes were used up. The cause: Blocking > Overview > Emergency unlock only listed what was blocked at that moment (`db.blocks(now)`), so an item still on its allowance, or before its limit ran out, wasn't there to pick. Now the list shows everything the unlock can free, in two parts - **Blocked now** and **Not blocked right now** (`emergency.choices`) - with "unlocked until HH:MM" next to anything already unlocked. Unlocking something that isn't blocked yet holds for the whole emergency: the allowance or limit running out meanwhile doesn't block it until the emergency ends, then it's blocked as usual (the rules already ignored every block during an unlock; only the picker was missing). **The time you use still counts** toward your limits and allowances, as before - the panel says so. It is still one use, however many you tick, and still refused when none are left.
- **An emergency can pause the bedtime alerts (and the break alerts).** The emergency panel has a new tick, **Pause bedtime and break alerts**: for the emergency's length the bedtime heads-up and the "Time for bed" screen stay away (a bedtime screen that is up closes within 5 s), no break prompt comes up, and a strict break that is minimising your windows stops. Ticked together with items it is the same one use; on its own it is one use too. When it ends everything comes back as normal - a bedtime screen that was due appears straight away and the escalation carries on; break time kept counting. **Your bedtime and break settings are not changed** - the pause lives with the emergency unlock itself (`emergency_unlocks.alerts`, `emergency.alerts_paused_until`, read by the reminders engine every tick).
- **"Emergency (N left)" on the bedtime screen.** So you can reach it while the screen covers everything: the bedtime screen now has an **Emergency (N left)** button next to Dismiss / Disable alerts, shown only while emergencies are on (Settings) and you have uses left. It asks once ("Pause the bedtime and break alerts for 20 min? Uses 1 of your 3...") so a stray click can't spend a use, then pauses the alerts as above - no Anti-Bypass challenge, because the emergency is the sanctioned way out and is limited by its use count. Cancel counts as Dismiss. "Disable alerts" still needs the challenge as before.
- **Anti-Bypass: permanent blocks stay out of reach, now also in the engine.** The panel never offered permanently-blocked items; `emergency.unlock` now refuses them itself (and the sites/apps of a permanently-blocked category), so no other screen can unlock them either - the experimental web view's emergency button used to pass every item, permanent ones included; it now passes only what `emergency.choices` allows. Fixed along the way: an emergency unlock on a site or app that a blocked **category** covers didn't free it (the category added it back); it now does, as it already did for a mode - except from a permanently-blocked category.
- Review fix: **leaving the bedtime screen's Emergency question open is no free pause.** Pressing Emergency took the bedtime screen down and asked to confirm - but until you answered, the reminders engine still thought the screen was up, so it never came back: an unanswered question kept bedtime away all night without spending a use. Now the click counts as a dismiss straight away (`ReminderUI._emergency_sleep`), so the screen returns on its escalation as usual; answering "Pause" later still spends the use and takes down a screen that came back meanwhile (`Engine.answer`). With no uses left by then (spent from the Blocking page meanwhile) it asks nothing and says so. The question also counts uses at the engine's own time, so the number it shows is the one that decides. Test: `test_emergency_anytime.py::test_leaving_the_bedtime_emergency_question_open_is_no_free_pause`.
- Where the emergency is offered: Blocking > Overview (while emergencies are on) and now the bedtime screen (while on and uses are left). The tray menu has none, as before.
- **No more Lockdown pop-ups over a full-screen game - Windows notifications instead (they were making games lag).** A "10 min left" warning drew Lockdown's own corner pop-up - a topmost window that faded in over the game - and that knocks a full-screen game out of its fast path for the 8 s it is up (it could even take the focus). Only reminders and the update notice waited for the game, and even a held reminder still drew one. Now:
  - **Windows' own notifications are the default** (Notifications > Show as: **Windows notifications (recommended)** / **Lockdown pop-ups** / **Both**; your saved choice is kept). They are real Windows notifications now (new `gui/toast.py`, package **`windows-toasts`**), so they carry buttons: the weekly summary's **See the week**, the update's **Install / Remind me later**, a reminder's **Done / Snooze / Dismiss** - a click is handed back to Lockdown and answered as the pop-up would have.
  - **While anything is full-screen, Lockdown never draws a window of its own** (`App._show` checks `win.is_fullscreen()` on every notice), whatever you picked: it sends an ordinary Windows notification. These are never "urgent" ones that break through Windows' Do not disturb - Adam's games lag from ordinary Windows notifications too - so during a game Windows may hold them back into the notification centre (the bell), where they stay for 2 hours. On the desktop a notice you must answer (the update) stays on screen until you do.
  - **Tip: turn on Windows' "Do not disturb when playing a game"** (Settings > System > Notifications > Turn on do not disturb automatically) and no notice - Lockdown's or anyone's - comes up in a game at all; you find them in the bell afterwards.
  - Reminders, breaks and the bedtime heads-up held back by a game now go out as a notification **with their buttons** instead of a pop-up that came anyway: answer there and it's done; leave it and the pop-up comes once the game is no longer in front (and the notification goes). The update notice no longer waits for the game to end (Do not disturb still holds it). The bedtime screen itself is unchanged - it is meant to interrupt.
  - Notices now leave the notification centre by themselves (an expiry time): the PowerShell that Lockdown started 7 s after every Windows notice to clear the bell is gone - it also deleted notices Windows had held back during a game before you could see them.
  - The Lockdown pop-ups that remain (on the desktop) are lighter: no hide-and-show of the window as it opens, they never take the focus from the window you are in, and no fade-in (a see-through window Windows re-blends on every step).
  - Without `windows-toasts` (or if Windows refuses), the tray's old balloon says the text, without buttons - except a notice with buttons on the desktop (the update's Install, the weekly summary), which is then Lockdown's pop-up so its buttons are kept. Over a game it is still the balloon; a held reminder still comes as its pop-up after the game.
  - A mode that mutes notices also mutes the notification of a held reminder (it comes as a pop-up when the mode ends, as before).
  - **New dependency:** `windows-toasts` (pulls in the `winrt-*` runtime parts, a few MB) - in `requirements.txt` (Windows only) and bundled by `installer/lockdown.spec`.
- Review fix: **leaving the bedtime screen's "Disable alerts" challenge (or its Off tonight / Snooze question) open is no free pause either.** The engine kept thinking the bedtime screen was up while either was open, so it never came back. Now they close after 2 minutes untouched (no key typed in the challenge - a long phrase may take longer than that to type, so typing keeps it open; 16 minutes at most in all; passing it gives the question its own 2 minutes) and that counts as **Dismiss**: the screen returns on its escalation as usual. The Emergency question likewise closes after 2 minutes (it already counted as dismissed; left open it would have blocked the clicks on a bedtime screen that came back).
- Database: `emergency_unlocks` gets an `alerts` column (schema version 4; added to existing databases on the first start).
- Tests: new `tests/test_emergency_anytime.py` (14 with the review fix above): an emergency on a game with allowance left holds past the allowance running out and blocks/closes it when it ends, with the time counted; the same before a daily limit runs out; blocked and not-blocked items are both offered, listed apart; permanent items and a permanent category's sites are never offered and refused (no use spent); unlocking a site frees it from a blocked category but not a permanent one; refused when off or out of uses, an item plus the alerts is one use, alerts alone is one use, the count resets with the week; an old database gets the new column; the bedtime screen's Emergency button shows only with uses left and emergencies on; pressing it pauses the bedtime screen for the emergency and it comes back after, with the stored bedtime settings unchanged; pausing from the Blocking page closes a bedtime screen that is up; the heads-up waits during a pause; with no uses left (spent elsewhere) it just dismisses; a pause holds the break prompt and ends a strict break. Updated: `test_periods.py::test_emergency_uses` uses a temporary block instead of a permanent one (permanent ones are now refused), `test_sleep_escalation.py::test_the_overlay_offers_dismiss_and_disable` allows the extra Emergency button, `test_db_speed.py` pins schema version 4.
- Tests: new `tests/test_game_notices.py` (25): over a full-screen app no Tk pop-up is built whatever the setting, and the notification is an ordinary one (never through Do not disturb) kept 2 h; on the desktop the setting decides, a notice to answer stays on screen, the digest keeps its button; the update notice goes out over a game but waits for Do not disturb, and its notification is taken down when answered elsewhere; the PowerShell clean-up is gone; a reminder held by a game goes out once with its buttons and no pop-up, answered there it is done (a late click does nothing), unanswered the pop-up follows the game and its notification goes; a button click is run on the Tk thread (`call_soon`), a click on the text opens Lockdown; notices expire by themselves; no "urgent" scenario; without `windows-toasts` (or when it fails) the balloon says it; the bedtime challenge / question left 2 min counts as Dismiss and the screen comes back on its escalation, answered in time the limit does nothing, cancelling dismisses once; still typing the challenge keeps it open (16 min at most), a challenge passed late gives the question its own 2 min; a muting mode sends no notification for a held reminder; without Windows notifications a notice with buttons is a pop-up on the desktop (never over a game). Updated: the held-reminder checks in `test_reminders.py` and `test_fewer_interruptions.py` (a notification with buttons instead of a toast line), `test_draft.py` (the default format is now Windows notifications), `test_app_id.py` (the second copy of the app id now lives in `gui/toast.py`).

## 0.84.6 - 2026-10-02 13:50
- **A new installer window, like Handy's: a welcome page, "Lockdown is running - OK to close it?", a real progress bar, and a finish page.** The old one was a small grey box with a text log. Now (`installer/setup.py`), in Lockdown's dark look with its logo and your accent colour (read from your settings):
  - **Welcome** says what will happen: "Update to Lockdown 0.84.6 - you have 0.84.5", the two versions side by side, that your settings, blocks and history are kept and that Lockdown starts again afterwards. [Update] [Cancel]. A fresh install says where it goes; the same version again says so and still asks once before reinstalling (now inside the window instead of a Windows message box).
  - **If Lockdown is running** (the window/tray app or its service), the window asks first: "Lockdown is running. It will be closed to update it, then started again." [OK] [Cancel] - nothing is touched until you say OK (`lockdown_running`).
  - **Progress**: a thick rounded bar in the accent colour, driven by the real steps - stopping Lockdown, copying the program (file by file from the installer, so it really moves), the data folder, registering the service, shortcuts, the Apps & features entry, starting it (`INSTALL_WEIGHTS`). The step it's on is shown above the bar, the file being copied below it, and **Show details** opens the full log. A step that takes a while with nothing to report (waiting for the service to stop) still inches forward, but never past its own share, so the bar never runs ahead of the work (`ease`).
  - **Finish**: "Lockdown 0.84.6 is installed", **Run Lockdown now** ticked, [Finish].
  - **Uninstall** looks the same: what it removes, "Also delete my settings, blocks and history", then the progress page. **The Anti-Bypass challenge is still required first**, exactly as before; if it isn't passed, the window says "Lockdown was not uninstalled" and nothing was touched.
- **Updating from inside Lockdown asks nothing and closes by itself.** "Download and install" now starts the installer with `--update` (`updates.INSTALLER_ARGS`): no welcome and no "it's running" question (you already said Install, and Lockdown closes itself) - only the progress page; at the end Lockdown is started again and the window closes after 1.5 s, like Handy's update. This works from the next update on (0.84.5 starts the installer without `--update`, so this one update shows the welcome page).
- **A failed install or update never leaves Lockdown off.** It used to show the error with the service possibly still stopped (the watchdog task switched off for the update). Now it starts the service and its watchdog again, and the app, whatever copy is in place (`recover`) - "Lockdown was started again, so blocking stays on" - and the error stays on screen with the details open and a [Close] button. A failed uninstall (after the challenge) does the same.
- Sharp on 125 % / 150 % displays (the installer says it's DPI-aware and scales its layout), dark title bar, Inter / Barlow Condensed like the app (bundled into LockdownSetup.exe; Segoe UI if they can't be loaded). All window work stays on the Tk thread: the install runs on a worker thread that only posts to a queue (`work`), and the window reads it every 30 ms - the old window called Tk from the worker thread. Round shapes are drawn as antialiased pictures (`raster`; Tk's canvas draws curves jagged); the bar redraws only the columns around its round ends (`bar_rows`). The install steps and their order are unchanged; copying now extracts one file at a time instead of `extractall` (same result).
- If the installer window goes away half-way (an error in the window, Windows closing it), the install still finishes and starts Lockdown again: the worker thread is no longer a daemon that dies with the window, and an error while the window reads the worker's news no longer stops it following the install. The details box shows whole lines (no half-cut line at the top).
- Build: `scripts/build.ps1` (and the Wine build) add `assets/setup` (the logo in 4 sizes) and three font files to LockdownSetup.exe.
- Tests: new `tests/test_setup_window.py` (17): `--update` and the other arguments, the page flow (only an install you started asks; an update goes straight to progress; uninstall goes welcome -> progress), the bar's weighted progress and its easing (never back, never past the step), the fast bar drawing matches the full picture, copying reports every file, the install steps keep their order, update mode starts Lockdown again (an interactive install leaves that to the Run tick), a failed update and a failed uninstall start the service, watchdog and app again, uninstall still needs the challenge (refused: nothing touched); with a display: the update window asks nothing and closes after starting Lockdown, a running Lockdown is asked about in the window, a failure stays on screen with the details open; the install outlives the window; a bad event does not stop the window following the install. `tests/test_auto_updates.py`: the installer gets `--update`, and setup reads it as an update.

## 0.84.5 - 2026-10-02 13:40
- **"Download and install" now really installs - it no longer sits at 100%.** You pressed it and nothing happened after the download. The installer is built to need admin rights, and Lockdown started it with `subprocess` (CreateProcess), which can't ask Windows for them: Windows refused with error 740 ("The requested operation requires elevation"), the error went nowhere, and the bar stayed at 100% with Lockdown still open. This was in every version with in-app updates. It is now started through the Windows shell (`ShellExecute` with "runas"), which shows the admin prompt like double-clicking it does (`updates._run_elevated`). If it still can't start (or you say No on the prompt), the About page now says so and shows where the installer was saved, instead of hanging.
- Because the bug is in the version you have, **this one update still has to be installed by hand** (download LockdownSetup.exe from the release page and run it). From 0.84.5 on, the banner's Install works.
- Tests: `test_installer_is_started_through_the_shell_so_windows_can_ask_for_admin`.

## 0.84.4 - 2026-10-02 10:53
- **A new version now tells you - and stays on screen until you deal with it.** You were right that it didn't: three causes, all in the window's update check (`gui/app.py` `_poll_updates` / `_update_found`, `updates.py`).
  - **It only looked once a day.** `updates.EVERY_HOURS` was 24 (first check 90 s after start, then a look every hour whether a day had passed), so 0.84.1, 0.84.2 and 0.84.3 - all released within nine hours - could come and go between two checks. Now it checks a minute after every start, then every 3 hours (`EVERY_HOURS = 3`), and when you open the window from the tray if the last check is over an hour old (`OPEN_HOURS`). A check that gets no answer (no network, GitHub refusing) is tried again on the next poll, but never within 15 minutes of the last try (`RETRY_MIN`, `updates.trying`), so it stays far inside GitHub's 60 requests an hour for a caller without an account - at most 4 an hour even with GitHub down all day.
  - **What it found was one popup that faded after 8 seconds** (or closed on any click), and it was then marked "said" for good - nothing in the window or the tray showed it again, and the About page said nothing until you pressed "Check for updates". **And a muted mode swallowed it**: `_show` returned without showing anything, but the version was still marked as said.
  - **Now, while a newer version is waiting:** a slim banner over every page - "Lockdown 0.84.5 is available · you have 0.84.4 · [Install] [Later] skip this version ✕" -, a dot on the About item in the sidebar, and "Update available: Lockdown X" with an **Install update** entry in the tray menu. What was found is kept (`updates.FOUND_KEY`), so all three are there straight after a restart, without asking GitHub; the About page offers "Download and install" as soon as you open it. Built once and only shown / hidden / relabelled when what to show changes (`refresh_update`, every minute; nothing is touched when nothing changed). The About icon's dot is drawn into the icon (`theme.icon(..., dot=)`), so it follows hover and selection.
  - **When it looks (your choice):** every time you open the Lockdown window, and every 6 hours while it only runs in the background - never two requests within 5 minutes (`updates.EVERY_HOURS = 6`, `OPEN_HOURS = 0`, `RETRY_MIN = 5`), at most 12 an hour even if you open the window non-stop, far under GitHub's 60.
  - **The corner popup** comes once per version and stays until you answer it (`Popup(..., actions=, sticky=True)`): **Install**, **Remind me later**, ✕. Remind me later hides the popup *and* the banner for 4 hours, then both come back (`updates.snooze` / `snoozed`, by Lockdown's trusted clock; a snooze can't be stretched past 4 h by a clock that jumped). ✕ on the popup only closes the popup - the banner stays. ✕ on the banner skips that version: nothing more about it, only about a newer one (`updates.skip` / `skipped`). The popup waits - not marked as said - while Do not disturb is on, a full-screen app is in front or a muted mode is on, and comes up once that is over; the banner shows meanwhile either way. "Tell me when a new version is found" off stops only the popup.
  - **Install** (banner, popup, tray) uses the About page's download with its progress bar, then the installer, as before - never a second download while one is running (`AboutPage.downloading`). A release with no installer attached offers its GitHub page instead. Only ever forward: anything not newer than the running version is never offered (`updates.available` re-checks against the version running now, so the banner goes once the update is installed).
  - Nothing here touches blocking or Anti-Bypass.
  - Found in review: **a popup left open while an even newer version came out kept offering the older one** (its Install would have downloaded it, and the one-popup-at-a-time guard kept the new popup away). It is now closed when the version it offers is no longer the one to show - newer release, installed, skipped or snoozed - and the popup for the newer one comes up (`test_a_newer_release_replaces_the_open_popup`). **In a narrow window the banner lost its Install button** (pack squeezes what was packed last, and that was the buttons): the buttons are packed first, and when room runs out "you have X" and then "skip this version" step aside - its ✕ stays (`test_banner_buttons_survive_a_narrow_window`).
- Tests: new `tests/test_update_notice.py` (20 tests): checks every 6 h in the background and on every window open (not twice within 5 min), a failed check retried after 5 min and no sooner, at most 12 requests an hour with GitHub failing all day, off means no checks; the banner stays across restarts and goes once installed, never an older or the same version, a failed check keeps what was found, Remind me later hides popup and banner for exactly 4 h and the popup comes once more, a clock turned back can't stretch the snooze, skip hides until a newer version, popup off keeps the banner, popup once per version while the banner stays; the tray line and its menu refresh only on change; the window's wiring (banner, About dot, tray follow the found version; Later and skip from the window; the popup waits for Do not disturb / a muted mode and is then shown with Install / Remind me later and doesn't fade; Install never starts a second download, a release without an installer opens its page; a newer release replaces the open popup; the banner keeps Install / Later / ✕ in a narrow window). `tests/test_auto_updates.py`: the daily-check test now checks every `EVERY_HOURS`; `tests/test_ui_threads.py`: `_poll_updates` reschedules in a `finally`.

## 0.84.3 - 2026-10-02 10:11
- **A member's own limits in a group now come on top of the group's - they never replace them.** As you asked: the group's rules always apply to every member, and a member's own rules only tighten it. Before, a rule "customized" for a member took the place of the group's rule of that kind and counted for that member alone (`rules.effective_rules`: `usage_owner = item`), so "Fun" 2 h a day with YouTube's own 1 h gave YouTube its own hour that never filled Fun's 2 h, and YouTube's own night hours replaced the group's. Now every member gets every group rule, counted in the group's shared pot as for everyone, plus its own rules besides (`extra_of` the group, its own pot):
  - **Time and openings fill both.** 1 h of YouTube is 1 h of its own 1 h *and* 1 h of Fun's 2 h; YouTube stops at 1 h, the other members can use the hour Fun has left. Opening and switch limits are counted in the group's total and the member's own the same way. Whichever limit runs out first blocks it.
  - **Blocked hours add up, allowed hours narrow.** Group blocked 15:00-18:00 and YouTube's own 13:00-14:00: YouTube is blocked 13-14 and 15-18, the other members 15-18 only. An "allowed only" window of the member's own can only narrow what the group allows, never widen it (blocked if either blocks).
  - **The group's "N minutes allowed during blocked hours" stays the group's.** Time on YouTube in the group's blocked hours spends the group's shared pot as before; a member's own blocked hours have their own allowance, for that member, which can't open the group's hours.
- **Customizations saved before keep their values but can no longer loosen anything.** They now add to the group's rules, which can only make things stricter, so there is nothing to approve. A customization looser than the group no longer gives anything back: YouTube "3 h" in a 2 h group now stops at the group's 2 h, an app given "5 min allowed" in a night group whose block has no allowance is now blocked all night, "10 openings" in a 3-openings group stops after 3. What a member already used under a customization still counts for its own limit after the update (the member's rule keeps the key its usage is stored under, owned by the member, so it never mixes with the group's pot) - but it was counted for that member alone, so it isn't in the group's total: the group's total for the current day (or week, month) is short of what that member used before the update, and counts it fully from the update on.
- **The screens say so.** Blocking > Groups > a member is now "Extra limits for YouTube, on top of the group's", each rule "Also limit this member: time limit", with a line explaining that the group's rules still apply and its time still fills them; a member's chip says "+ extra limits". On Blocking > Overview a member inside a group's box shows its extra rules as live chips under its name ("Limit (own): 40m / 1h 00m today", next to the box's "Limit (group): 1h 10m / 2h 00m today"); a group's limit says "(group)" instead of "(shared)". The dashboard's limits card lists a member's own limit as its own row ("YouTube (own, in Fun)") next to the group's, and the web view's limit shows the one with least time left. The groups list now shows a group's opening count (it read an empty bucket and always said 0).
- **Anti-Bypass knows the new rule.** Adding or tightening a member's extra rule is free; removing or relaxing one (or changing its hours) is loosening and asks for the challenge (`antibypass.group_looser`). Adding a big extra rule ("3 h" in a 2 h group) used to count as loosening the group; it can't any more, so it's free. Clearing away an extra temporary block that has already run out is free.
- **Retrying a blocked member doesn't use up the group's openings.** An app Lockdown closes the moment it opens hasn't really been opened. Before, with "Fun" at 3 openings and YouTube's own 1, the second YouTube start (closed: over its own 1) and every retry after it each took one of Fun's openings, so Twitch was soon blocked by a YouTube nobody could use. Now an opening (or switch) of an app that is blocked and closed counts in none of the opening limits, and the one that goes over a limit counts only in the limit it goes over - that's what closes it (`rules.closed_opening`, `UsageTracker.unless_closed`). Only for apps set to "Close app": one that is only minimized or cut off the internet can still be used, so its openings count as before, and so do sites (a "can't load" tab may still play over a connection already open). Errs on the side of counting: an opening less than 2 minutes before its block ends counts (it may not be closed in time), so does any opening in an emergency unlock, and anything that can't be read counts everything. Time is counted as before - a closed app is gone in seconds. The service's backstop, which counts only while the tray app isn't, still counts every opening.
- **"Blocked until" is the end of the longest block.** Two rules can now block a member at once; the time shown was the first one's. Group blocked 15-18 and YouTube's own 17-20 said "until 18:00" at 17:30, the group's day limit and YouTube's own week limit said "tomorrow". Now it's the latest end among the rules blocking for the top reason (`rules.item_block`), on the overlay, the dashboard and the notices.
- **The allowance notice, the dashboard and the web view show the limit that really limits.** A group giving each member 30 min a night and YouTube's own 5 min said "28 min of your 30 left" (`alerts`); the dashboard's "limit will be reached" row showed YouTube's own 25 min while the group had 10 left; the web view showed only the group's hours and the last allowance. Now each shows the one with least left, and the web view lists the member's own blocked hours with the group's.
- **A member can get an extra limit of any kind, and keeps it.** Blocking > Groups > a member offers hours, time limit, opening limit and temporary - not only the kinds the group has (a group with only night hours can give YouTube 1 h a day of its own). Saving a group used to drop a member's extra whose kind the group no longer had - removing a limit without a word, and then asking for the challenge with nothing to show why; now it stays. An extra temporary block that has run out is cleared away (free).
- Not changed: the Android engine (`engine/Rules.kt` `effectiveRules`, `AntiBypass.kt` `groupLooser`) still has the old replace behaviour.
- Tests: new `tests/test_group_merge.py` (26 tests): `test_youtube_stops_at_its_own_hour_and_that_hour_fills_the_group`, `test_when_the_group_runs_out_first_the_member_stops_too`, `test_the_chips_show_both_limits`, `test_the_tracker_writes_the_member_both_pots_on_time`, `test_blocked_hours_add_up`, `test_allowed_hours_intersect`, `test_the_shared_allowance_stays_shared_and_youtube_spends_it`, `test_a_member_own_allowance_is_its_own_pot_on_top`, `test_an_old_looser_limit_no_longer_loosens`, `test_an_old_night_allowance_no_longer_opens_the_group_block`, `test_an_old_looser_opening_limit_no_longer_loosens`, `test_upgrading_keeps_what_the_member_already_used`, `test_removing_or_relaxing_an_extra_rule_needs_the_challenge`, `test_openings_fill_the_group_and_the_member`, `test_switches_fill_the_group_and_the_member`, `test_a_disabled_group_still_counts_the_member_both_ways`, `test_extra_rules_round_trip_through_the_database`, `test_retrying_a_blocked_member_does_not_use_up_the_groups_openings`, `test_a_blocked_member_that_is_only_minimized_still_spends_the_groups_openings`, `test_an_opening_just_before_a_block_ends_still_counts`, `test_an_emergency_unlock_counts_the_opening`, `test_blocked_until_is_the_latest_of_the_rules_blocking_it`, `test_the_allowance_notice_names_the_allowance_that_limits_it`, `test_the_dashboard_limit_warning_is_the_limit_with_least_left`, `test_the_web_view_shows_both_blocked_hours_and_the_tightest_allowance`, `test_a_member_extra_needs_no_group_rule_of_its_kind_and_an_old_temporary_one_is_cleared`. Changed to the new rule: `test_rules.py::test_effective_rules_inherit_and_override` (the member's rule is now a fourth rule after the group's, not in its place), `test_db.py::test_member_override` (the member with its own "5 min allowed" is now blocked by the group's night block too), `test_antibypass.py::test_draft_changes` (adding a member's 90-minute limit to a 30-minute group is no longer loosening; relaxing or removing a member's extra limit is).

## 0.84.2 - 2026-10-02 01:31
- **The bedtime / break screen now fills exactly your main monitor and its message is centred on it.** On a scaled display (125%, 150%) it used to come out too big, spill onto the second monitor and sit off-centre.
- **Other monitors are covered with a plain dark screen while it is up, instead of showing a piece of it.** That cover can't be closed with Alt+F4, the same as the bedtime screen itself.
- **If a monitor is plugged in or unplugged, or the main monitor changes, while the bedtime / break screen is up, it follows: the message moves to the main monitor and every other monitor is covered.** A screen moved off its monitor goes back.
- **The bedtime / break screen is smoother: it appears in one go, finished, and it's no longer see-through (which had to be redrawn whenever anything moved behind it).** Its countdown only redraws once a second and still shows the right time left after the PC sleeps.
- **Time on a game (or a site) now also counts while it is the window in front on another monitor.** As you asked: a game left on the second screen while you browse on the first counted nothing, because only the one window with the keyboard focus was counted (`usage.sense_desktop` read `win.foreground_process` only). Now the tracker also counts every window you can see on any monitor, into the same limits, allowances, group and category limits. "In front" means: shown, not minimized, not on another virtual desktop (DWM "cloaked"), and enough of it in view past the windows above it - at least half of it, or at least a quarter of a monitor - and never less than 1 % of a monitor (`win.front_windows`, `win.pick_front`, `win.seen_enough`). So a game behind another window does not count, and a minimized one does not either. A small window on top (a calculator, a picture-in-picture video) doesn't hide the game or video under it - that also closes the old gap where clicking a small window on top of a full-screen video stopped its count. Note: this is "you can see it", not "the one top window per monitor" - two windows side by side both count, on the focused monitor too, and a game with a third of the screen in view still counts. A strict "top window only" rule would let any window parked over part of the game stop its count. One `EnumWindows` per tick, from the top of the Z-order down.
- **A browser on top of another monitor counts its site too.** Its active tab's address is read with UI Automation from that window, as for the focused one. When the address bar can't be read (it is hidden while a video plays full screen), the address last read from that window still counts while its title is unchanged - focused or not (`usage._read_url`). A browser window that fails three reads in a row (an installed web app has no address bar) is looked at again only every 30 s, and one that won't answer still counts as its app.
- **Each item, and each limit, counts once a tick, however many windows show it** - a game spanning two monitors, or with a window on each, gets the real time once. A shared group or category limit fed by the game on monitor 2 and YouTube on monitor 1 gets 2 s per 2 s, not 4 (each limit's bucket is added once per tick; this also fixes a browser app item and a site item in one group counting twice, which was possible before). A locked PC still counts nothing on any monitor.
- **Hiding the game from the count doesn't work (found in review).** Only a window you really see hides what is under it: a see-through window (AutoHotkey `WinSet Transparent`, alpha below 90 %, a colour key or per-pixel transparency), a click-through, tool or "no activate" window, and one whose region (`WinSet Region`) cuts it down hide nothing - or only the part that is left (`win.cover`). Restyling the game or the browser itself (tool window, "no activate", click-through) doesn't take it out of the count; only a window you can't see at all (alpha 5 % or less) isn't counted. A window stretched over both monitors counts for its part on either (it used to belong only to the monitor it was mostly on). Covering everything around a video in a maximized browser leaves it counted (a quarter of a monitor in view). A Store app (UWP, e.g. the Netflix app) counts as the app inside its frame, not as ApplicationFrameHost.exe - for the focused window too (`win.content_pid`). A window Windows errors on is skipped alone, not every other monitor's count with it; the process list is read once per tick for windows whose process can't be opened, not once per window.
- **Screen time stays what you were doing.** The Screen Time pages, the switch log and "every switch" opening limits still follow the focused window only: switching between two windows on monitor 1 is not a switch to the game on monitor 2.
- **Known limits.** A live mirror of a hidden window (OnTopReplica, an OBS projector, Magnifier) shows the mirror tool in front, not the game. A program written to draw an invisible window over the game could still hide it. A blocked site in a window that isn't focused is stopped by the network block only (its tab isn't closed). Not yet tried on a real Windows PC with two monitors: the window listing is faked in the tests.
- **Lockdown now checks the windows twice a second instead of every 2 seconds.** As you asked, to make everything react faster: a block now starts on the half-second the time runs out (it could be 2 s late) and a site in the focused tab is sent back within about a second (it could take up to 4), launches and switches are seen sooner, and the windows in front are counted sooner. The time counted doesn't change - since 0.84.1 it is the time that really passed, whatever the tick (`usage.TICK_SEC` 2 -> 0.5).
- **It costs about the same as before, not four times as much.** Measured with the new `scripts/bench_tick.py` (one tick with the desktop faked; simulated 2 ms per address-bar read, 1.5 ms per process list): 2.4 ms of CPU per second of real time, against 2.2 ms when it ticked every 2 s and 8.4 ms if it had simply ticked four times as often; database commits 30 a minute instead of 90 (360 the simple way). How: the windows in front and the focused window are looked at every tick (cheap); a browser's address bar (UI Automation, the slow part) is read only when its window or tab title changed, or 2 s after the last read - a page can change its address and keep its title (a single-page site), so a move to a blocked site is still caught within 2 s, as before (`usage._read_url`, `URL_REFRESH_SEC`); when the address bar is hidden (a full-screen video) it is looked for every 2 s, not every tick. The process list (to spot app launches) is read every 2 s, and at once when a window in front belongs to a new process - so a program started (or closed and started again) is counted on the tick it appears (`UsageTracker.running_now`). An hours rule's state is worked out once per rule and minute (`rules.schedule_until`; hours are whole minutes, so the answer is the same all through a minute).
- **The counted time is written every 2 s, in one go - and at once when a limit or an allowance is about to be reached.** Writing every half second per item would have meant four times the disk writes. Now time waits in memory and is written every 2 s in one transaction with the "counted until" mark (`db.add_tracked`) - every tick instead while a time limit or allowance in use is within 2 s of running out, so the database (which the service and the tab check block by) reaches it on the very tick it runs out (`UsageTracker.near_limit`, `rules.limit_targets`). An opening (a launch, a new visit, a switch) is written at once too, as one can go over an opening limit. When the app closes, what is still waiting is written (`UsageTracker.stop`); a failed write is tried again on the next tick, nothing dropped. Fractions of a second are kept per limit until they make a whole second, so switching between two things every half second gives each its half, not all of it to one. If the tray freezes with time not yet written and the service counts the game meanwhile (its backstop), the part the service covered is not added twice.
- **The tab check and "Minimize" blocks hear about a limit running out at once.** The tab check already looked at the tab twice a second but re-read the list of blocked sites only every 2 s, and the window re-read its "Minimize" app blocks only every 5 s; both now re-read as soon as the tracker writes time that reaches a limit (`usage.limit_writes`, `word_guard.BlockedSites`, `app._read_minimize_blocks`). Still every 2 s: the service's own pass, which updates the hosts file and closes a blocked app (a site in the focused tab is sent back by the tab check without waiting for it); the warnings ("blocked in 5 minutes") stay every 5 s.
- Tests: new `tests/test_second_monitor.py` (52 tests), with the window list faked and the real selection, `sense_desktop` and tracker: a game on top of monitor 2 while YouTube is focused on monitor 1 (both count), minimized (no), behind another window (no), mostly covered (no), a small window or a tooltip / overlay / taskbar / hidden window above it (still counts), on another virtual desktop (no), spanning both monitors or with a window on each (counted once), YouTube on top of monitor 2 (counts) and behind a window there (no), a category limit, the 60-minute limit reached and the game blocked, locked (nothing), screen time and switches foreground-only, one enumeration per tick, a browser without an address bar, a browser that won't answer. Found in review: `test_a_see_through_window_over_monitor_2_does_not_hide_the_game`, `test_a_nearly_opaque_layered_window_still_hides_the_game`, `test_restyling_the_game_window_does_not_take_it_out_of_the_count`, `test_a_restyled_youtube_window_on_monitor_2_still_counts_as_youtube`, `test_covering_the_rest_of_the_browser_does_not_hide_the_video`, `test_a_browser_stretched_over_both_monitors_counts_for_its_part_on_monitor_2`, `test_a_window_cut_down_by_a_region_hides_only_what_is_left_of_it`, `test_a_shared_group_limit_counts_two_monitors_once`, `test_one_window_windows_errors_on_does_not_stop_the_count_elsewhere`, `test_a_minimized_game_s_little_helper_window_does_not_count_it`, `test_a_full_screen_youtube_video_still_counts_when_the_address_bar_is_hidden`, `test_one_failed_read_does_not_drop_the_site_for_30_seconds`, `test_a_store_app_counts_as_the_app_not_its_frame_host`, `test_the_process_list_is_read_once_however_many_protected_windows`, `test_a_partly_covered_window_counts_only_while_enough_of_it_shows`. `test_unlocked_the_window_in_front_is_reported` now also expects the (empty) list of other windows.
- **Found in review of the faster tick.** A browser window in front with no address bar (an installed web app) was searched for one twice a second - now every 2 s like a hidden bar, and at once when its title changes (`usage._read_url`). The tab check and the window's "Minimize" blocks are told once, on the write that makes a limit or an allowance run out (or counts an opening), not on each of the up to 5 writes in the 2 s before it - each of those made the window re-read every block on its own thread. A tick now reads everything it needs from the database before it counts anything, so one that fails on a lock no longer drops an app launch or counts screen time twice.
- Tests: new `tests/test_fast_tick.py` (24 tests): half-second ticks count real time exactly (20 minutes = 1200 s, screen time too), uneven ticks, switching every tick (each gets its half), a stall covered by the service not counted twice; writes batched (30 transactions a minute, never more than 2 s behind), written on stop, a failed write loses nothing; the block starts within a second of an allowance running out and the tab check sends the tab back on that same tick, a daily limit blocks on the very tick it runs out, every tick written only near a limit (not once over it), an opening written at once; `limit_targets` matches `usage_targets`, `schedule_until` the same all through a minute; the address bar not read again while nothing changed, read at once for another title or window, a page that keeps its title caught within 2 s, a hidden address bar not searched every tick; the process list every 2 s or for a new process (the launch counted on that tick); the tab check's site list re-read every 2 s and when a limit is reached; found in review: `test_a_browser_window_with_no_address_bar_in_front_is_not_searched_every_tick`, `test_the_tab_check_is_told_once_when_the_limit_is_reached_not_on_every_write_before_it`, `test_a_tick_whose_database_read_fails_counts_nothing_twice_and_loses_no_launch`. Tests that ticked every 2 s with no clock now move one (`test_usage.py`), and the tests that tick every 2 s start the tracker "counting since the start" (a fresh tracker's first tick counts one tick, now 0.5 s).

## 0.84.1 - 2026-10-02 01:06
- **A game is closed outside its allowed hours even when it runs as another exe.** You played 1 h 30 m outside the allowed hours and nothing stopped it. The service only closed processes whose name was exactly the exe on the list. A game rarely runs as that exe: an Unreal Engine game's `Game.exe` (what the Steam picker chose, because it is named like the game) only starts `Binaries\Win64\Game-Win64-Shipping.exe`, and Steam often starts the Shipping exe directly. So the starter was closed and the game played on, unless "also close its background processes" was ticked and the item had a path. Now everything that *is* the app is closed the same way (at once if started while blocked, after 10 s if it was already open): its own exe, what it starts from its own install folder (remembered after the starter has gone), and for a Steam game anything that runs from the game's folder (`service.Enforcer.app_processes`, `blocker.apps.app_folder`). Steam itself, Windows and Lockdown are never touched; for an ordinary app, a program from the same folder that it did not start stays alone (Word's folder holds Excel). The Steam picker now picks the `...-Shipping.exe` (`steam.main_exe`). Apps added by name only get their install path filled in from a running copy (`learn_paths`), so their folder is known too.
- **Time on a game is counted as the time that really passed, also when its window is another exe, and also when the tray app isn't counting.** The tray app's tracker added a fixed 2 s per tick however late the tick came, so with the tray app busy two hours of play counted as about twenty minutes and "N minutes allowed during blocked hours" never ran out. It now counts the real time between ticks (at most 60 s, so a sleeping PC isn't play). It also counts a game whose window belongs to another exe of the same game (the Shipping exe). And while the tray app is closed, crashed or stuck, the service counts every limited app that is running, into the same limits and allowances (it can't see what's in front, so it counts more, never less). Both write one shared mark, so no stretch is counted twice or not at all.
- **An app typed as a full path is blocked.** `C:\Games\Foo\Foo.exe` typed (or pasted, with quotes) into the box became the target as written. No process has that name, so the app was never closed and its time never counted. The box now keeps `foo.exe` and remembers the path. Items saved that way before are matched by their exe name everywhere: the service, the tracker and Minimize (`blocker.apps.exe_name`).
- **A game Windows won't let Lockdown close is now in the log.** If closing failed (for example anti-cheat refusing it), nothing was written and the game kept running without a trace. It is now logged once per process ("Couldn't close blocked app ... - Windows refused") and retried.
- **A game outside Steam is closed and counted too, and so is a game whose starter has already gone (found in review).** The first fix above only knew Steam's folders, and only followed what the starter started while the game was blocked. So an Epic, GOG, Xbox or standalone Unreal game started inside its allowed hours, whose `Game.exe` started `Game-Win64-Shipping.exe` and exited, played on after the hours ended, and its window counted 0 s towards its limit (60 minutes of play: nothing). Now: an Unreal game's Shipping exe is the game wherever it is installed (`blocker.apps.names_of`); a game installed in a game library - Steam, Epic Games, GOG, XboxGames, Riot Games, EA Games, Ubisoft, Amazon Games, itch - is its whole folder (`apps.game_folder`; another game, and the store's own client, stay alone); and what any listed app starts from its own folder is followed all the time, blocked or not, and remembered after the starter exits (`service.Enforcer.track_families`). The service tells the tray app which running processes belong to which listed app (setting `apps.members`, not part of a backup), so the window in front is counted even when it has another name or anti-cheat hides its path from the tray app. A Steam game added by name only now also learns its folder when Steam starts the Shipping exe directly (from the game's folder, or from where Unreal puts the starter); a renamed copy on the Desktop / Downloads / Documents is never learned as the game's home. A launch of the Shipping exe counts as an opening.
- **While the tray app isn't counting, the service also fills category limits and opening limits (found in review).** Its backstop counted only items of type app, so a "Games" category with a 30-minute limit stayed at whatever it had while the game ran on, and "launches per day" never filled. It now adds the time to the category blockers of the apps that are running (and of any running program you put in a category, one pot per category, as the tray counts it) and counts launches.
- **Two blocks on the same exe add up; closing wins (found in review).** A second item for the same exe set to "minimize", or a "minimize" category blocker next to an old item saved as a full path, replaced the "close" block - the game was minimised, not closed. They are now merged: every flag of both, "close" over "minimize" (`apps.merge_blocks`).
- **A process number Windows hands to another program is not "the game".** A followed child is recognised by number and name, and its start time is checked again before it is closed.
- **The option switches look right again: Theme, Day / Week, Today / 7 days and the rest.** Their labels were drawn with a Tk font size in points, which Windows' display scaling enlarges again on top of the app's own scaling. At 150 % they were twice the size of the text around them and spilled out of their chips. They are now 13 px like the body text, scaled once. The switch is 28 px tall, level with the entries beside it. Each label has 12 px either side, so the chosen chip no longer pinches its text and the others don't run together. The track has a hairline border, so it shows on a card and on the page in light and dark. Hovering only brightens the label; it no longer fills the chip, which looked like a second selection. The padding and the gaps between chips belong to the nearest chip (they were dead strips under a hand cursor). With the Yellow accent the chosen label is dark (white on yellow was 2.6:1). The Network Log's All / Allowed / Blocked lines up with the menu below it. This is one shared component (`gui.components.Segmented`), so all 15 places that use it change together; how they behave is unchanged.
- **YouTube is blocked when its "minutes allowed during blocked hours" run out, even if you just watch.** Your group blocks 21:00-05:00 with 15 minutes allowed; those minutes only ran out while the tray counted, and it counted 2 s per tick however long had passed and stopped after 15 minutes without keyboard or mouse input. Sites now count real elapsed time and keep counting with no input (only a locked PC stops it), so the allowance is used up and the block starts. Test: `test_youtube_watched_without_touching_anything_uses_up_the_allowance`.
- **A YouTube tab that was already open stops too.** When a site block starts, the open connections are taken from the DNS cache before it is flushed and cut, browsers get a firewall rule against QUIC (UDP 443) while any site is blocked, and the tray sends any blocked site in front back (closing the tab if that doesn't leave the page) - IPv6 connections can't be cut on Windows, so this is the fallback.
- **The tab check starts again after a failure.** It started outside the restart loop, so one failure at start-up switched closing tabs off until the app was restarted.
- **A Neutral subdomain no longer takes YouTube out of Work and Focus.** Setting music.youtube.com to Neutral made the whole YouTube item not Distracting; any Distracting host now wins.
- **Sites saved as www.youtube.com get the video hosts too** (googlevideo.com and the rest), like youtube.com.
- **An hours rule with allowed minutes left shows orange "soon", not red "blocked".**
- **Checked, no change: no Mode can let a game run outside its hours.** Work, Study, Focus (a Pomodoro break too), Do Not Disturb, Relax and a mode of your own, started by hand, until a time, locked or by schedule, only ever add blocks. A disabled item still counts its time but doesn't block (0.79.3). An emergency unlock ends and the game is closed again. A rule changed in the window, or straight in the database file, reaches the running service on its next pass (0.84.0 cache).
- Tests: new `tests/test_app_limit_bypasses.py` (30 tests): an Epic game whose starter exited, its Shipping window counted toward the limit, the starter gone before the first look, a standalone game's child with another name (closed and its allowance counted; a program from the same folder that it didn't start left alone), a renamed exe in an Epic folder, the Epic launcher and other games left alone, `game_folder` for each library, a Steam game by name only started as its Shipping exe, learning its folder from a process with another name, a Desktop decoy not learned, a Shipping launch counted, a category limit and launches counted with the tray app closed, a protected window without a path counted, minimize not undoing close (two ways), a reused process number, a refused close of a followed child, Steam's own children vs a game it started, the member list not in a backup. New `tests/test_app_limit_enforcement.py` (51 tests). It runs the real service pass (`enforce_once` + `enforce_apps`) and the real tray tracker on one database file, with only Windows faked and a controllable trusted clock. It covers allowed hours, blocked hours, daily and weekly limits counted by the tracker, a 04:00 limit day, group hours and a shared group limit, the Steam Shipping exe (Adam's case: 16-18 plus 15 minutes, blocked after 15 minutes), a child from the app's folder, the target written in any case or as a path, every Mode, a disabled item, an emergency unlock ending, a rule edited from the window or the raw file, slow ticks, no tray app, and a refused close. 18 of them fail on 0.84.0. New `tests/test_segmented.py` checks that the switch's font is in pixels and scaled once, its height and room per label, where clicks land (no dead strips) and the text colour on a light accent.

## 0.84.0 - 2026-10-02 00:05
- **The installer now carries uiautomation's two helper DLLs.** PyInstaller never picked up `UIAutomationClient_VC140_X64.dll` / `_X86.dll` from the uiautomation package, so they were missing from every build (it warned and carried on). `installer/lockdown.spec` now adds them with `collect_data_files`.
- **The Dashboard and Screen Time load much faster on an old install, and stay fast.** On a year-old database (200 000 minute rows, 70 000 switches, 6 000 blocked items), after the first daily roll-up, the Dashboard's data work went from 598 ms to about 65 ms, "streaks" from 342 ms to 10 ms, the weekly summary from 399 ms to 21 ms, Screen Time's 30-day view from 295 ms to about 80 ms, a calendar month from 21 ms to 0.2 ms, the 5-second watcher tick from 115 ms to about 30 ms and the 3-second status poll from 70 ms to nothing (`scripts/bench_stats.py`, medians, before = 0.83.4). Causes (design/rebuild/inventory-perf.md #3-#4): the database had no index at all, so every switch count and blocked-visit lookup scanned the whole table; "streaks" pulled up to a year of per-minute rows into Python on every refresh; the average of switches ran 14 queries; "time saved" compared every blocked attempt with every visit of the last 30 days; the status poll loaded every blocked item (with all its rules) every 3 s only to count them, and the usage tracker and the watcher loaded them every 2 and 5 s; and the tables only ever grew. Fixes: indexes on every hot query (switches and blocked visits by time, limit usage by day, reminder answers, rules / group members by item); per-day totals summed in SQL; one grouped query for the switch average and one for all reminders' counts; "time saved" indexes the visit lengths once by site and domain suffix; Dashboard's "coming up" is worked out once per refresh instead of twice; Screen Time works out its per-minute summary once (not once for sessions and again for longest focus) and cuts the heatmap's week out of the rows it already has; the status poll counts with `COUNT(*)`; the item list is loaded again only when a counter - bumped by database triggers whenever any program changes an item or rule - says it changed (each caller still gets its own copy); timestamps are parsed with `fromisoformat` (about 20x faster than `strptime`). Every number is the same as before - checked against the old code on random data (`tests/test_stats_rewrite.py`); the index use is checked on what the real functions run, not on copies of their SQL (`test_the_real_code_paths_never_scan_a_growing_table`, `test_list_items_is_reused_until_any_connection_changes_items_or_rules`, `test_list_items_hands_out_copies`, `test_status_poll_counts_items_without_loading_them`).
- **Screen-time detail is kept for a month; daily totals forever.** As agreed: per-minute screen time, the switch log, blocked visits and reminder answers older than 32 days (the 30 you approved, plus the two days "time saved" and a late limit reset reach back) are added to daily totals and then deleted, once a day, on a background thread of the tray app. Both happen in one transaction per day, so a day is never counted twice or lost, and charts, streaks, trends, averages, the calendar, the weekly summary and the CSV export show exactly what they showed before (`tests/test_retention.py` builds 60 days of minute data and compares every statistic before and after). Reads that combine the detail with the daily totals are each one statement, so the daily roll-up can't make a day count twice or vanish while a page is reading it (`test_detail_plus_roll_up_reads_are_one_statement`). **Nothing enforcement needs is touched:** limit usage for every day / week / month and every opening count, emergency unlocks and their count per period, the trusted clock, notice cooldowns, the challenge settings and all rules (including temporary ones) stay exactly as they are (`test_limits_emergency_unlocks_and_protection_state_are_untouched`, on the first and last day of a month too). Retention can't make any limit or block weaker. A year-old database is rolled up in under a second.
- **A wrong clock can't delete recent detail.** Found in review: if the service was stopped (so the clock offset was stale) and the Windows clock moved forward, or the service started offline with a wrong hardware clock, retention took the wrong date as "today" and permanently rolled up the real last month - Today / 7 / 30-day views empty, the switch average 0, "time saved" wrong. The cut-off now also needs 33 days that really have recorded data after it, so a clock jump rolls up nothing, and real detail only goes once as many days of use have actually followed (`test_a_clock_jumped_forward_keeps_recent_detail`, `test_normal_daily_use_still_rolls_up_at_the_cut_off`, `test_too_few_days_of_data_rolls_up_nothing`). Also: one stray row dated 1970 no longer makes the first pass walk 20 000 empty days (it jumps to the next day with data, `test_one_stray_old_row_is_not_thousands_of_transactions`); each pass ends with `PRAGMA optimize`, so the query statistics follow the much smaller tables (`test_retention_refreshes_the_query_statistics`); and a backup no longer carries the date retention last ran, which made every import report "1 other setting will change" (`test_a_backup_does_not_carry_when_retention_last_ran`).
- **No more 10-second window freeze when the database is busy.** The window's connection now waits at most 1.5 s for a lock (the service or the tracker writing) instead of 10 s. Only display settings (theme, size, which Dashboard cards / Screen Time tab, the last notice time, update-check dates) may wait in memory for a busy database; they are read back from there at once and written by a background thread as soon as the lock is free - and before the app exits or restarts. Everything that matters for blocking - switching Lockdown back on, the challenge settings, modes, protection lists, blocked words, limits, reminders, "the tray app is running again" - is written before the call returns, retried for up to 10 s like any other edit, so it can't be lost if the app is killed and can't land late on top of a newer change (found in review). A waiting display setting is written only if nobody changed it meanwhile (compare-and-set), the background writer retries any database error instead of giving up, and a last synchronous attempt is made on exit (`test_ui_connection_does_not_freeze_on_a_lock_and_loses_no_setting`, `test_enforcement_settings_are_never_deferred`, `test_a_waiting_setting_does_not_overwrite_a_newer_value_from_another_connection`, `test_writer_retries_any_database_error_and_loses_nothing`, `test_flush_writes_what_a_dead_writer_left`, `test_ui_structural_write_waits_for_the_lock_instead_of_failing`).
- **Fewer repeated parses and loads.** Settings are read straight from the database (a cache checked with `data_version` turned out to cost as much as the read it saved, and could go stale when one connection was shared by threads - both found in review; `test_settings_read_is_one_statement`, `test_one_connection_used_from_several_threads_never_reads_a_stale_setting`). What was slow was parsing: the trusted-clock offset, the limit clock, your protection lists' names (for blocked-visit notices - the list settings can hold thousands of sites) and the blocked-words settings (read by the word check every 0.5 s) are parsed once per saved value (`test_word_check_parses_its_settings_once_per_save`). The 5-second watcher loads items, groups and limit usage once per tick instead of twice. Opening the database no longer re-runs about 20 migration queries every time: they run once per schema version (`PRAGMA user_version`), followed by a one-time `ANALYZE` for the new indexes (`test_migrations_run_once_then_open_is_cheap`); a schema change without a version bump fails a test (`test_schema_changes_come_with_a_schema_version_bump`).
- **Fixed: a possible crash or hang after "Check for updates", during an update download and after the daily update check.** Those ran on worker threads and called Tk (`after(...)`) from there - Tcl from the wrong thread, which can crash or hang the window; the download did it once per chunk. Workers now hand their results to a queue that the window empties on its own thread, and progress updates are merged so only the newest reaches the bar (`tests/test_ui_threads.py`). The tray icon is touched only when its status, colour or menu actually changed - not three cross-thread calls every 3 seconds - and its menu is rebuilt on the tray's own thread just before it opens, never destroyed from the window's thread while it is showing (`test_tray_menu_is_rebuilt_on_pystrays_thread_only_when_shown`).
- **Fixed: a page could stop updating for the rest of the session after one error.** The Anti-Bypass lock banner, a mode's lock / stop state, the clock, Dashboard / Screen Time / Blocking / Network Log auto-refresh and the tray's Open / Exit / mode clicks ran in loops that ended for good if one round raised; they now always schedule the next round (`test_refresh_loops_reschedule_even_after_an_error`). Changing the theme or accent no longer can leave Lockdown not running for a minute (the new copy was launched before the old one had saved and quit, `test_restart_flushes_before_launching_the_new_copy`). The new web UI's bridge gives each of its worker threads its own database connection (`test_each_pywebview_thread_gets_its_own_connection`).
- **No more periodic stall from garbage collection.** The window ran a full garbage collection every 2 seconds, which took longer the more pages had been opened. Automatic collection stays off in the window process for the documented reason (0.4.0: it can free a Tk object on another thread, which hangs Tk), but the Tk thread now collects the way Python normally would - the young generation when it fills up, an older one now and then - and everything alive once all pages are built is frozen (`gc.freeze()`) so it is not walked every time. The full sweep every 5 minutes unfreezes first, so a startup object that later becomes garbage is still freed (`test_full_collection_frees_startup_objects_that_became_garbage`).
- **Faster start, and the watchdog can't be switched off quietly.** The autostart entry and the "Lockdown Agent Watchdog" task are registered on a background thread, so `schtasks` no longer delays the window. They are still re-written on every launch: a review caught that writing them "only when missing" would have let a disabled or edited watchdog task (`schtasks /Change /DISABLE` needs no admin) stay off for good - it is now re-created and re-enabled every time, as before 0.84 (`test_watchdog_task_is_rewritten_on_every_launch`). A failure is written to the log (`test_autostart_failure_is_logged`).
- Fixed: on the very first start the service and the tray app could both create the database at the same moment, and one of them failed with "database is locked" (switching a new file to WAL ignores SQLite's busy timeout). It now waits (`test_two_processes_opening_a_fresh_database_together`).
- Developer: `scripts/bench_stats.py` builds a large synthetic database (a year of minutes, 10 000 network rows, 6 000 blocked items) and times the Dashboard / Screen Time / watcher / status-poll work; `--src` points it at another checkout for before / after numbers. Shared builder: `tests/synthdata.py` (no longer fails when two blocked visits share a second). New tests: `test_retention.py`, `test_stats_rewrite.py`, `test_db_speed.py`, `test_ui_threads.py`.

## 0.83.4 - 2026-10-01 22:56
- **The tests now run on Linux too (developer-only; the app is unchanged).** Several modules call the Windows API the moment they are imported (`ctypes.WinDLL("kernel32")`, `winreg`, pywin32, COM/UI Automation, pystray, customtkinter, pywebview), so off Windows the suite could not even be collected. New `tests/conftest.py` installs inert fakes for exactly those - only when not on Windows; on Windows it does nothing and the real modules are used. A fake Win32 call returns 0 and the fake registry is empty (a missing value raises `FileNotFoundError`, as Windows does). The five tests that read the real process list, TCP table or DNS adapter API are now marked "needs Windows" with the reason (`test_list_processes_real`, `test_start_time_real`, `test_tcp_table_readable`, `test_a_local_server_stays_reachable_while_a_block_is_on`, `test_set_dns_signature_is_accepted`); no logic test is skipped. Pinned by `tests/test_conftest_fakes.py`. Also silenced an invalid-escape warning in a `tests/test_phase8.py` docstring.

## 0.83.3 - 2026-09-29 22:47
- **Opening the window is instant again (redesign Phase 0).** Clicking the icon while the tray agent is already running no longer imports the whole GUI (customtkinter, PIL, COM, every page) before checking that an instance is already running — that import happens only for the instance that actually shows the UI. The "wait ~10 s for the window" on a normal click is gone.
- **No more freeze when dismissing the bedtime / break overlay.** The full-screen cover is now torn down *before* its answer runs (e.g. "Disable alerts", which opens the Anti-Bypass challenge). Previously the challenge appeared behind the still-up cover and the app looked frozen. The challenge window is also forced on top. Covered by `tests/test_overlay_teardown.py`.
- Added `pywebview` to requirements (for the in-progress web-UI redesign; see design/REDESIGN.md §11).

## 0.83.2 - 2026-09-27 17:25
- **Fewer repeat "site blocked" notifications.** The per-site cooldown is now remembered across restarts, so a restart no longer forgets it and re-notifies at once on the next background connection (browsers and other apps quietly hit youtube.com / googlevideo.com even when you're not on YouTube). The default cooldown is also raised from 5 to 30 minutes.

## 0.83.1 - 2026-09-27 15:44
- **Corner notifications position correctly on scaled displays.** On a display above 100% scaling the pop-ups were rendered larger than they were placed, so they spilled off the right edge and sat over the taskbar. They now sit just above the taskbar (using the desktop work area) and account for the scaling, so they're never cut off.

## 0.83.0 - 2026-09-27 06:26
- **Mark an alert "important" and you can't turn it off without the challenge.** Each reminder - sleep, breaks, and your own - now has an **Important** switch. When it's on, that alert can't be turned off, un-flagged, or deleted in the settings without the Anti-Bypass challenge - so you can't just disable an annoying reminder to escape it. Dismissing the popup itself stays free; it's the settings that are locked. Off by default.

## 0.82.0 - 2026-09-27 06:08
- **The bedtime alerts can now be tuned in the app.** The Sleep card has an escalation editor: steps of "every N min, after HH:MM" that you can add and remove. The later it gets, the more often the "Time for bed" screen comes back after you dismiss it (default: every 15 min from 21:00, every 5 from midnight, every 1 from 3 a.m.). The escalation itself has worked since 0.81.0; this adds the controls for it and replaces the old single "Repeat every" field.

## 0.81.2 - 2026-09-27 05:14
- **Reminders and Modes open faster.** Their editor forms - a couple hundred widgets each - are now built the first time you open one, not every time the page loads. The pages come up with far fewer widgets (Reminders 426->259, Modes 392->216), so they render noticeably quicker; opening an editor costs about a second, once, then it's reused. Nothing was removed - everything still works.

## 0.81.1 - 2026-09-27 04:36
- **The window opens maximized and can't be shrunk.** Lockdown now starts full-size and its size is locked (it keeps its title bar, so minimise and close-to-tray still work); reopening from the tray comes back full-size too.

## 0.81.0 - 2026-09-26 20:24
- **The bedtime screen gets more insistent the later it is.** Instead of one fixed repeat, the sleep reminder
  now has editable escalation tiers: by default it comes back 15 minutes after you dismiss it from 21:00,
  every 5 minutes after midnight, and every single minute after 03:00 - so going to bed is easier than
  fending it off. The tiers are fully customizable (the visual editor lands with the new UI; the defaults work
  now).
- **The bedtime screen's buttons are Dismiss and Disable alerts.** Dismiss delays it by the current tier's
  interval. **Disable alerts** is the only way to actually stop it, and it needs the Anti-Bypass challenge -
  pass it and you can turn it off for the night or snooze your own amount. Cancelling the challenge just
  dismisses it, so it always comes back on the escalation - never a free way out.
- **Verified: the challenge-gated uninstall.** Removing Lockdown from Apps & Features runs the challenge first
  and refuses unless it passes (this was already wired; confirmed end to end, no change needed).

## 0.80.0 - 2026-09-25 21:22
- **Fewer interruptions.** On a normal day a break every 45 minutes, "hydrate" every 45 and pull-ups every 60
  came to about forty popups - most of them waved away (the break: taken 0 times out of 12). Three changes:
  - **A pace.** At most one interruption every 20 minutes. Whatever falls due in between waits, and arrives
    with the next one as a single popup - not three in a row. Something already on screen simply takes the
    new one in. A snooze is not held back (you asked for it at that time), and neither is a strict break
    that has run out of snoozes (the pace must not become a way to put it off).
  - **Back-off.** Wave the same reminder away three times running and it asks half as often for the rest of
    the day, and says so once. Doing it starts the count again.
  - **Folded into the break.** Reminders due around a break ride along in it - "Time for a break ... While
    you're up: drink some water" - one popup instead of two. Taking the break records them as "with break"
    (not done - that is yours to say); dismissing it dismisses them too.
- **Buttons only where they mean something.** Nearly every notice carried "Open Lockdown" and "Mute 1 h" -
  a door to the app in general, and a way to silence something that fades by itself anyway. They are gone.
  The weekly summary now has **See the week** (opens Screen Time) and a new version has **Install**, which
  downloads and installs it; everything else is simply clicked away.
- **Limits say which "today" they mean.** Screen Time counts from midnight; your limits reset at 03:00. So
  two hours of play since midnight read as 28 minutes at 03:30, with nothing to say why. A limit whose day
  does not start at midnight now says so: "0m / 2h 00m since 03:00", or "since Thu 03:00" just after
  midnight.
- **You can't miss that Lockdown is off.** Switched off from the Anti-Bypass page, it now also turns the tray
  icon grey ("OFF - nothing is enforced") and puts a banner across the top of the Dashboard with a **Turn back
  on** button. The Dashboard's "Blocked now" stops counting things as blocked while nothing acts on them.
- Found while building the pace, and fixed: a reminder whose interval kept running while it waited could come
  due again the moment it was shown and open a second popup beside the first; a reminder queued by the pace
  could come up after its own hours had ended or its daily limit was reached; and a break held back by a game
  would have been announced again on every tick.

## 0.79.3 - 2026-09-25 21:00
- **Disabling a group no longer stops its clock.** A disabled group got no rules at all, so its limit counted
  nothing and read "0m / 2h 00m today" after 155 minutes of osu!, YouTube and Twitch. It was also a way round
  the limit: disabling needs the challenge but enabling does not, so disable it, use it for hours, switch it
  back on, and the limit started again from zero. Time and openings are now counted against every rule,
  enforced right now or not; disabling stops the blocking only. The same goes for a disabled item's own limit.

## 0.79.2 - 2026-09-25 02:41
- **The Windows app identity is now `com.husarp.lockdown`** (it was `Lockdown.App`), the same reverse-DNS
  shape these projects use everywhere else. It is what Windows hangs Lockdown's name and icon on for
  notifications and the taskbar, and what the app matches on to clear its own toasts and nobody else's.
  A test keeps the two copies of it - the one that registers it and the one that clears by it - in step.

## 0.79.1 - 2026-09-24 05:52
- **MIT licence.** The repository is public; now anyone may also use, change and share Lockdown, as
  long as the copyright notice stays with it. `LICENSE` added, and a *Licence* section in README. The
  bundled fonts keep their own licence (SIL OFL). No change to the program itself.

## 0.79.0 - 2026-09-23 01:46
- **Turn Lockdown off entirely.** A new card on the Anti-Bypass page switches the whole thing off: no site or
  app blocking, no time limits, no protection lists, no bedtime, breaks or reminders - as close to not having
  it installed as it can be while still being there to switch back on. It stays off until you turn it back on;
  nothing brings it back by itself.
- **Switching off goes through the challenge, switching back on never does.** Turning it off is the biggest
  loosening there is, so it asks for the typed phrase (and the wait, and the hours, if you set them) like
  anything else that weakens a block. Coming back to your own rules is never worth standing in the way of.
- **Off really means off, not paused.** The enforcer carries on running with an empty list of blocks rather
  than skipping its work, so the hosts entries, firewall rules and app blocks are taken back down by the same
  code that put them up. Stopping halfway would have left whatever was in place at that moment stuck there.
- Turning it off is not the same as quitting from the tray: Lockdown still starts with Windows, so the button
  that turns it back on is always reachable.

## 0.78.2 - 2026-09-23 01:38
- **Blocking a site was cutting every local connection on the machine.** The log repeated "Closed 10 open
  connections to blocked sites" every two seconds forever, and anything using a loopback socket broke - a
  Gradle build died with "client disconnection detected, canceling the build", and a local server on
  127.0.0.1 could not be reached from the same PC. The hosts file points blocked domains at 127.0.0.1, and
  Windows loads the hosts file into its DNS cache; the part that matches open connections to names reads that
  cache back, so every blocked name answered "127.0.0.1" and loopback went onto the kill list.
- **A connection to a loopback or unspecified address is now never closed**, whatever the addresses handed in
  say - a hard filter in the sweep itself, not only where the list is built. The hosts entry already stops the
  real traffic, so cutting a local socket achieves nothing even when the address really is blocked. An address
  that cannot be parsed is left alone too.
- **Blocked domains are resolved by asking the network's own DNS server** rather than the system resolver, so
  the hosts file Lockdown writes can no longer feed its own answers back into the killer. The system resolver
  is used only if the network has no usable DNS server.
- **The log says what it closed** - every remote address and port, not just a count. The old message gave no
  way to tell what was being killed.

## 0.78.1 - 2026-09-22 18:27
- **Time was silently stopping being counted.** A 2-hour group limit read 39 minutes after a day of use, and
  the reason was not the counting: the usage tracker had been dead since 14:04 and nothing noticed. Only its
  tick was guarded, so anything that failed while it was *starting* - the COM initializer, or opening
  config.db while the service was restarting and holding it - killed the thread on the spot, logged nothing
  at all (the logging lived inside the loop it never reached), and nothing ever started it again. The app
  stayed up and blocking carried on, because the service does that, so everything looked fine while 4 hours
  20 minutes of use went unrecorded. The tracker now writes down every failure and starts itself again after
  30 seconds, and remembers when it last counted, so this cannot happen quietly again.
- Time already lost this way cannot be recovered: the screen-time log is written by the same tick, so those
  hours are missing from both.

## 0.78.0 - 2026-09-22 14:02
- **Updating without leaving Lockdown.** About used to find a new version and then send you to a web page to
  fetch it yourself. Now it downloads the installer itself, with a progress bar, and starts it - Lockdown
  closes so its own files can be replaced, and Windows asks for permission the way it does for any installer.
  **Open the GitHub page** is still there if you would rather do it by hand.
- **It looks by itself, once a day.** Two switches on the About page, both on: *Check for updates
  automatically (once a day)* and *Tell me when a new version is found*. The check is one request to GitHub
  with nothing about you in it, it runs on its own thread so the window never waits for the network, and you
  are told once per version rather than once a day forever. Turning the notice off leaves the check running,
  so About still shows what it found.
- **It never installs an older version.** Lockdown is a blocker, and "update" to an earlier build would be a
  way to drop the rules you set - so the button only ever goes forwards.

## 0.77.0 - 2026-09-22 13:26
- **Every reminder has a way out that isn't a lie.** A small dark **X** now sits beside Done and Snooze.
  Until now a reminder that had used up its snoozes (or was set to none) could only be cleared with **Done** -
  which counts towards the daily limit and starts the "did you actually do it?" check. So the only way to get a
  popup off your screen was to claim you had done something you hadn't. The X closes it, counts nothing, and
  the reminder comes back at its normal time: an "every 45 min of use" one after another 45 minutes, an
  "at 09:00" one tomorrow. It is written to the log as **dismissed**, so your statistics stay honest.
- **Gentle break prompts can be skipped the same way.** Strict breaks cannot - they exist to be hard to wave
  away, and a one-click exit would be a bypass with no friction. The bedtime screen is unchanged for the same
  reason.
- **The build now stamps what it made.** A successful build writes `build\BUILT.json` with the version and the
  time, so a dashboard can tell *which* version the files in `build\` came from rather than only when they
  were made. It is written last, after the installer exists, so a failed build never leaves a stamp behind.

## 0.76.1 - 2026-09-22 12:17
- **Moved to a new GitHub account.** The repository is now `Husarp/lockdown`, and the About page's
  **Check for updates** and **Open the project page** follow it there. Every commit is attributed to a GitHub
  noreply address rather than a real one, so the history carries no personal e-mail.

## 0.76.0 - 2026-09-22 11:47
- **The buttons fit what the reminder says.** "20-20-20: look 20 feet away" was offering *Open Lockdown* and
  *Mute 1 h* - one has nothing to open, the other isn't an answer to it. Reminders shown as a notice now carry
  no buttons at all: read it, click it away. The blocked-site notice keeps both, where they make sense.
- **Every reminder can say which days and hours it may appear in.** The days used to apply only to "at set
  times"; they now apply to all three kinds, and "every N minutes of use" has its own **Only between 08:00
  and 20:00** - so nothing asks you for push-ups at 3 a.m. An overnight window (22:00-02:00) works.
- **A daily limit counted in what you did, not what you saw.** "Stop for the day after 5 times DONE": drink
  water five times and it leaves you alone until tomorrow. Snoozing or ignoring it doesn't count towards the
  five, which is the point - the limit is on the doing, not on the asking.
- **Two reminders due at once interrupt you once.** One already on screen takes the other in with it: one
  popup, both lines, one Done that answers both. Rather than being stopped twice a minute apart for two things
  you'd have done in one go.
- **What you are using now gets through the quiet.** A warning that the app in front of you is about to be
  blocked - or the notice that it just was - is shown even while a muting mode, Windows Do not disturb or
  "Mute 1 h" is holding everything else back. That notice is directly about what you are doing; the rest can
  wait.

## 0.75.1 - 2026-09-22 11:07
- **About knows where Lockdown lives**: github.com/G4dam/lockdown. **Check for updates** and **Open the
  GitHub page** are on the About page now - the check asks GitHub for the newest release and says whether it
  is newer than the version you are running.

## 0.75.0 - 2026-09-22 01:00
- **Reminders respect Windows' Do not disturb.** Lockdown never asked Windows about it, so the "Time for bed"
  screen came up over a film with everything else silenced. It now waits - and appears as soon as you turn
  Do not disturb off, as long as it is still night. So it can't be used to skip bedtime by switching quiet on
  at five to nine, and it isn't lost either.
  - The same goes for break and 20-20-20 reminders, and for a mode of your own that mutes notifications.
  - Presentation mode counts as Do not disturb too (Windows reports them the same way).
  - A full-screen game still gets the bedtime screen over it - that is the one thing it is for.

## 0.74.2 - 2026-09-21 21:40
- **Polish letters: the other half of the path.** An AltGr key reaches Tk in one of two ways - as a Control
  key (Tk swallows it, so Lockdown types it in) or as an ordinary one (Tk types it in itself). Every fix so
  far only touched the first; if this Tk build takes the second road, nothing we did to the first could ever
  have helped. Now the letter is corrected after any keypress, whichever of the two put it there, and does
  nothing when the right letter is already in the box.
- **scripts/keycheck.py**: run it, type the letters that come out wrong, and it writes exactly what Tk reports
  for each key (keysym, character, modifiers, active layout) to build/keycheck.log - so this stops being a
  matter of guessing.

## 0.74.1 - 2026-09-21 21:24
- **Lockdown's own popups no longer cover each other.** A blocked-site notice and a reminder (or two
  reminders - they are separate windows, one per reminder, so several can be waiting at once) all put
  themselves in the bottom-right corner and landed on top of one another. They are one stack now: the newest
  sits in the corner, the ones before it are pushed up with a gap, and when one goes the rest slide back down.
  If they ever filled the screen the topmost stays where it is rather than sliding off it.

## 0.74.0 - 2026-09-21 21:15
- **Polish letters, properly this time.** 0.70.1 asked Windows for the *system* codepage; on an English
  Windows that is cp1252, so the conversion did nothing and the letters still arrived wrong. What matters is
  the codepage of the **keyboard layout you are typing with** - the Polish one is cp1250 even on an English
  system. That is what is used now, read at each keypress, so switching layouts switches with you.
- **Text saved before this is put back, once.** The bedtime message, reminder texts, item and group names and
  the Anti-Bypass phrase are read back through that codepage the first time Lockdown starts - but only when
  they carry a character that practically cannot be typed on purpose (a superscript 3, an oe ligature, an
  inverted question mark), which is what makes the mix-up recognisable. Anything else is left alone.
- **Resizing the window is instant again.** 0.73.0 re-scaled every widget whenever the window settled, and
  customtkinter does that by walking every widget it has ever made - about 8 seconds once every page is
  built. That is gone. The size is decided **once, at startup**, from what your screen can show, and
  **Settings > Interface size** (Auto / 100% / 90% / 80% / 70%) overrides it from the next start.
- **"On the allowance" instead of "Allowed now".** While the hours are blocking and you are spending the
  minutes they allow, it says so, with what is left - that is why something can look allowed at 21:30 when
  its block starts at 21:00.
- **A rule that can no longer be used goes red.** While anything is blocking a site, app or group, its other
  rules turn red with it: a shared limit saying "34m of 2h today" in green was offering time you cannot spend.
- **The clock** is at the foot of the sidebar - Lockdown's own trusted time, the one the blocks go by.

## 0.73.0 - 2026-09-21 18:51
- **A window that isn't maximised now shrinks everything to fit** instead of cutting cards off at the edge.
  The pages are laid out for 1280x780 of their own units; a smaller window draws every one of those units
  smaller, down to 62%, and you can see at a glance that the window wants to be bigger. It re-scales a quarter
  of a second after you stop dragging, not during - laying every widget out again is the expensive part.
  - This is what was cutting "System" off the theme picker and "Custom..." off the accent row: at 150% Windows
    scaling every control is half as big again, and the card ran out of room.
  - The minimum window size is smaller now (560x420), since the contents follow it down.
- **The option switches are tighter**: 6px either side of a label instead of 8, 1px between chips, 22px tall.
  They also re-measure themselves when the scale changes, being drawn by hand rather than out of CTk buttons.
- **A new About page** (last in the sidebar):
  - the version and the date it was released;
  - **Check for updates** and **Open the GitHub page** - both appear once a repository is set in version.py.
    The check asks GitHub for the latest release and says whether it is newer. Nothing is sent but the
    request, and only when you press the button;
  - **What Lockdown can do** - every feature in one list, as a tour of the app;
  - where your data, the program and the log are, with a button to open the folders.

## 0.72.2 - 2026-09-21 12:48
- **A disabled member says so in its group's box**: "● Disabled" on its line, and its blockers are not listed,
  since none of them apply. Before, it sat there looking like an ordinary allowed member.
- **What is left of the allowance is in brackets after it**: "+ 15 min allowed during blocked hours
  (15m left until 05:00)", or "(used up until 05:00)" once it is gone.

## 0.72.1 - 2026-09-21 12:10
- **The allowance during blocked hours is its own purple chip.** The hours themselves are red while they are
  on - they are blocking, and the allowance is the way out of them, not a reprieve - and next to them a purple
  "+ 15 min allowed during blocked hours · 15m left until 05:00" says how much of that way out is left.
  It reads as what it is: time to use if you have to, rather than time you are meant to spend.

## 0.72.0 - 2026-09-21 11:49
- **A group is one box on Blocking > Overview.** Its rules are written once at the top, its members sit
  underneath - instead of the group's rules being copied onto every member's row.
  - The header has the group's name, how many members it has, how it stands ("2 of 3 blocked now") and
    **Edit / Disable / Remove**. Edit opens it in Groups, where per-member versions of its rules live.
  - A member is one line: what it is, how it stands, and only what it has **on top of** the group - its own
    blockers, or "its own by time in this group" when you customised the group's rule for it. Click the line
    to open its own blockers.
  - This is also the answer to "shared with what?": a shared limit now sits above the list of members sharing it.
  - Anything that is in no group stays a row of its own below, exactly as before.
  - **Sorting sorts the boxes** along with the rows: a group counts as blocked as soon as one member is, and
    its next block is the soonest of its members'.
- **A rule's colour says what it is doing, not what kind it is.** Red while it is blocking right now, orange
  while it is about to (90% of a limit or an allowance used, or its hours starting within 10 minutes), green
  while it is not. The colours update in place every 2 seconds, as the countdowns do.
- The per-item **Alerts** setting moved from the list to the item's own editor (Blocking > Edit), since
  members have no controls of their own.

## 0.71.0 - 2026-09-21 10:42
- **Your games are in Games now.** 0.64.0 added the category, but nothing moved into it: every Steam game had
  already been written down as Distracting (that is what Lockdown did before Games existed), and a category
  that is already set is never overwritten.
  - **Steam games move to Games** - the ones Lockdown itself had marked Distracting and you never changed.
    One you put somewhere else yourself stays where you put it.
  - **The starter list knows the difference too**: CS2, Dota 2, GTA V, Rocket League, Overwatch, Genshin,
    Apex, Rainbow Six, Valorant, Fortnite, Roblox, Minecraft, League, osu! and the browser-game sites (poki,
    crazygames, miniclip, y8, friv, roblox.com) are Games; **stores, launchers and streaming stay
    Distracting** - Steam, Epic, Battle.net, EA, Ubisoft, GOG, Netflix, Twitch and so on. They are not the game.
- **No more second "Games" in the menu.** A category of your own made before the built-in one has the same key,
  so it was the same category listed twice. It appears once now, and keeps the colour you gave it.

## 0.70.1 - 2026-09-21 10:37
- **The Polish letter that now reaches the box is the right one.** 0.65.0 got AltGr keys through Tk's
  "Control + a key does nothing" rule, but the wrong letter arrived: "pamietaj" came out as "pami<e-circumflex>taj".
  Tk hands the character over as one byte in the keyboard's codepage (cp1250 here) and tkinter reads that byte
  as Western European (cp1252), so e-ogonek became a circumflex e, z-dot an inverted question mark, l-stroke a
  superscript 3 and s-acute an oe ligature. The byte is now read back through the codepage Windows actually
  uses. Every box in the app is covered - it is one rule on the Entry and Text classes.
- A Western (cp1252) Windows is untouched, and a character that can't have come from this mix-up is left alone.

## 0.70.0 - 2026-09-21 10:26
- **A wait between passing the challenge and being able to change anything** (Anti-Bypass > "Then wait ...
  before it actually unlocks"). Type any time - `10`, `1h`, `45s`, or `Off` for none, as with the reminders.
  - Passing the challenge starts the wait instead of unlocking: nothing changes yet, and the window says when
    it opens ("possible from 10:36 - about 10 min from now - and then for 5 minutes").
  - Trying again while the wait runs shows the countdown, **not the phrase again** - typing it a second time
    would only start the wait over.
  - The Anti-Bypass banner counts it down, with a bar that fills.
  - It is a time kept in the database, so quitting Lockdown, killing the tray agent or restarting the PC
    doesn't skip it. Shortening or removing the wait is a weakening change, so it needs the challenge itself.
  - An easy phrase plus a long wait is often the stronger setting: impatience beats difficulty.

## 0.69.0 - 2026-09-21 10:10
- **Moving the limit reset earlier now asks which way you want it.** A small window offers both:
  - **Start now** - the limit day you are in ends at once and a fresh one begins, so today's limits start
    over. This is the one that needs the Anti-Bypass challenge.
  - **From <when the day ends>** - the new time takes over when the running day ends and nothing starts over.
    No challenge, because nothing comes back early.
  Moving it later still applies straight away without asking - a longer day never gives anything back.
- A held week or month no longer ends early either, even while its key is unchanged (starting a fresh day
  doesn't drag the week's end back with it).

## 0.68.0 - 2026-09-21 09:57
- **Changing when limits reset works properly again.** Changing it twice stacked: each change stretched the
  day that was running, so a day could become 45 hours long and end at a time that had nothing to do with the
  reset time. That is what left "resets at 03:00" showing "current limit day ends 00:00" for days.
  - **An earlier time no longer stretches the day at all.** 03:00 -> 00:00 leaves the running day ending at
    03:00 and applies 00:00 from then on, so the day *after* it is the shorter one. It used to push the
    running day out by another whole day.
  - **A later time stretches the running day once**, to the new time (a day of at most 27-28 h), and a second
    change can't stretch it again: a running day never lasts more than two days from its start.
  - **Moving the reset earlier now asks for the Anti-Bypass challenge**, because one day then ends sooner than
    it would have. Moving it later is free.
  - **A day stretched twice by an older version is repaired** when the clock is read, which puts your limit
    day back on the real boundary.

## 0.67.0 - 2026-09-21 08:56
- **A blocked video no longer keeps downloading to the end.** The page and the video are different domains:
  youtube.com serves the page, but the video itself comes from a random host under **googlevideo.com**, which
  was never blocked - so an open player finished the video however long the block had been on.
  - **Known sites now carry the domain their media comes from**: YouTube + googlevideo.com, Twitch + ttvnw.net,
    Netflix + nflxvideo.net, TikTok + tiktokcdn.com / tiktokv.com, Instagram + cdninstagram.com, Facebook +
    fbcdn.net. Sites you blocked earlier get them once, automatically; take one off yourself and it stays off.
  - **Everything under a blocked name is blocked too.** The hosts file has no wildcards, so this is done by
    Lockdown's own DNS filter, which until now only answered for the protection lists. googlevideo.com
    therefore covers rr1---sn-u2oxu-f5fed.googlevideo.com.
  - **Connections already open to a blocked name are cut**, by the name the browser actually looked up (from
    the Windows DNS cache), not only by the addresses resolved when the block began. This is what stops a
    transfer that is already running. IPv4 only - Windows has no way to close an IPv6 connection.

## 0.66.0 - 2026-09-21 07:42
- **A website now has its own "When blocked" options**, the way an app does. Pick a site on Blocking > Add
  (or Edit one) and tick any of:
  - **Can't load it** - the address goes nowhere, in every browser. This is what Lockdown has always done, and
    it stays ticked by default.
  - **Close the tab** - if you open it anyway, the tab is closed (a browser with a single tab gets a fresh tab
    first, so the window doesn't close).
  - **Go back** - the browser goes back instead; if that doesn't leave the page, the tab is closed.
  Close the tab and Go back exclude each other; the tray agent does them twice a second, the same check the
  bad-word list uses. A site that is only set to close the tab is deliberately left out of the hosts file.
- The Blocking list says how each site is blocked, next to its address: "youtube.com · can't load + closes the
  tab" - it only said this for apps before.
- Unticking an option is a weaker block, so Anti-Bypass asks for the challenge, as it does for apps.

## 0.65.0 - 2026-09-21 02:14
- **Polish letters can be typed again - everywhere in the app.** Windows sends AltGr as Ctrl+Alt, and Tk's own
  "Control + a key does nothing" rule swallowed the keypress, so a, c, e, l, n, o, s, z with their accents
  never reached any box (you hit it on the Anti-Bypass phrase, but it was every box: names, groups, reminders,
  search). They are typed in now, and real shortcuts (Ctrl+C / Ctrl+V / Ctrl+A / Ctrl+Z) are untouched -
  including the no-pasting rule in the challenge.
- **Every reminder can say what you want it to.** Sleep has "Heads-up says" and "Bedtime says", Breaks has
  "It says", and 20-20-20 has its own. Leave a box empty and it says what it always said. `{bedtime}`,
  `{wake}`, `{time}` (sleep) and `{every}`, `{length}` (breaks) are filled in; anything else you type is left
  alone rather than breaking the reminder.
- **The built .exe files carry their version.** Explorer's Details tab (and the tooltip, and the UAC prompt)
  shows it for LockdownSetup.exe, Lockdown.exe and LockdownService.exe, so you can tell two downloaded setups
  apart without running them. The build prints it too.

## 0.64.0 - 2026-09-21 01:56
- **Games is a category now**, next to Productive / Neutral / Distracting - purple, and available everywhere a
  category is (the right-click menu, Screen Time, the timeline, and as a block target of its own).
  - **Installed Steam games go into it by themselves.** The first time the app list is built, every Steam game
    that you have not already put somewhere else is tagged Games. Anything you set by hand is left alone.
  - Non-Steam games are not detected - right-click one and pick Games.
- **The bedtime reminder takes any time you type**, instead of a menu of four. "Heads-up" and "Repeat every"
  are boxes now: `30`, `30 min`, `45s`, `1h`, `1h30` all work, and `Off` turns the heads-up off. Seconds are
  kept, so "repeat every 30s" really nags every 30 seconds (the reminder engine ticks every 5 s, so that is
  the smallest useful step).

## 0.63.1 - 2026-09-20 23:18
- **The setup notices when you are installing the version you already have.** It says so ("Lockdown 0.63.1 is
  already installed - this setup has the same version"), the button reads **Reinstall** instead of Update, and
  clicking it asks "Install the same version again?" first. Saying no leaves everything untouched; saying yes
  reinstalls as before, which is still the way to repair a broken install.

## 0.63.0 - 2026-09-20 22:22
- **Disable instead of remove.** A group you want to pause for a while no longer has to be deleted and built
  again from scratch.
  - **Groups > Edit group** has a **Disable** button next to Remove group, and **Blocking > Edit** has one for
    a single site or app. Everything is kept - rules, members, customisations - it just stops being enforced.
  - Disabled things leave the list and appear in a **Disabled** card at the bottom of Blocking > Overview,
    with an Edit button that takes you back in (where the button now says Enable).
  - An item whose group is paused shows a grey "→ Good Night · disabled" chip, so it doesn't look as if it
    lost its blockers.
  - Disabling stops a block, so Anti-Bypass asks for the challenge first ("Disable Good Night"). Enabling it
    again is free.
- **The allowance alert says what you started with**: "you have 5 min of your 15 min allowance left (until
  05:00)" instead of only the minutes remaining. The figure was already live - this just shows both numbers.
- The rule chips in Blocking > Overview have shown the allowance left since 0.61.0 ("+ 15 min allowed during
  blocked hours (6m left until 01:20)"), and since 0.62.0 that is the group's shared pot.

## 0.62.0 - 2026-09-20 21:59
- **A group's "N minutes allowed during blocked hours" is now one pot shared by every member.** Before, each
  member had its own 15 minutes, so a group of three quietly granted 45. Whichever member you use spends the
  same minutes, and when they run out everything in the group is blocked.
  - **Groups > By time** has a tick, **"One pot shared by every member"**, on by default. Turn it off and each
    member gets that many minutes of its own (the old behaviour). Turning it off is a weakening change, so
    Anti-Bypass asks for the challenge.
  - The Dashboard row and the alert are one per pot and carry the group's name, instead of one per member.
- **Adding something that is already on your list merges the blockers into it** instead of throwing them away.
  Adding YouTube with a time limit when YouTube is already blocked by hours now leaves it with both, and says
  "YouTube was already on the list - blockers merged". (It used to flip the form into Edit mode, drop what you
  had ticked, and grey out the address box - which looked like the page had frozen.)
- **A block starting no longer sends a popup for every site and app.** It is one line for the group ("Good
  Night started - 3 things blocked until 05:00.") and one for everything blocked by its own rules. What you
  actually try to open still gets its own alert, as before.

## 0.61.0 - 2026-09-20 21:37
- **You can see how much of your "N minutes allowed during blocked hours" is left**, instead of guessing.
  - **Dashboard > Limits today** now lists the allowance while you are inside the blocked stretch:
    "6 m of 15 m allowed during blocked hours (until 00:34)" with a bar and "9 m left".
  - **The rule chip** on Blocking > Overview says the same thing: "+ 15 min allowed during blocked hours
    (9m left until 00:34)", or "(used up until 00:34)" once it is gone.
  - **An alert** when you open the thing inside its blocked hours: "Discord is blocked now - you have 13 min of
    your allowance left (until 07:00)." Said once per blocked stretch, not on every check.
  - All three read the same `rules.allowance_left()`, so they cannot disagree about what is left.
- **"When blocked" is back for a category.** Picking a category on Blocking > Add now offers Close app /
  Minimize / Block internet the same way an app does, and its apps are closed, minimised or cut off the way you
  chose. (It never went away for apps - the row appears once the target is an app, and a category had no row
  at all.)

## 0.60.0 - 2026-09-20 21:17
- **You can set an app's category without opening it first.** Until now a category could only be set from the
  chip on a Screen Time row, and an app only appears there once you have actually used it - so there was no way
  to say "Discord is Distracting" before ever running it.
  - **Right-click an app or a website**, in Browse apps or on the Screen Time lists: **Category** (the same
    picker as the chip), **Block it...** (opens Blocking > Add with it filled in), **Add to group** (puts it on
    the blocklist and into that group, so the group's shared rules cover it) and, for an app, **Show in
    Explorer**. Something that is part of Windows says so instead of offering to block it.
  - **Browse apps** works as a browser on its own, not only as a picker for the Add tab: **Screen Time > Apps**
    has a "Browse all apps..." button that opens it. It lists every installed app and Steam game.
  - Websites stay out of the app search, as they are not installed apps - right-click them on Screen Time >
    Websites instead.

## 0.59.1 - 2026-09-20 21:02
- **The option switches are smaller again**, and the long ones are much shorter. They sit in rows next to other
  controls, so a window that wasn't maximised ran out of room for them.
  - The control: 11px text (was 12), 8px either side of a label (was 11), 24px tall (was 26).
  - The labels that made them wide: "Windows notification / Lockdown popup" -> **Windows / Lockdown**,
    "Match Windows" -> **System**, "Allow only during / Block during" -> **Allow only / Block**.
  - Measured: the notification one 326 -> 186 px (43% narrower), the hours one 226 -> 122 px (46%), the theme
    picker 302 -> 214 px (29%), and everything else 15-16% narrower.

## 0.59.0 - 2026-09-20 20:37
- **Resizing the window and switching pages are much lighter.** Three things were doing work for nothing:
  - All nine pages stayed **laid out** at once (only raised and lowered), so dragging the window edge made Tk
    re-lay-out and repaint every one of them. On a single resize step, 4 of the 6 chart redraws were for pages
    you could not see. Only the page you are on is laid out now - the rest are kept built, so switching to them
    is still instant, but they are out of the layout until you open them.
  - Charts re-rendered **while** the window was being dragged. They keep the picture they have during the drag
    and render once, sharply, about 0.2 s after you let go - you only ever look at the size you stop at. A
    <Configure> that doesn't change the size is ignored outright.
  - Opening a page refreshes it, and that **re-rendered every chart on it even when the data was identical**.
    A chart now compares what it is handed with what it is already showing and skips the work if they match
    (light / dark mode is part of that comparison, so switching theme still redraws everything).
  - Measured on the test machine: a 12-step drag went from 68 chart redraws / 1.7 s of drawing to 22 / 0.5 s,
    and switching between pages that are already up to date went from 15 redraws to none.

## 0.58.0 - 2026-09-20 19:28
- **Block a whole category.** On Blocking > Add there is now a **Category...** button next to Browse apps: pick
  Distracting (or any category of your own) instead of one site or app, and put any blocker on it - hours, a
  time limit, an opening limit, permanent, temporary.
  - It covers **everything** in that category, whether or not it is on your blocklist. Mark something
    Distracting on Screen Time and it is covered from then on, with nothing else to do.
  - A time limit on a category is **one shared pot**: 40 minutes of Discord and 20 of Reddit use up an hour of
    "Distracting: 1 h a day" between them.
  - A category means the same thing here as it does for a mode, so Work and a category blocker agree on what
    counts as Distracting (things on your blocklist count as Distracting unless you said otherwise).
  - Anything blocked in its own right keeps its own rule - the category never overrides it.
  - The row shows the category's colour and a CATEGORY badge.

## 0.57.2 - 2026-09-20 17:52
- **Setting a time limit on an app froze the window.** The note under the limit boxes measured the panel it was
  in and re-wrapped itself to fit. Re-wrapping made it taller, that flipped the page's scrollbar on, the
  scrollbar took the width it had just measured away, so it re-wrapped again - and CustomTkinter's scrollbar
  redraws by running the event loop, so it span there forever. It only tipped over on an app (an app has extra
  options, so the page sits right at the height where the scrollbar appears). The note has a fixed wrap width
  now: the editor already knows whether it is in the narrow group panel or the wide Add panel.
- **The window now writes its errors to the log.** Tk runs the whole GUI out of callbacks and throws away
  anything they raise, so a crash left nothing behind - which is why this one took a while to find. Errors go
  to `C:\ProgramData\Lockdown\lockdown.log` as "Lockdown window: ...". The same error in a row is written
  once a minute at most, so a callback that fails on every frame can't fill the disk.

## 0.57.1 - 2026-09-20 16:02
- **A site you put on "Allowed anyway" stayed blocked.** The lookup itself was right - it let the site and its
  subdomains through straight away - but the answer already handed out to Windows said "this is 127.0.0.1" and
  was allowed to sit in the DNS cache. Nothing flushed it, because the service only reacted when a *list*
  changed and adding a site to "allowed" didn't count as a change. Three fixes:
  - `Protection.refresh()` reports an "allowed anyway" change, not just a change of lists.
  - the service flushes the DNS cache when the protection state changes.
  - a blocked DNS answer may now be cached for 10 seconds instead of 60, so nothing lingers either way.
- This only covers sites blocked by a protection list or one of your own blocking lists. A site you blocked
  yourself on Blocking > Overview is a different thing - remove the block there, or use the emergency unlock.

## 0.57.0 - 2026-09-20 15:47
- **Clicking a button twice no longer opens the window twice.** A window only takes the click grab once it is
  actually on screen, and every pop-up waited a fixed 50 ms before even trying - so a fast double-click, or
  holding the button down, stacked a pile of them. Six confirm dialogs from six quick clicks, measured.
  - `widgets.once()` opens a window only if one of that kind isn't already open, and brings the open one to
    the front otherwise. Every pop-up goes through it: the challenge, confirm dialogs, Browse apps, the word
    lists, your blocking lists, the category editor, the display settings and a group member's rules.
  - `widgets.modal()` takes the grab as soon as the window can take one, instead of 50 ms later.
  - The Anti-Bypass challenge used to throw away the open window and put up a new one when a second change
    asked for it - losing a phrase you were halfway through typing. It now keeps the one you are answering and
    turns the new request down, so whatever asked for it puts itself back.

## 0.56.1 - 2026-09-20 15:33
- The segmented tabs were still too big: the chip left a band of colour above and below its label, which is what
  made them look cheap. The chip hugs its text now - track 30 -> 26 px, 14 -> 11 px either side, rounder corners
  (5 / 3). A short label like "All" keeps a minimum chip width so it doesn't shrink to nothing.
- **Screen Time**: the range switcher (Today / Yesterday / 7 days / 30 days) moved to the left of its row, where
  it belongs now that the page tabs sit up on the title row; the gear stays on the right. It had been right-
  aligned since the first design, when that row still held the tab group on its left.

## 0.56.0 - 2026-09-20 15:09
- **The switches are no longer pixelated.** Tk draws circles and rounded corners without anti-aliasing, so every
  knob had stair-stepped edges. Anything we draw ourselves now goes through `gui/paint.py`, which renders it with
  Pillow at 4x and scales it back down - the same trick the charts and the app icon already used.
- **The small segmented tabs** (All / Allowed / Blocked, Today / Yesterday, Dark / AMOLED / Light, ...) were a
  square block wedged inside a rounded track, in plain 13px text. They are redrawn to the design: a recessed
  track with 3px padding and smooth 3 / 2px corners, semibold 12px labels, muted until chosen. The track is
  darker than a card it sits on and lighter than the page background, as the design has it.
- **The display-settings gear** on Dashboard and Screen Time was a 16px muted glyph that was hard to see - it is
  22px now and in the normal text colour.

## 0.55.0 - 2026-09-20 04:36
- **Switching pages and tabs no longer flashes.** Three things were wrong:
  - The curtain that hides a page while it's swapped opened on a 60 ms timer. Whenever the swap was quicker than
    that - the normal case - a flat rectangle sat on screen for the rest of the 60 ms: the flash. It now opens on
    the next idle moment, before Tk paints, so it's never seen at all and the page just appears.
  - Charts drew 40 ms after the rest of the page, so a tab appeared with empty chart boxes that filled in a
    moment later. The first drawing now happens with the rest of the page (the delay stays for window resizing).
  - **Blocking > Overview was rebuilt from scratch every time you opened it** - every row, chip and button
    destroyed and made again. Rows are reused now, the way the rest of the app already did it. Opening Blocking
    went from ~1.6 s to ~0.16 s, and the Overview tab from ~1.2 s to ~0.08 s (measured on the test desktop).

## 0.54.0 - 2026-09-20 04:06
- **Dashboard "Coming up"**: tighter rows - a coloured dot per row (red = gets blocked, green = allowed again,
  yellow = a limit runs out), the time in the condensed face right next to it, and the time column only widens
  when something is on another day.
- **Blocking > Groups**: the list card is as tall as its groups instead of a full-height panel that's mostly
  empty; its scrollbar only appears when the list is actually longer than the room.
- **Modes**: each mode card has its own icon (Work, Study, Focus, Do Not Disturb, Relax; your own modes get the
  sliders icon) in a small tile next to the name - the cards used to be indistinguishable. Five Lucide icons
  added to `assets/icons`.

## 0.53.0 - 2026-09-20 03:53
- **Look-and-feel pass** (every page captured with realistic data and gone through):
  - Reminders: Sleep and Breaks sit side by side; labels in one muted column with the controls lined up after
    them; "Strict break" and "20-20-20" are short switches with a muted explanation underneath; the bedtime tip
    is a quiet note at the card's foot; "Breaks today" moved to the card header.
  - Blocking > Overview: statuses stay on one line ("Pending · service off", "Unlocked · 12 m left"), group rule
    chips use the spare width instead of wrapping ("→ School nights · Blocked: …").
  - Blocking > Groups: a proper empty-state card ("No group open" + New group) instead of a bare line of text.
  - Screen Time trend: the first / last date labels are no longer cut in half at the plot edges.
  - Add / group editor: the "0 of 5 on" count lines up with the rail instead of touching its edge.

## 0.52.0 - 2026-09-20 01:50
- **Blocking > Calendar rebuilt to design 4a / 4b**: one row per blocked item (icon, name, SITE / APP badge), a
  24 h track with the blocked stretches as bars (no text inside), "Blocked today" in its own column, a legend
  footer and three summary cards (Next change · Busiest stretch · Free window). A Day / 3 days / Week switcher
  and an Everything / Sites / Apps filter; in 3 days / Week each day is a mini strip - click one to open that day.
  Emergency unlocks show as Ⓔ on the item they unlocked. The old week grid (WeekCalendar) is gone.

## 0.51.0 - 2026-09-20 01:40
- **App icon (design 3n)**: crisp title-bar / taskbar icon. The .ico now carries 20 px and 40 px images (what
  Windows shows at 125% scaling - it used to scale the nearest size, which blurred it), and the 16-20 px
  versions draw the padlock as a pixel-snapped block with a 1 px shackle. The tray icon uses the same tiny mark.
- Strict break: a test pins that it never minimises Lockdown's own windows (it already skipped them).
- Challenge window: the "0 / 71" count sits at the right of the "Word 1 of 12" row; a disabled Continue is a
  faded accent with pale text. SITE / APP badges draw their full border.

## 0.50.1 - 2026-09-20 01:36
- Screen Time trend legend (design 3k): the swatches are solid short lines - the 3px CTk frames drew them broken.

## 0.50.0 - 2026-09-20 01:36
- **Small windows (design 3l)**:
  - Browse apps: an All / Running / Games filter, the list on its own bordered surface (icon · name · exe ·
    RUNNING), a Cancel button.
  - Suggestions dropdown: "SUGGESTIONS · “what you typed”" eyebrow, hairlines between rows, a footer hint.
  - Anti-Bypass challenge: the word in a chip ("Word 1 of 12"), left-aligned grid boxes; the phrase challenge
    gets a bordered phrase box, an accent-bordered entry and a thin progress bar; the "closed" variant says
    "Closed right now" with a red-tinted note.
  - Alert popup: **Open Lockdown** and **Mute 1 h** buttons (mute holds every popup for an hour).

## 0.49.0 - 2026-09-20 01:35
- **Settings (design 3j)**: two columns. Left: Appearance (theme, accent, daily goal) and "When limits reset" in
  one card, then Categories. Right: Emergency unlock with a yellow bar and a usage meter ("2 of 3 left this
  week · resets …"), and Backup & export. Status lines (reset error, backup result) only take space while they
  say something.

## 0.48.1 - 2026-09-20 01:23
- Site protection: the thin separators between list rows (and above "Your blocking lists") now actually draw -
  a 1px CTkFrame renders nothing, so hairlines are plain Tk frames (`components.hairline`).
- Notifications: the "On a protection list" alert cell no longer clips its label.

## 0.48.0 - 2026-09-20 01:17
- **Notifications (design 3i)**: the blocked-visit alerts are a grid of bordered cells (accent edge when on),
  each message has a Reset button, and Upcoming blocks / Weekly summary sit side by side.

## 0.47.0 - 2026-09-20 01:17
- **Network Log (design 3f)**: a **Rule** column (which rule blocked a visit), blocked rows tinted red, and the
  table's footer inside the card - hint on the left, "Show more (N older)" on the right.

## 0.46.0 - 2026-09-20 01:09
- **Anti-Bypass in two columns (design 3d)**: Challenges + "Keeping Lockdown running" on the left, "What needs
  the challenge" (with its tinted note) on the right, under the full-width lock banner - no more long stack.

## 0.45.0 - 2026-09-20 01:09
- **Site protection in two columns (design 3c)**: community lists, Connect a blocking list, your own lists,
  Safe search and Check a site on the left; Blocked words and Allowed anyway on the right. Descriptions wrap to
  the column; the URL field stretches to fit.

## 0.44.0 - 2026-09-20 01:01
- **Group editor: blockers side by side (design 3b)** - the group's blockers are now a left rail + one open
  panel on the right (shared with Blocking → Add) instead of stacked cards that expand downwards.
- **Add tab polish (3b)**: the panel has a header strip with the blocker's name + what it does and a footer hint
  ("Next: tick ... to combine it"); a tinted note under the rail spells out what will be blocked; rail rows are
  the design's height with the summary under the name.
- Fix: a fixed-width switch (list rows) no longer reserves 200px of height.

## 0.43.0 - 2026-09-20 00:52
- **Switches redrawn to the design (plate 3m)**: a proper 34x18 pill with the knob sitting inside the track (2px
  inset) and a 1px edge - no more thin track, and the white knob no longer melts into a white card in light mode.
- **Buttons at the design size**: ~34px tall with semibold labels (design padding 9x18); small icon buttons keep
  their own size.

## 0.42.3 - 2026-09-19 22:20
- **Notifications default to Lockdown's own popup** (the nice in-app one that slides up bottom-right) instead of
  the Windows notification - so "YouTube is blocked" shows as a Lockdown popup by default. You can still choose
  Windows notification or Both on the Notifications page.

## 0.42.2 - 2026-09-19 21:40
- **Site protection tidy-up**: the URL feature is now **"Connect a blocking list"** (it subscribes to an online
  list), set off from your own lists; **"Your blocking lists"** sits below the community lists with a divider.
- **Calendar legend**: the block calendar now has a small legend — the accent line is **now**, a blue dot marks
  an **emergency unlock used** — instead of a cryptic symbol in the footer text.

## 0.42.1 - 2026-09-19 21:35
- Renamed the Blocking sub-tab "Protection" to "Site protection".

## 0.42.0 - 2026-09-19 21:30
- **Restart fixed**: "Restart now" (after a theme/colour change) no longer flashes a console window and now
  reliably starts a fresh copy - the relaunch helper waits (hidden) until this copy has fully quit before
  launching, instead of racing it.
- **"Distracting" is always red**: the Distracting category no longer follows the accent colour (so changing
  the accent to blue won't make Distracting blue) - red is the logical "avoid" colour. Custom colours still win.
- **Import review**: importing a backup now shows exactly what will change (blocked items, groups, categories,
  which settings) and asks you to confirm, before the Anti-Bypass challenge and applying.
- **Polish**: the page sub-tabs lighten on hover; the confirm dialogs get a thin accent top edge like the cards.

## 0.41.1 - 2026-09-19 21:18
- **One-click Allow / Block on "Check a site"**: checking a site now shows an "Allow anyway" button when it's
  blocked (by any list - community or your own) and a "Block again" button when it's already allowed, so you
  can unblock a site in one place instead of hunting through each list. Also reports your own lists.

## 0.41.0 - 2026-09-19 21:11
- **Your own blocking lists**: on Blocking → Protection, create named lists and add sites to them by name +
  address, turn each list on/off, and open one to add/remove sites. They block the site and its subdomains via
  the same always-on filter as the community lists. Adding is instant; removing sites, turning a list off or
  deleting one needs the Anti-Bypass challenge (with the ~10s mis-click grace).
- **Manga & anime list**: a new built-in community list (off by default) covering unofficial manga / manhwa /
  anime and other pirate streaming/download sites (HaGeZi Anti-Piracy).

## 0.40.0 - 2026-09-19 20:59
- **Challenge phrase options** (Anti-Bypass): the random phrase is now **letters only by default**; a new
  "Include numbers and CAPITAL letters" toggle makes it harder. Dropping it counts as loosening (needs the
  challenge).
- **Your own phrase**: set a phrase only you know - you still type it exactly each time (no pasting), and with
  the 3×3 grid it's split into words by the spaces. A shorter custom phrase counts as loosening.

## 0.39.1 - 2026-09-19 20:51
- **Crisper title-bar / taskbar icon**: the 16px icon is now drawn as a bolder, canvas-filling shield (no tiny
  keyhole) so it stays sharp instead of looking muddy; larger sizes unchanged.
- **Clearer locked wording**: the locked strip and Anti-Bypass banner just say "Locked" (the "Unlock to edit"
  button explains the rest) instead of "changes that loosen a block need the challenge".
- **"Add your own blocking list"** (was "Add your own list") in Protection.
- **Trend legend**: a small legend under the Screen Time trend explains the lines (each day / 7-day average /
  your goal - the grey line is the 7-day average).

## 0.39.0 - 2026-09-19 19:55
- **Calendar: click a day** on Screen Time > Calendar to see that day's details (what you spent time on) -
  handy for looking back / for parents. The picked day is ringed.
- **Trend follows the range**: the Overview trend line is 7 days when "7 days" is selected, 30 otherwise,
  and hovering it shows a marker dot on the day under the cursor.
- **Day timeline** only shows for a single day (Today / Yesterday) - it's hidden for the 7 / 30-day ranges
  where the bars and heatmap already cover the span.
- **Mis-click grace**: after turning a protection switch stricter (SafeSearch, a word list, a blocklist),
  you have ~10 seconds to switch it back without the Anti-Bypass challenge; after that it needs the challenge.

## 0.38.0 - 2026-09-19 19:43
- **Lockdown can't be blocked**: lockdown.exe / lockdownservice.exe are protected, so you can't add them as
  a blocked app (or have a mode close them) and soft-lock yourself out.
- **Category changes need the challenge when locked**: changing an app's/site's category, adding one or
  deleting one goes through Anti-Bypass while it's locked (categories feed the modes that block by
  category). Recolouring stays free. Editing is still instant when unlocked / off.
- **Daily goal moved out of Appearance** into its own "Daily goal" section (still not locked - it doesn't
  affect blocking).
- **Limit reset**: clearer wording, and toggling the reset time can no longer stack the running limit day
  past the end of the next day (it still never resets a limit early).
- Import settings already required the Anti-Bypass challenge - unchanged.

## 0.37.0 - 2026-09-19 19:35
- **Reminders in the sidebar**: breaks, sleep and your own reminders are now their own top-level page
  (hourglass icon) instead of a hidden tab under Modes - much easier to find. Modes now just shows modes.
- **Editors close when you leave**: opening a mode's Start panel / editor or the reminder editor and then
  switching pages returns to the default view, so you don't have to click Cancel first.

## 0.36.0 - 2026-09-19 18:43
- **New sub-tabs (Round 3 design)**: the page sub-tabs (Blocking, Screen Time, Modes, Network Log) are now the
  underline / ink-bar style from the design - a row on the title line, the selected tab with an accent underline
  and brighter text - instead of the filled segmented pills. Small in-form option switches keep the pill look.
- **Continuous "now" line**: the current-time line on the block calendar is one full-height line across the whole
  grid instead of a short segment inside today's row (no more gaps between rows).
- **Live status**: the Anti-Bypass locked/unlocked banner now re-checks the clock on a timer, so its colour is
  right when an allowed-hours window opens or closes even if you never leave the tab.
- **Confirm before changing the challenge**: making the Anti-Bypass challenge stronger (a phrase, longer length,
  the grid, or restricting the hours) now asks you to confirm - so you can't accidentally lock yourself out.

## 0.35.0 - 2026-09-19 19:20
- **Installer finish flow**: the "run now" tick is hidden until it's done; when the update finishes you get a green
  check, a "Run Lockdown now" tick (on by default) and a **Finish** button - like a normal installer.
- **Anti-Bypass hours**: you can't remove the *only* time window any more (the x appears once there are two).
- **Emergency unlock can't be turned against you**: it no longer lists permanently-blocked items (e.g. adult sites)
  - it's for limits and scheduled hours, not for things you blocked for good.
- **Anti-Bypass unlocked banner**: shows a live "Unlocked for 4:59" countdown with a draining bar, instead of a
  fixed "until HH:MM".
- **Switches** are a bit chunkier so the track no longer looks too thin.

## 0.34.0 - 2026-09-19 18:45
- **New app icon and tray icon** (from the design): a shield with a padlock. The app icon (window, taskbar, notifications, installer) is a two-tone orange shield + white padlock; the tray icon shows the state in colour - **green** = blocking enforced, **yellow** = a mode is on, **red** = service down. Drawn with Pillow, so it scales cleanly from 16 px up.

## 0.33.0 - 2026-09-19 18:20
- **Break reminders reworked** (Modes > Reminders > Breaks):
  - **Default (gentle)**: the reminder pops up; "Start break" just closes it (it trusts you) and "Snooze" reminds you again after a customisable number of minutes.
  - **Strict break** (off by default): allows only a set number of snoozes, then the break starts on its own; while it runs it **minimises all your windows** until the time is up, and if you open something it re-minimises it with a "Break in progress - N min left" notice.
  - Customisable: how often, break length, snooze minutes, snoozes-before-auto-start. (Replaces the old "forced break" screen cover.)

## 0.32.1 - 2026-09-19 17:50
- **Blocked word on the last tab no longer closes the browser**: when the tab being closed is the only one open (which would close the whole window), Lockdown opens a fresh tab first, then closes the blocked one - so the window stays. (Chromium browsers; falls back to the old behaviour elsewhere.)

## 0.32.0 - 2026-09-19 17:35
- **Pages reopen on their default tab**: leaving a page and coming back no longer remembers the sub-tab - Blocking returns to Overview, Screen Time to its default tab, Network Log to Table, Modes to Modes.
- **Dashboard "Today" timeline is now full width** (its own row across the page), so the coloured bars are wide enough to read and hover even for short sessions.

## 0.31.2 - 2026-09-19 17:10
- **Categories are now easy to find**: a "Categories" section on the Settings page shows Productive / Neutral / Distracting (and your own) with their colours and a "Manage categories..." button (add, rename-by-colour, delete). Before, this was only reachable by clicking a category on an app/site.

## 0.31.1 - 2026-09-19 16:55
- **Blocked words: on/off switches for "Your words" and "Exceptions"** - quickly disable your own words (e.g. when they catch something you're writing) or your exceptions without deleting them. Turning your words off needs the Anti-Bypass challenge (it loosens blocking); turning exceptions off is stricter, so it's instant.
- **No more double word notice**: when a blocked word closes a tab or sends it back you now get a single message ("<word> is a blocked word - closing the tab / going back") instead of two.

## 0.31.0 - 2026-09-19 16:35
- **Round 2 redesign - locked state, app-wide**: when Anti-Bypass is locked, the Blocking and Settings pages now show a red "Locked - changes that loosen a block need the challenge" strip with an "Unlock to edit" button (and "Locked - outside the allowed hours" when in that state). Controls stay usable - trying to loosen one still opens the challenge as before - so nothing is greyed into looking broken.
- **Round 2 redesign is now feature-complete**: all planned pages, the Calendar, trend line, network markers, type badges, accent bars, the locked/unlocked states and the calm animations (sidebar pulse, pop-up slide/fade, confirm countdown, challenge-box breathing) are in.

## 0.30.4 - 2026-09-19 16:20
- **Round 2 redesign - animations**: the Remove "Confirm?" button now shows a thin bar that drains over its 3-second window, and the 3x3 challenge grid's active box gently pulses its border so the eye finds it. (With the sidebar pulse and the pop-up slide/fade, these are all the calm, buildable animations from the design; the deliberately-skipped ones would have felt busy or clashed with an existing control.)

## 0.30.3 - 2026-09-19 16:05
- **Round 2 redesign - small windows**: the site/app suggestion dropdown now shows a SITE / APP type badge on each row (site = grey, app = blue) and a cleaner name + detail layout. The Browse apps and Display settings windows already follow the round-2 look via the shared tokens.

## 0.30.2 - 2026-09-19 15:50
- **Round 2 redesign - phase F**: the in-app pop-up now slides up and fades in at the bottom-right with an accent edge, the Lockdown mark, a "now" label and a close X (stays while the mouse is over it). The Anti-Bypass challenge window lists the changes with accent arrows to match the round-2 look.

## 0.30.1 - 2026-09-19 15:35
- **Round 2 redesign - phase E finished**: the Network Log graph now draws the minutes that had a blocked attempt in **red** (with a small legend), so you can see at a glance when something was turned away.

## 0.30.0 — 2026-09-19 15:00
- **Round 2 redesign - phase E: Blocking > Calendar (new tab)**. A week view (Mon-Sun x 24 h) showing when each site/app is blocked: one bar per item, coloured by category, lane-packed so overlaps stack; permanent = all day, by-time schedules shown at their real hours (spilling past midnight), with the previous/next week arrows, a "this week" button, an emergency-unlock marker and a "now" line. Click a bar to edit that item. (Time/opening limits and temporary blocks aren't clock-time-based, so they aren't shown here.)

## 0.29.1 — 2026-09-19 15:20
- **Round 2 redesign — phase F (part 1)**: the Anti-Bypass page now shows a coloured **locked / unlocked banner** - red "Locked - you can look at everything, but loosening a block needs the challenge" with an "Unlock to edit" button, or a green "Unlocked until HH:MM" with "Lock now" during the 5-minute window.

## 0.29.0 — 2026-09-19 15:05
- **Notifications don't pile up as unread**: after a Windows notification shows, Lockdown removes *its own* entries from the Windows notification centre (the bell) a few seconds later, so one-time block/word alerts don't accumulate. Only Lockdown's notifications are cleared - never other apps'. Both display options stay (Windows notification / Lockdown popup / Both).

## 0.28.6 — 2026-09-19 14:40
- **Redesign fix (from verification pass)**: the Notifications page section titles ("Blocked Visit Alerts", "Upcoming Blocks", "Weekly Summary") now show the accent bar like every other page. A background verification agent checked all 11 pages in dark + light and found this as the only accent-bar inconsistency; everything else passed.

## 0.28.5 — 2026-09-19 14:30
- **Round 2 redesign — phase E (part 1): Screen Time trend line**. A new "Trend - last 30 days" card shows daily screen time as a line over a filled area, with a dashed 7-day average, the daily-goal line, hollow markers on days you used an emergency unlock, and a "vs last week" figure (down = green = improving).

## 0.28.4 — 2026-09-19 14:12
- **Round 2 redesign — phase D**: the Modes "Now" card and every mode card now use the accent top-bar card style; Settings sections get the same accent bar as the rest of the app.

## 0.28.3 — 2026-09-19 14:05
- **Round 2 redesign — phase C**: Blocking > Add now shows the blockers as a compact **rail** on the left with one **open settings panel** on the right (accent-top card), so the form never grows past the window. Ticking a blocker opens its panel; the open one is marked with an accent bar.

## 0.28.2 — 2026-09-19 13:49
- **Round 2 redesign — phase B**: a small accent bar now sits before every page title and every card / section title; site / app / group **type badges** appear next to blocked items; the sidebar service dot **pulses** while the service runs (green) and is a steady red when it is down.

## 0.28.1 — 2026-09-19 13:40
- **Round 2 redesign — phase A (design system)**: the whole app now uses the tightened round-2 palette
  (design/Lockdown Round 2.dc.html) — darker backgrounds, 4px cards / 2px controls, and the round-2 status colours.
- **Light mode fixes (7b)**: switch "off" tracks are a visible grey (#AEB6C0) instead of near-white, and outlined /
  secondary buttons now have a clear border (#B9C0C9) so they no longer vanish on white cards.
- Remaining redesign phases (title accent bars, type badges, sidebar pulse, the Add rail, Calendar, trend lines,
  locked view-only state, popups) are tracked in PLAN and coming next.

## 0.28.0 — 2026-09-19 13:04
- **YouTube Restricted Mode is now its own switch** (Blocking > Protection > Safe search), separate from Force
  SafeSearch. Restricted Mode also hides *all* YouTube comments, so turn it off if you want to read comments -
  SafeSearch for Google / Bing / DuckDuckGo stays on either way. (On by default, so nothing changes unless you turn
  it off; turning it off needs the Anti-Bypass challenge.)
- **Installer finish**: after an install / update it no longer just pops the app open behind the still-open setup
  window. It says it's done ("Blocking is active. Click Close to finish"), the button becomes **Close**, and a
  **"Run Lockdown when I close this window"** tick (on by default) opens the app when you close - like a normal
  installer.
- **Dashboard**: the "Blocked visits today" box now shrinks back to the small size after you Show then Hide it
  again (it used to stay tall).

## 0.27.1 — 2026-09-19 12:44
- **Installer fix**: `LockdownSetup.exe` failed with "[WinError 2] The system cannot find the file specified" when
  the admin account's PATH didn't include System32 (some PCs). It now calls every Windows tool (powershell, sc,
  schtasks, taskkill, icacls, cmd, explorer) by full path. The "Start service" button on the Dashboard was fixed
  the same way.

## 0.27.0 — 2026-09-19 06:21
- **Distracting by default**: every Steam game you have, plus popular sites / apps that are purely for fun (game
  launchers and games, Twitch, Netflix and other streaming, TikTok, 9gag...) - not ones also used for work or
  school (YouTube, Reddit, X, Discord, Spotify). Added once; anything you already categorised is kept, and you can
  change them on Screen Time
- Notifications page: "Recent blocked visits" removed (they're on the Dashboard)
- Design brief round 2 (`design/DESIGN.md` section 9 + 64 screenshots in `design/screenshots/round2`)
- 172 tests passing

## 0.26.0 — 2026-09-19 05:52
- **Phase 8b - Lockdown as a real Windows program**:
  - `Lockdown.exe` (app + tray) and `LockdownService.exe` - no Python needed (PyInstaller; `scripts\build.ps1`
    builds everything and runs a self-test of the built app: every page, fonts / icons / logo, browser reading)
  - **Windows service** "Lockdown Enforcer": starts at boot, Windows restarts it if it crashes, the "Lockdown
    Watchdog" task starts it again within a minute if it's stopped; "Start service" restarts it (admin prompt)
  - **`LockdownSetup.exe`** (34 MB): installs into Program Files, registers the service and watchdog, Start menu +
    desktop shortcuts, an "Apps & features" entry, then starts everything. Run it again to **update** - it stops
    Lockdown, replaces only the program and starts it again; your settings, blocks and history (in
    C:\ProgramData\Lockdown) are kept, older databases get new columns on their own. It also takes over from the
    scripts-based setup (removes the old scheduled task). **Uninstall** (Apps & features) asks for the Anti-Bypass
    challenge, then undoes network settings, browser policies, firewall rules and hosts entries; your data is kept
    unless you tick "also delete"
  - the service's "remove" step also removes Lockdown's hosts-file lines now
- **Protection lists: "Update automatically"** (on by default) with every 6 h / 12 h / daily (default) / weekly;
  off = only "Update now"
- 171 tests passing

## 0.25.0 — 2026-09-19 05:33
- **Phase 8a (Import & Polish)**:
  - **Your own block lists** (Blocking > Protection > "Add your own list"): any list on the internet by its address -
    hosts file, plain domains or adblock-style (||site.com^, blocks subdomains too). **Preview** first (how many sites
    + a few examples), then Add; downloaded / updated daily like the others, own switch, "on the <name> list" in the
    blocked notice, "Remove" (needs the Anti-Bypass challenge)
  - **Backup & export** (Settings): export everything you set up to a file and import it again (replaces your blocks
    and settings - Anti-Bypass challenge first), screen time as CSV (per day and app / site, with categories)
  - **Weekly summary** (Notifications): once a week (Sunday 19:00 by default) - screen time vs the week before,
    blocked visits, top app and site, days within your goal, streaks
  - **Streaks** (Dashboard > At a glance): days in a row within your daily goal, days without an emergency unlock
  - **Calendar** (Screen Time): a month at a time coloured by screen time, a red dot on days over your goal, ‹ ›
    for other months, the month's total / average day / days within goal / busiest day
  - **Display settings** (⚙ on Dashboard and Screen Time): show / hide each Dashboard card; which tab and range
    Screen Time opens with
- Fix: "Blocked visits today" was a tall empty box while its list was hidden
- 169 tests passing

## 0.24.0 — 2026-09-19 05:20
- **One search box for sites and apps** (Blocking > Add): typing suggests your sites, popular sites, installed apps
  and Steam games together ("Discord   discord.exe · app"); picking an app shows the app options. "Browse apps" stays
- **"?" hints**: explanations that were grey lines (By time, allowance, app block options, Protection, Anti-Bypass
  hours, Settings sections, Screen Time switches, Modes categories, Blocking group rules) are now a small "?" that
  shows the text on hover; important hints and half-titles stay as they were
- **Smoother switching**: changing page or tab (Blocking, Screen Time, Modes) shows the new page at once - it's
  drawn behind a cover for a moment instead of piece by piece in front of you
- **Locked modes**: "Stop (locked until 18:00)" works now but asks for the Anti-Bypass phrase first - also when no
  challenge is turned on (the lock would mean nothing otherwise); starting another mode over a locked one too
- **Themes** (Settings > Appearance): Dark, **AMOLED** (pure black), Light, Match Windows; **accent colour**: 8
  colours or any colour ("Custom..." colour picker) - the logo, buttons, switches, charts and heatmap follow it.
  Light / dark switch at once; AMOLED and a new colour need a restart ("Restart now" button)
- 164 tests passing

## 0.23.1 — 2026-09-19 04:58
- **Site suggestions fixed**: the box flashed while typing (a new window was made on every key, first shown at its
  default 200 × 200 size) and stayed on screen over other apps until you picked something. Now it's one window that
  is only updated, sized to its rows, a bit wider (long names are shortened, icons no longer pushed out), and it
  hides as soon as the focus leaves the box, the tab changes or another app comes to the front
- "+ Popular sites" button removed - the site box already suggests popular sites while you type
- Anti-Bypass: the "Real keyboard only" option removed; the 3×3 grid continues by itself once every word is typed
- 164 tests passing

## 0.23.0 — 2026-09-19 04:49
- **Fix: the window didn't come back** (taskbar / Alt+Tab) after minimizing Lockdown while a pop-up was open
  (Browse apps, a word list, the phrase window...): the pop-up held the focus and Tk ignored Windows' "restore"; now
  it lets go while Lockdown is minimized and takes it back when the window returns
- **Shortcuts**: Esc closes pop-up windows (like their X / Cancel; the bedtime and forced-break screens and reminder
  pop-ups don't); in every text box Ctrl+Z / Ctrl+Y undo / redo, Ctrl+Backspace / Ctrl+Delete delete a word,
  Ctrl+A selects all
- **Search forgives small typos** (1 wrong letter in 4-6 letter words, 2 in longer ones, swapped letters count as
  one, also while still typing; exact matches first) - Browse apps, site suggestions in Add, Network Log search,
  word list windows
- **Browse apps**: typing "steam" lists all Steam games right under Steam; "running" is a small green tag
- **Dashboard**: "Blocked visits today" shows only the count; "Show" opens the list (remembered)
- 164 tests passing

## 0.22.0 — 2026-09-19 04:31
- **Steam games** in Browse apps: read from Steam's own library list (every Steam library folder, the game's real name,
  "· Steam" tag); the game's main .exe is guessed (named like the game, else the biggest; crash reporters,
  installers and anti-cheat skipped). Picking one ticks "Also close its background processes", and for a Steam game
  that means everything running from its whole game folder (launchers, second exes, e.g. tModLoader via dotnet.exe)
- **Browse apps**: search is instant (rows reused instead of rebuilt on every key, waits until you pause typing,
  every word must match, first 60 results + a count); the list jumps back to the top after each search (the scrollbar
  no longer behaves oddly when there's nothing to scroll); app icons are extracted in the background
- **By time** (was "By hours"): all seven days are selected by default; × removes a time window (also in Modes'
  automatic times and Anti-Bypass' allowed hours)
- **"+ Add" button** is repainted when the Add tab is shown (it could stay unpainted after being built in the
  background)
- **Anti-Bypass "3×3 grid"** (off by default): instead of one long line, nine boxes - a random box lights up, you
  click it and type the word shown, then another box lights up with the next word; boxes are never focused for you
  (Tab skips them), so a macro can't type blindly; pasting blocked; turning it off needs the challenge
- Designer brief: a proper app icon + tray icon (DESIGN.md 7c)
- Anti-Bypass turned off in your settings again (you asked)
- 161 tests passing

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
