# Removes the Lockdown enforcement task. Run from an elevated PowerShell:
#   Set-ExecutionPolicy -Scope Process Bypass -Force; & "<project folder>\scripts\uninstall_service.ps1"
# Blocks stay in the hosts file until you clear the blocklist first (or edit the hosts file by hand).
$ErrorActionPreference = "Stop"
$TaskName = "Lockdown Enforcer"
$Root = Split-Path -Parent $PSScriptRoot
Stop-ScheduledTask -TaskName $TaskName
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false

# Undo the browser DoH/QUIC policies
$PyHome = (Get-Content "$Root\.venv\pyvenv.cfg" | Select-String '^home\s*=\s*(.+)$').Matches[0].Groups[1].Value.Trim()
& (Join-Path $PyHome "python.exe") "$Root\src\service.py" remove-policies
Write-Output "Removed '$TaskName'."
