# Lockdown Mobile

The Android companion of [Lockdown for Windows](../README.md): a website and app blocker for your
phone, built to help you stick to your own limits. Same ideas as the desktop app — block lists, site
lists, schedules, daily time limits, modes, reminders and anti-bypass — with a native Material You
look (colours follow your wallpaper; dark/light follows the phone).

**Package:** `com.husarp.lockdown` · **Min Android:** 8.0 (API 26) · **Target:** Android 15 (API 35).

## What it will do

- **Blocking** — block apps and sites: forever, on a schedule, or after a daily time limit. Sites are
  filtered through a local DNS-filter VPN (works in every browser and app). Keywords are caught by an
  accessibility service reading the address / search bar.
- **Insights (screen time)** — a timeline of your day and per-app totals and opens, switchable between
  Today, Yesterday, 7 days, 30 days or a chosen date.
- **Modes** — focus / bedtime modes that switch groups of rules on together.
- **Reminders** — bedtime (with the escalating alerts), breaks, and your own reminders; a reminder can
  be marked *Important* so it can't be turned off without the challenge.
- **Guardrails (anti-bypass)** — a challenge in front of anything that loosens a rule, uninstall
  protection via device admin, a foreground service that survives being killed and reboots, and
  warnings if the accessibility service or VPN is switched off. Strong, self-imposed friction — always
  removable by you, because it's your device.
- **Settings** — appearance, update checks (GitHub releases), emergency unlocks, and importing your
  lists from Lockdown on the PC.
- **Extras (each an on/off setting)** — pause / open-cap before opening an app, bedtime grayscale,
  Quick Settings tile and home-screen widgets (time used / time left), forced SafeSearch and YouTube
  Restricted Mode, a weekly digest, and a set number of emergency unlocks.

## Build

Needs the Android SDK and a JDK 17 (Android Studio's bundled JBR works). From this folder:

```
./gradlew assembleDebug
```

Install on a connected phone (main profile only):

```
adb install -r --user 0 app/build/outputs/apk/debug/app-debug.apk
```

## Status

Early scaffold: navigation shell with the seven sections as placeholders. See [PLAN.md](PLAN.md) for
the build order and [CHANGELOG.md](CHANGELOG.md) for what's landed.
