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
    $versionCheck = 'import sys; assert sys.version_info[:2] == (3, 11), "Python 3.11 is required"'
    if (Test-Path -LiteralPath $bundledPython) {
        $pythonExe = $bundledPython
    } else {
        if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
            if ($Python) {
                & $Python -c $versionCheck
                if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 is required.' }
                & $Python -m venv .venv
            } elseif (Get-Command py -ErrorAction SilentlyContinue) {
                & py -3.11 -c $versionCheck
                if ($LASTEXITCODE -ne 0) { throw 'Use the portable Release ZIP, or install Python 3.11.' }
                & py -3.11 -m venv .venv
            } else {
                & python -c $versionCheck
                if ($LASTEXITCODE -ne 0) { throw 'Use the portable Release ZIP, or install Python 3.11.' }
                & python -m venv .venv
            }
            if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
        }
        $pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    }
    & $pythonExe -I -c $versionCheck
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 is required.' }
    $nodeExe = 'node'
    $npmCommand = 'npm.cmd'
    if (Test-Path -LiteralPath $bundledNode) {
        $nodeFolder = Split-Path -Parent $bundledNode
        $env:PATH = "$nodeFolder;$previousPath"
        $nodeExe = $bundledNode
        $npmCommand = Join-Path $nodeFolder 'npm.cmd'
    }
    & $nodeExe -e 'const [a,b]=process.versions.node.split(".").map(Number); if(a<22||(a===22&&b<12)) process.exit(1)'
    if ($LASTEXITCODE -ne 0) { throw 'Use the portable Release ZIP, or install Node.js 22.12+.' }
    if ($bundle -and $bundle.backend_preinstalled) {
        & $pythonExe -I -c 'import fastapi, uvicorn, yaml, httpx, langgraph, faiss, sentence_transformers, markdown_it; print("Bundled backend ready")'
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
        & $pythonExe -c 'import os,sys; from pathlib import Path; p=Path(sys.argv[1]); required=["model.safetensors","config.json","tokenizer.json","modules.json","1_Pooling/config.json"]; complete=all((p/n).is_file() for n in required); os.environ["HF_HUB_DISABLE_TELEMETRY"]="1"; from huggingface_hub import snapshot_download; complete or snapshot_download(repo_id="Qwen/Qwen3-Embedding-0.6B", local_dir=str(p), allow_patterns=["*.json","model.safetensors","*.txt","LICENSE","README.md"]); assert all((p/n).is_file() for n in required), "Embedding files are incomplete"' $embeddingPath
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
