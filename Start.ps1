$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'scripts\service_manager.py') start
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
