$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\ResolvePython.ps1')
& (Get-LauraPython -ProjectRoot $PSScriptRoot) (Join-Path $PSScriptRoot 'scripts\service_manager.py') stop
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
