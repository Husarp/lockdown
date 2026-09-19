# Removes the Lockdown enforcement task (after the Anti-Bypass challenge, if it's on). Run from an elevated PowerShell:
#   Set-ExecutionPolicy -Scope Process Bypass -Force; & "<project folder>\scripts\uninstall_service.ps1"
# Blocks stay in the hosts file until you clear the blocklist first (or edit the hosts file by hand).
$ErrorActionPreference = "Stop"
$TaskName = "Lockdown Enforcer"
$Root = Split-Path -Parent $PSScriptRoot
$PyHome = (Get-Content "$Root\.venv\pyvenv.cfg" | Select-String '^home\s*=\s*(.+)$').Matches[0].Groups[1].Value.Trim()

# Anti-Bypass: uninstalling needs the challenge
$challenge = Start-Process -FilePath "$Root\.venv\Scripts\pythonw.exe" -Wait -PassThru `
    -ArgumentList "`"$Root\src\main.py`" --challenge `"Uninstall Lockdown`""
if ($challenge.ExitCode -ne 0) { throw "Not uninstalled: the Anti-Bypass challenge wasn't passed." }

foreach ($name in @("Lockdown Watchdog", $TaskName)) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $name
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
}
cmd /c 'schtasks /Delete /F /TN "Lockdown Agent Watchdog" >nul 2>&1'

# Undo the browser DoH/QUIC policies, firewall rules and DNS filter
& (Join-Path $PyHome "python.exe") "$Root\src\service.py" remove-policies
Write-Output "Removed '$TaskName'."
