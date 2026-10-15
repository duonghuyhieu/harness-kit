# Standard boot path (Windows): set up environment -> verify baseline -> print the app start command.
# The real commands live in .harness/config.json (single source of truth).
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot
$py = (Get-Command python -ErrorAction SilentlyContinue)
if ($null -eq $py) { $py = Get-Command py -ErrorAction Stop }

Write-Host "==> Working directory: $PWD"
Write-Host "==> Installing dependencies"
& $py.Source scripts/harness.py setup
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> Verifying baseline"
& $py.Source scripts/harness.py check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> App start command:"
& $py.Source scripts/harness.py setup --print-dev
if ($env:RUN_START_COMMAND -eq '1') { & $py.Source scripts/harness.py setup --run-dev }
