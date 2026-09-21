# Builds the installer: build\LockdownSetup.exe (and the program folder build\dist\Lockdown).
#   1. Lockdown.exe + LockdownService.exe (PyInstaller, installer\lockdown.spec)
#   2. self-test: the built app starts hidden with a temporary data folder and builds every page (a tray icon
#      shows for a moment; skip with -NoSelfTest)
#   3. LockdownSetup.exe = the setup program + the program folder zipped inside it
# No admin needed. Run from any folder:  & "<project folder>\scripts\build.ps1"
param([switch]$NoSelfTest)
$ErrorActionPreference = "Continue"   # (PyInstaller writes its progress to stderr; failures are checked below)
$Root = Split-Path -Parent $PSScriptRoot
$Py = "$Root\.venv\Scripts\python.exe"
$Build = "$Root\build"
Set-Location $Root

New-Item -ItemType Directory -Force $Build | Out-Null
& $Py "$Root\installer\version_res.py" $Build | Out-Null   # the version Explorer shows on the .exe files
if ($LASTEXITCODE) { throw "Writing the version resource failed" }

& $Py -m PyInstaller --noconfirm --log-level WARN --distpath "$Build\dist" --workpath "$Build\work" installer\lockdown.spec
if ($LASTEXITCODE) { throw "Building the program failed" }

if (-not $NoSelfTest) {
    $report = "$Build\selftest.txt"
    Remove-Item $report -ErrorAction SilentlyContinue
    Start-Process "$Build\dist\Lockdown\Lockdown.exe" -ArgumentList "--selftest", "`"$report`"" -Wait
    $result = Get-Content $report -Raw -ErrorAction SilentlyContinue
    if (-not $result -or -not $result.StartsWith("OK")) { throw "Self-test failed:`n$result" }
    Write-Output "Self-test: $result"
}

$zip = "$Build\payload.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path "$Build\dist\Lockdown\*" -DestinationPath $zip -CompressionLevel Optimal -ErrorAction Stop

& $Py -m PyInstaller --noconfirm --log-level WARN --onefile --noconsole --uac-admin --name LockdownSetup `
    --icon "$Root\assets\lockdown.ico" --paths "$Root\src" --version-file "$Build\version_LockdownSetup.txt" `
    --add-data "$zip;." --add-data "$Root\assets\lockdown.ico;." `
    --distpath $Build --workpath "$Build\work-setup" --specpath "$Build\work-setup" installer\setup.py
if ($LASTEXITCODE) { throw "Building the installer failed" }
$setup = Get-Item "$Build\LockdownSetup.exe"
$size = "{0:N0}" -f ($setup.Length / 1MB)
Write-Output "Built $Build\LockdownSetup.exe ($size MB, version $($setup.VersionInfo.FileVersion))"
