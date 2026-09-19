# Emergency fix if pages stop loading because of Lockdown.
# Stops the service, gives the network adapters their own DNS settings back (DNS filter off), removes the Lockdown
# section from the hosts file and flushes DNS. Lockdown stays off until you run install_service.ps1 again.
# Run from an elevated (Administrator) PowerShell:
#   Set-ExecutionPolicy -Scope Process Bypass -Force; & "<project folder>\scripts\repair_dns.ps1"
$ErrorActionPreference = "Stop"
$TaskName = "Lockdown Enforcer"
$Root = Split-Path -Parent $PSScriptRoot
$Hosts = "$env:SystemRoot\System32\drivers\etc\hosts"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $TaskName }
Get-CimInstance Win32_Process -Filter "name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -like "*service.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

$PyHome = (Get-Content "$Root\.venv\pyvenv.cfg" | Select-String '^home\s*=\s*(.+)$').Matches[0].Groups[1].Value.Trim()
& (Join-Path $PyHome "python.exe") "$Root\src\service.py" restore-dns

$lines = [IO.File]::ReadAllLines($Hosts)
$keep = New-Object System.Collections.Generic.List[string]
$inside = $false
foreach ($l in $lines) {
    if ($l.Trim() -like "# >>> Lockdown START*") { $inside = $true; continue }
    if ($inside -and $l.Trim() -eq "# <<< Lockdown END") { $inside = $false; continue }
    if (-not $inside) { $keep.Add($l) }
}
[IO.File]::WriteAllLines($Hosts, $keep)
ipconfig /flushdns | Out-Null
Write-Output "Lockdown stopped, DNS settings restored, hosts file cleaned. Run install_service.ps1 to turn it back on."
