<#
    Tracking Check - installer (Windows, PowerShell)

    Usage:
        .\setup.ps1                # install into .\.venv, create shortcuts, run the self-test
        .\setup.ps1 -Recreate      # delete .\.venv first
        .\setup.ps1 -NoShortcut    # skip the Start Menu / Desktop shortcuts
        .\setup.ps1 -Dev           # also install pytest

    Steps: find/install Python 3.12 -> .venv -> requirements.txt -> pip install -e . ->
           shortcuts -> --selftest (offline demo run on a generated workbook)
#>
[CmdletBinding()]
param([switch]$Recreate, [switch]$NoShortcut, [switch]$Dev)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$venv = Join-Path $root ".venv"
$py   = Join-Path $venv "Scripts\python.exe"
$pyw  = Join-Path $venv "Scripts\pythonw.exe"

function Info($m){ Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m){ Write-Host "!!  $m" -ForegroundColor Yellow }

function Find-Python {
    $pyl = (Get-Command py -ErrorAction SilentlyContinue).Source
    if ($pyl) {
        foreach ($ver in @("3.12","3.13","3.11")) {
            try {
                $p = & py "-$ver" -c "import sys;print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() }
            } catch {}
        }
    }
    foreach ($cmd in @("python","python3")) {
        $p = (Get-Command $cmd -ErrorAction SilentlyContinue).Source
        if ($p -and $p -notmatch "WindowsApps") {
            try {
                $v = & $p -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
                if ($v -match '^3\.(1[1-3])$') { return $p }
            } catch {}
        }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Warn "No Python 3.11-3.13 found. Installing Python 3.12 via winget ..."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget is not available. Install Python 3.12 (64-bit) from https://python.org and re-run."
    }
    & winget install --id Python.Python.3.12 --source winget --scope user --accept-package-agreements --accept-source-agreements -e
    Start-Sleep -Seconds 3
    $cand = @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:PROGRAMFILES\Python312\python.exe") |
            Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($cand) { $python = $cand } else { $python = Find-Python }
    if (-not $python) { throw "Python installed but not found. Open a new terminal and re-run setup.ps1." }
}
Info "Using Python: $python"

if ($Recreate -and (Test-Path $venv)) { Info "Removing .venv ..."; Remove-Item -Recurse -Force $venv }
if (-not (Test-Path $py)) { Info "Creating .venv ..."; & $python -m venv $venv }
if (-not (Test-Path $py)) { throw "venv creation failed." }

& $py -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) { Warn "tkinter missing - the window won't start (the --cli mode still works)." }

Info "Installing dependencies ..."
& $py -m pip install --upgrade pip wheel setuptools --quiet
& $py -m pip install -r (Join-Path $root "requirements.txt")
if ($Dev) { & $py -m pip install -r (Join-Path $root "requirements-dev.txt") }
& $py -m pip install -e $root --quiet

$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
if ($edge) { Info "Website lookups will use Microsoft Edge: $edge" }
else {
    Warn "Microsoft Edge not found - downloading Playwright's Chromium for website lookups ..."
    & $py -m playwright install chromium
}

if (-not $NoShortcut) {
    $target = if (Test-Path $pyw) { $pyw } else { $py }
    $ws = New-Object -ComObject WScript.Shell
    $desktop   = [Environment]::GetFolderPath("Desktop")
    $startmenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
    foreach ($dir in @($desktop, $startmenu)) {
        $old = Join-Path $dir "Tracking Checker.lnk"      # the app's earlier name
        if (Test-Path $old) { Remove-Item $old -Force; Info "Removed old shortcut: $old" }
        $lnk = Join-Path $dir "Tracking Check.lnk"
        $s = $ws.CreateShortcut($lnk)
        $s.TargetPath = $target
        $s.Arguments = "-m tracking_checker"
        $s.WorkingDirectory = $root
        $s.Description = "Mass-check shipment tracking numbers from a spreadsheet"
        $s.Save()
        Info "Shortcut: $lnk"
    }
}

Info "Running self-test (offline demo run) ..."
& $py -m tracking_checker --selftest
$code = $LASTEXITCODE
Write-Host ""
if ($code -eq 0) { Info "Setup complete - self-test PASSED." } else { Warn "Setup finished but the self-test FAILED (exit $code)." }
Write-Host ""
Write-Host "  Launch       :  .\run.ps1   (or the 'Tracking Check' shortcut)"
Write-Host "  Headless     :  .\.venv\Scripts\python -m tracking_checker --cli --file `"C:\path\book.xlsx`" --check `"not delivered`""
Write-Host "  Diagnostics  :  .\.venv\Scripts\python -m tracking_checker --doctor"
Write-Host "  API key guide:  docs\CARRIER_API_SETUP.md"
Write-Host ""
exit $code
