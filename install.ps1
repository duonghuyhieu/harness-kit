# harness-kit one-line installer for Windows (PowerShell 5.1+ / 7).
#
#   irm https://raw.githubusercontent.com/duonghuyhieu/harness-kit/main/install.ps1 | iex
#       -> installs the harness + .claude/ into the CURRENT directory (run it from your project folder)
#   .\install.ps1 C:\path\to\project [harness install options]     (from a clone)
#   $env:HARNESS_TARGET = "C:\path\to\project"; irm ... | iex      (piped, other folder)
#
# Piped, the kit is downloaded to ~\.harness-kit (re-running updates it). Needs Python >= 3.10. No admin rights.
# Everything runs in a script block: under `irm | iex` no variable or setting leaks into your session.
& {
    $ErrorActionPreference = "Stop"
    $Repo = "duonghuyhieu/harness-kit"

    function Find-Python {
        foreach ($cand in @(@("python"), @("py", "-3"), @("python3"))) {
            if (Get-Command $cand[0] -ErrorAction SilentlyContinue) {
                $pre = @()
                if ($cand.Count -gt 1) { $pre = $cand[1..($cand.Count - 1)] }
                # a probe that writes to stderr (the Microsoft Store "python" stub) must not abort the installer
                $ok = $null
                try {
                    $ErrorActionPreference = "Continue"
                    $ok = & $cand[0] @pre -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
                } catch { $ok = $null } finally { $ErrorActionPreference = "Stop" }
                if ($LASTEXITCODE -eq 0 -and "$ok".Trim() -eq "True") { return , $cand }
            }
        }
        return $null
    }

    $py = Find-Python
    if (-not $py) {
        Write-Host "harness-kit needs Python >= 3.10 (python, py -3 or python3 on PATH)." -ForegroundColor Red
        Write-Host "Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run this again."
        return
    }

    # 1. Locate the kit: next to this script (a clone), otherwise download it to ~\.harness-kit
    $kitDir = $null
    if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "harness.py"))) {
        $kitDir = $PSScriptRoot
    } else {
        $kitDir = Join-Path $HOME ".harness-kit"
        $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("harness-kit-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $tmp | Out-Null
        Write-Host "Downloading harness-kit from github.com/$Repo ..."
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $zip = Join-Path $tmp "kit.zip"
        Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/$Repo/archive/refs/heads/main.zip" -OutFile $zip
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $src = Get-ChildItem -Path $tmp -Directory | Select-Object -First 1
        if (Test-Path $kitDir) { Remove-Item -Recurse -Force $kitDir }
        Move-Item -Path $src.FullName -Destination $kitDir
        Remove-Item -Recurse -Force $tmp
    }

    # 2. Install into the target project (never overwrites your files)
    $target = (Get-Location).Path
    $rest = @()
    if ($args.Count -gt 0) { $target = $args[0]; if ($args.Count -gt 1) { $rest = $args[1..($args.Count - 1)] } }
    elseif ($env:HARNESS_TARGET) { $target = $env:HARNESS_TARGET }
    $pre = @()
    if ($py.Count -gt 1) { $pre = $py[1..($py.Count - 1)] }
    & $py[0] @pre (Join-Path $kitDir "harness.py") install $target @rest
} @args
