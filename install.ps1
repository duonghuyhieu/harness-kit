# Install harness-kit into a project (Windows PowerShell 5.1+ / PowerShell 7).
#   .\install.ps1 [project-dir] [harness install options...]
#   .\install.ps1 -Global            # skills + agents into ~/.claude for every project
# Needs Python >= 3.10. Runs the kit's own installer; nothing is downloaded and no admin rights are needed.
param(
    [string]$Target = ".",
    [switch]$Global,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
)
$ErrorActionPreference = "Stop"
$kit = Join-Path $PSScriptRoot "harness.py"

$py = $null
foreach ($cand in @(@("python"), @("py", "-3"), @("python3"))) {
    if (Get-Command $cand[0] -ErrorAction SilentlyContinue) {
        $pre = @()
        if ($cand.Count -gt 1) { $pre = $cand[1..($cand.Count - 1)] }
        $ok = & $cand[0] @pre -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
        if ($ok -eq "True") { $py = $cand; break }
    }
}
if (-not $py) {
    Write-Error "harness-kit needs Python >= 3.10 (python, py -3 or python3 on PATH)."
    exit 1
}
$pre = @()
if ($py.Count -gt 1) { $pre = $py[1..($py.Count - 1)] }
if ($Global) {
    & $py[0] @pre $kit install --global @Rest
} else {
    & $py[0] @pre $kit install $Target @Rest
}
exit $LASTEXITCODE
