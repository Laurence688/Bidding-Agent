param(
    [string]$Python = '',
    [switch]$DownloadEmbedding
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $versionCheck = 'import sys; assert sys.version_info[:2] == (3, 11), "Python 3.11 is required"'
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        if ($Python) {
            & $Python -c $versionCheck
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 is required.' }
            & $Python -m venv .venv
        } elseif (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3.11 -c $versionCheck
            if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 or pass -Python.' }
            & py -3.11 -m venv .venv
        } else {
            & python -c $versionCheck
            if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 or pass -Python.' }
            & python -m venv .venv
        }
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
    }
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    & $venvPython -c $versionCheck
    if ($LASTEXITCODE -ne 0) { throw 'Existing environment must use Python 3.11.' }
    & node -e 'const [a,b]=process.versions.node.split(".").map(Number); if(a<22||(a===22&&b<12)) process.exit(1)'
    if ($LASTEXITCODE -ne 0) { throw 'Node.js 22.12+ is required.' }
    & $venvPython -m pip install -r backend/requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependencies failed to install.' }
    & $venvPython -m pip install -e './backend[embedding]'
    if ($LASTEXITCODE -ne 0) { throw 'Backend installation failed.' }
    & npm.cmd --prefix frontend ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependencies failed to install.' }
    & npm.cmd --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    Get-ChildItem -LiteralPath config -Filter '*.example.yaml' | ForEach-Object {
        $destination = Join-Path $_.DirectoryName ($_.Name -replace '\.example\.yaml$', '.yaml')
        if (-not (Test-Path -LiteralPath $destination)) { Copy-Item -LiteralPath $_.FullName -Destination $destination }
    }
    if (-not (Test-Path -LiteralPath '.env.local')) { Copy-Item -LiteralPath '.env.example' -Destination '.env.local' }
    if ($DownloadEmbedding) {
        & $venvPython -c 'import os,sys; os.environ["HF_HUB_DISABLE_TELEMETRY"]="1"; from huggingface_hub import snapshot_download; snapshot_download(repo_id="Qwen/Qwen3-Embedding-0.6B", local_dir=sys.argv[1])' (Join-Path $PSScriptRoot 'data\local-models\embedding')
        if ($LASTEXITCODE -ne 0) { throw 'Embedding download failed; retry after checking your network.' }
        & $venvPython -c 'import sys; from pathlib import Path; p=Path(sys.argv[1]); required=["model.safetensors","config.json","tokenizer.json","modules.json","1_Pooling/config.json"]; assert all((p/n).is_file() for n in required), "Embedding files are incomplete"' (Join-Path $PSScriptRoot 'data\local-models\embedding')
        if ($LASTEXITCODE -ne 0) { throw 'Embedding directory is incomplete.' }
    }
    Write-Host 'Installation complete. Run .\Start.ps1 and configure your own provider and knowledge library.'
} finally {
    Pop-Location
}
