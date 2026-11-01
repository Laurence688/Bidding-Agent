param([string]$Target = '')
$ErrorActionPreference = 'Stop'
$programRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'ResolvePython.ps1')
$python = Get-LauraPython -ProjectRoot $programRoot
if (-not $Target) {
    $Target = $programRoot
    if (-not (Test-Path -LiteralPath (Join-Path $programRoot 'data\app.db'))) {
        Add-Type -AssemblyName System.Windows.Forms
        $desktop = [Environment]::GetFolderPath('Desktop')
        $shortcut = Join-Path $desktop 'Laura.lnk'
        if (Test-Path -LiteralPath $shortcut) {
            $shell = New-Object -ComObject WScript.Shell
            $existing = Split-Path -Parent ($shell.CreateShortcut($shortcut).TargetPath)
            if ($existing -and (Test-Path -LiteralPath (Join-Path $existing 'laura-bundle.json'))) { $Target = $existing }
        }
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = '已有Laura请选择原来的Laura文件夹：将自动备份并保留知识库、对话、记忆和设置。新用户可选择当前文件夹。'
        $dialog.SelectedPath = $Target
        $dialog.ShowNewFolderButton = $false
        if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { exit 0 }
        $Target = $dialog.SelectedPath
    }
}
$oldHome = $env:PYTHONHOME
$oldPath = $env:PYTHONPATH
try {
    $env:PYTHONHOME = $null
    $env:PYTHONPATH = $null
    $env:PYTHONNOUSERSITE = '1'
    $env:PYTHONUTF8 = '1'
    & $python -I (Join-Path $PSScriptRoot 'check_updates.py') --target $Target
    exit $LASTEXITCODE
} finally {
    $env:PYTHONHOME = $oldHome
    $env:PYTHONPATH = $oldPath
}
