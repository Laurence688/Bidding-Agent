function Get-LauraPython {
    param([string]$ProjectRoot)
    $bundled = Join-Path $ProjectRoot '.runtimes\python\python.exe'
    if (Test-Path -LiteralPath $bundled) { return $bundled }
    $venv = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venv) { return $venv }
    throw 'Python runtime not found. Download and extract the complete portable Release ZIP.'
}
