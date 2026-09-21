# Changelog

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
