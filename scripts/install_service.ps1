# Installs the Lockdown enforcement service as a SYSTEM scheduled task (runs at boot, restarts on failure).
# Run once from an elevated (Administrator) PowerShell:
#   Set-ExecutionPolicy -Scope Process Bypass -Force; & "<project folder>\scripts\install_service.ps1"
# Re-run it after changing service code to restart the service with the new code.
$ErrorActionPreference = "Stop"
$TaskName = "Lockdown Enforcer"
$Root = Split-Path -Parent $PSScriptRoot
$DataDir = "C:\ProgramData\Lockdown"

# The service only uses the standard library, so it runs on the base Python the venv was made from.
$PyHome = (Get-Content "$Root\.venv\pyvenv.cfg" | Select-String '^home\s*=\s*(.+)$').Matches[0].Groups[1].Value.Trim()
$Pythonw = Join-Path $PyHome "pythonw.exe"
if (-not (Test-Path $Pythonw)) { throw "pythonw.exe not found at $Pythonw" }

# Shared data folder: the GUI (your user) and the service (SYSTEM) both write config.db.
New-Item -ItemType Directory -Force $DataDir | Out-Null
icacls $DataDir /grant "*S-1-5-32-545:(OI)(CI)M" | Out-Null   # BUILTIN\Users: modify

$action = New-ScheduledTaskAction -Execute $Pythonw -Argument "`"$Root\src\service.py`" run" -WorkingDirectory "$Root\src"
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $TaskName

# Watchdog: every minute, start the service again if it was stopped (does nothing while it runs)
$WatchdogName = "Lockdown Watchdog"
$wAction = New-ScheduledTaskAction -Execute "schtasks.exe" -Argument "/Run /TN `"$TaskName`""
$wTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1)
$wSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
if (Get-ScheduledTask -TaskName $WatchdogName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $WatchdogName -Confirm:$false
}
Register-ScheduledTask -TaskName $WatchdogName -Action $wAction -Trigger $wTrigger -Principal $principal `
    -Settings $wSettings | Out-Null
Write-Output "Installed and started '$TaskName' (+ '$WatchdogName'). Log: $DataDir\lockdown.log"
