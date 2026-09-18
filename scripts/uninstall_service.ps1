# Removes the Lockdown enforcement task. Run from an elevated PowerShell:
#   Set-ExecutionPolicy -Scope Process Bypass -Force; & "<project folder>\scripts\uninstall_service.ps1"
# Blocks stay in the hosts file until you clear the blocklist first (or edit the hosts file by hand).
$ErrorActionPreference = "Stop"
$TaskName = "Lockdown Enforcer"
Stop-ScheduledTask -TaskName $TaskName
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Output "Removed '$TaskName'."
