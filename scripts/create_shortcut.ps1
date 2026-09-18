# Creates double-click shortcuts "Lockdown.lnk" (project folder + Desktop) that start the GUI with no console window.
# Re-run if the project folder moves. No admin needed.
$Root = Split-Path -Parent $PSScriptRoot
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @($Root, [Environment]::GetFolderPath("Desktop"))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "Lockdown.lnk"))
    $lnk.TargetPath = "$Root\.venv\Scripts\pythonw.exe"
    $lnk.Arguments = "`"$Root\src\main.py`""
    $lnk.WorkingDirectory = "$Root\src"
    $lnk.Description = "Lockdown"
    $lnk.Save()
    Write-Output "Created $dir\Lockdown.lnk"
}
