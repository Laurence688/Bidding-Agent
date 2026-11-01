$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$launcherPath = Join-Path $projectRoot 'Laura.exe'
if (-not (Test-Path -LiteralPath $launcherPath)) { throw 'Laura.exe not found. Extract the complete project first.' }
$desktopPath = [Environment]::GetFolderPath('Desktop')
if (-not $desktopPath) { throw 'Windows desktop directory not found.' }
$shortcutShell = New-Object -ComObject WScript.Shell
$shortcutPath = Join-Path $desktopPath 'Laura.lnk'
if (Test-Path -LiteralPath $shortcutPath) {
    $existingShortcut = $shortcutShell.CreateShortcut($shortcutPath)
    if ($existingShortcut.TargetPath -ne $launcherPath) {
        $suffix = 1
        do {
            $shortcutPath = Join-Path $desktopPath ("Laura ($suffix).lnk")
            $suffix++
        } while (Test-Path -LiteralPath $shortcutPath)
    }
}
$shortcut = $shortcutShell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcherPath
$shortcut.WorkingDirectory = $projectRoot
$iconPath = Join-Path $projectRoot 'packaging\desktop\laura.ico'
$shortcut.IconLocation = if (Test-Path -LiteralPath $iconPath) { "$iconPath,0" } else { "$launcherPath,0" }
$shortcut.Description = 'Launch Laura in your browser; close its pages to stop local services.'
$shortcut.Save()
Write-Host "Created $shortcutPath"
