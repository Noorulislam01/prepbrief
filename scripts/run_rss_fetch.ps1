$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "PrepBrief virtual environment not found at $Python. Create it and install requirements first."
}
Set-Location -LiteralPath $ProjectRoot
& $Python -m pipeline.scheduled_fetch
exit $LASTEXITCODE
