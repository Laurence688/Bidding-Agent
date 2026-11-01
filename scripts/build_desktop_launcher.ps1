$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path -LiteralPath $compiler)) { throw 'Windows .NET Framework compiler not found.' }
$source = Join-Path $projectRoot 'packaging\desktop\Launcher.cs'
$icon = Join-Path $projectRoot 'packaging\desktop\laura.ico'
$output = Join-Path $projectRoot 'Laura.exe'
& $compiler /nologo /target:winexe /platform:anycpu /optimize+ "/out:$output" "/win32icon:$icon" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll $source
if ($LASTEXITCODE -ne 0) { throw 'Laura launcher build failed.' }
Write-Host "Built $output"
