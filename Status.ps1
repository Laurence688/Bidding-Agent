$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'scripts\service_manager.py') status
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
