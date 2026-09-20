$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Cannot create Python environment.' }
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt -c requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Cannot install dependencies.' }
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
Write-Host 'Ready. Edit .env, then run .\start.ps1'
