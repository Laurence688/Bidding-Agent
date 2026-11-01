param(
    [string]$Python = '',
    [switch]$DownloadEmbedding,
    [switch]$NoDesktopShortcut
)
$ErrorActionPreference = 'Stop'
$previousPath = $env:PATH
$previousPythonHome = $env:PYTHONHOME
$previousPythonPath = $env:PYTHONPATH
$previousUserSite = $env:PYTHONNOUSERSITE
$env:PYTHONHOME = $null
$env:PYTHONPATH = $null
$env:PYTHONNOUSERSITE = '1'
Push-Location $PSScriptRoot
try {
    $bundle = $null
    if (Test-Path -LiteralPath 'laura-bundle.json') { $bundle = Get-Content -LiteralPath 'laura-bundle.json' -Raw | ConvertFrom-Json }
    $bundledPython = Join-Path $PSScriptRoot '.runtimes\python\python.exe'
    $bundledNode = Join-Path $PSScriptRoot '.runtimes\node\node.exe'
    $runtimeHelper = Join-Path $PSScriptRoot 'scripts\setup_runtime.py'
    if (Test-Path -LiteralPath $bundledPython) {
        $pythonExe = $bundledPython
    } else {
        if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
            if ($Python) {
                & $Python -I $runtimeHelper check-python
                if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 is required.' }
                & $Python -m venv .venv
            } elseif (Get-Command py -ErrorAction SilentlyContinue) {
                & py -3.11 -I $runtimeHelper check-python
                if ($LASTEXITCODE -ne 0) { throw 'Use the portable Release ZIP, or install Python 3.11.' }
                & py -3.11 -m venv .venv
            } else {
                & python -I $runtimeHelper check-python
                if ($LASTEXITCODE -ne 0) { throw 'Use the portable Release ZIP, or install Python 3.11.' }
                & python -m venv .venv
            }
            if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
        }
        $pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    }
    & $pythonExe -I $runtimeHelper check-python
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 is required.' }
    $nodeExe = 'node'
    $npmCommand = 'npm.cmd'
    if (Test-Path -LiteralPath $bundledNode) {
        $nodeFolder = Split-Path -Parent $bundledNode
        $env:PATH = "$nodeFolder;$previousPath"
        $nodeExe = $bundledNode
        $npmCommand = Join-Path $nodeFolder 'npm.cmd'
    }
    $nodeVersionText = & $nodeExe --version
    if ($LASTEXITCODE -ne 0) { throw 'Use the portable Release ZIP, or install Node.js 22.12+.' }
    $nodeVersion = [Version]($nodeVersionText.Trim().TrimStart('v'))
    if ($nodeVersion.Major -lt 22 -or ($nodeVersion.Major -eq 22 -and $nodeVersion.Minor -lt 12)) { throw 'Node.js 22.12+ is required.' }
    if ($bundle -and $bundle.backend_preinstalled) {
        & $pythonExe -I $runtimeHelper probe-backend
        if ($LASTEXITCODE -ne 0) { throw 'Bundled backend is incomplete. Extract the complete portable ZIP again.' }
    } else {
        & $pythonExe -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) { throw 'pip initialization failed.' }
        & $pythonExe -m pip install -r backend/requirements.lock.txt
        if ($LASTEXITCODE -ne 0) { throw 'Backend dependencies failed to install.' }
        & $pythonExe -m pip install -e './backend[embedding]'
        if ($LASTEXITCODE -ne 0) { throw 'Backend installation failed.' }
    }
    if (-not ($bundle -and $bundle.frontend_prebuilt -and (Test-Path -LiteralPath 'frontend\dist\index.html'))) {
        & $npmCommand --prefix frontend ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependencies failed to install.' }
        & $npmCommand --prefix frontend run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    Get-ChildItem -LiteralPath config -Filter '*.example.yaml' | ForEach-Object {
        $destination = Join-Path $_.DirectoryName ($_.Name -replace '\.example\.yaml$', '.yaml')
        if (-not (Test-Path -LiteralPath $destination)) { Copy-Item -LiteralPath $_.FullName -Destination $destination }
    }
    if (-not (Test-Path -LiteralPath '.env.local')) { Copy-Item -LiteralPath '.env.example' -Destination '.env.local' }
    if ($DownloadEmbedding) {
        $embeddingPath = Join-Path $PSScriptRoot 'data\local-models\embedding'
        & $pythonExe $runtimeHelper download-embedding --target $embeddingPath
        if ($LASTEXITCODE -ne 0) { throw 'Embedding download failed. Check your network and double-click Laura.exe again to retry.' }
    }
    if (-not $NoDesktopShortcut) {
        try { & (Join-Path $PSScriptRoot 'scripts\CreateDesktopShortcut.ps1') } catch { Write-Warning 'Desktop shortcut could not be created. Double-click Laura.exe instead.' }
    }
    if ($bundle) {
        New-Item -ItemType Directory -Path 'data\runtime' -Force | Out-Null
        Set-Content -LiteralPath 'data\runtime\first-run.ready' -Value 'ready' -Encoding ASCII
    }
    Write-Host 'Ready. Double-click Laura.exe to use Laura in your browser.'
} finally {
    $env:PATH = $previousPath
    $env:PYTHONHOME = $previousPythonHome
    $env:PYTHONPATH = $previousPythonPath
    $env:PYTHONNOUSERSITE = $previousUserSite
    Pop-Location
}
