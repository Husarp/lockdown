# Lockdown

Windows app that blocks websites and apps, tracks all network activity, and makes it genuinely hard to cheat your own rules.

## What it does

- **Block websites** — permanently, on a schedule, with daily time limits, or temporarily
- **Block apps** — prevent games/launchers/apps from running or accessing the internet
- **Network log** — see every connection your PC makes (which app, where, when)
- **Anti-bypass** — locked settings, delayed unlocks, challenges, watchdog services — designed to beat your own willpower

## Status

**Phase 1 (MVP) done:** permanent website blocking via the hosts file, enforcement service, GUI with blocklist + popular-sites quick-list, system tray.
See [PLAN.md](PLAN.md) for the full plan and later phases.

## How it works

- The **GUI** (runs as you) writes the blocklist to `C:\ProgramData\Lockdown\config.db`.
- The **enforcement service** (runs as SYSTEM) checks the database every 5 s and rewrites a marked
  `# >>> Lockdown` section in `C:\Windows\System32\drivers\etc\hosts`, redirecting blocked hostnames to `127.0.0.1`.
  Manual edits to that section are repaired automatically. The rest of the hosts file is never touched;
  a backup of the original is kept at `C:\ProgramData\Lockdown\hosts.backup`.
- Service log: `C:\ProgramData\Lockdown\lockdown.log`.

## Setup

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

Remove it with `scripts\uninstall_service.ps1` (clear the blocklist first so the hosts entries are removed).

## Usage

```powershell
.\.venv\Scripts\pythonw.exe src\main.py
```

Closing the window minimizes it to the tray; use the tray icon's **Exit** to quit.
Tray icon: green = service enforcing, red = service not running.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Set `LOCKDOWN_DATA_DIR` to use a different data folder (e.g. for testing).

## Known limitations

- The hosts file has no wildcards: blocking `reddit.com` also blocks `www.reddit.com`, but not other subdomains
  unless they are listed (the quick-list includes the common ones).
- Browsers with DNS-over-HTTPS enabled may skip the hosts file, and already-open connections can keep working
  for a while after blocking.
