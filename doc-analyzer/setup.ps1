<#
    Document Corpus Analyzer - installer / bootstrapper (Windows, PowerShell)

    Usage:
        .\setup.ps1                 # base install into .\.venv  + Start Menu / Desktop shortcut
        .\setup.ps1 -Full           # also install optional extras (cloud sources, OCR, Claude) + the local Qwen model
        .\setup.ps1 -LocalModel     # add just the local Qwen LLM stack (torch/transformers) to .\.venv
        .\setup.ps1 -Recreate       # delete .\.venv first, then install
        .\setup.ps1 -NoShortcut     # skip creating the shortcuts

    Steps, in order:
      1. find or install Python 3.12 (py launcher / PATH / winget)
      2. create .\.venv
      3. install requirements.txt        (light, no compilers)
      4. install requirements-optional.txt  (only with -Full)
      4b. install requirements-llm.txt   (with -Full or -LocalModel: torch + transformers for the Qwen objective planner)
      5. pip install -e .  (registers the 'doc-analyzer' command)
      6. copy config.example.toml -> config.toml (first run only)
      7. create Start Menu + Desktop shortcuts that launch the GUI
      8. run --selftest against the bundled sample_docs
#>
[CmdletBinding()]
param(
    [switch]$Full,
    [switch]$LocalModel,
    [switch]$Recreate,
    [switch]$NoShortcut
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$venv = Join-Path $root ".venv"
$py   = Join-Path $venv "Scripts\python.exe"
$pyw  = Join-Path $venv "Scripts\pythonw.exe"

function Info($m){ Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m){ Write-Host "!!  $m" -ForegroundColor Yellow }

# --------------------------------------------------------------------- Python
function Find-Python {
    foreach ($cmd in @("python","python3")) {
        $p = (Get-Command $cmd -ErrorAction SilentlyContinue).Source
        if ($p) {
            try {
                $v = & $p -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
                if ($v -match '^3\.(1[1-3])$') { return $p }
            } catch {}
        }
    }
    $pyl = (Get-Command py -ErrorAction SilentlyContinue).Source
    if ($pyl) {
        foreach ($ver in @("3.12","3.13","3.11")) {
            try {
                $p = & py "-$ver" -c "import sys;print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() }
            } catch {}
        }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Warn "No suitable Python (3.11-3.13) found. Installing Python 3.12 via winget ..."
    $wg = (Get-Command winget -ErrorAction SilentlyContinue).Source
    if (-not $wg) {
        throw "winget is not available. Install Python 3.12 (64-bit) from https://python.org and re-run."
    }
    & winget install --id Python.Python.3.12 --source winget --scope user `
        --accept-package-agreements --accept-source-agreements -e
    Start-Sleep -Seconds 3
    $cand = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:PROGRAMFILES\Python312\python.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($cand) { $python = $cand } else { $python = Find-Python }
    if (-not $python) { throw "Python installed but not found. Open a new terminal and re-run setup.ps1." }
}
Info "Using Python: $python"
& $python -c "import sys;print('    version', sys.version)"

# --------------------------------------------------------------------- venv
if ($Recreate -and (Test-Path $venv)) { Info "Removing existing .venv ..."; Remove-Item -Recurse -Force $venv }
if (-not (Test-Path $py)) { Info "Creating virtual environment in .venv ..."; & $python -m venv $venv }
if (-not (Test-Path $py)) { throw "venv creation failed ($py not found)." }

# tkinter check (needed for the GUI; ships with python.org / winget builds)
& $py -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
    Warn "tkinter is not available in this Python. The GUI will not start; the CLI (--cli) still works."
    Warn "Install a python.org 3.12 build (includes Tcl/Tk) and re-run with -Recreate."
}

Info "Upgrading pip / wheel / setuptools ..."
& $py -m pip install --upgrade pip wheel setuptools

Info "Installing base dependencies (requirements.txt) ..."
& $py -m pip install -r (Join-Path $root "requirements.txt")

if ($Full) {
    Info "Installing optional extras (requirements-optional.txt) ..."
    & $py -m pip install -r (Join-Path $root "requirements-optional.txt")
    Warn "For image OCR you also need the Tesseract engine:  winget install UB-Mannheim.TesseractOCR"
} else {
    Warn "-Full not set: cloud sources (S3/Azure/SharePoint/HTTP), OCR and Claude are NOT installed yet."
}

if ($Full -or $LocalModel) {
    Info "Installing the local Qwen LLM stack into .venv (requirements-llm.txt) - torch + transformers, ~2-3 GB ..."
    & $py -m pip install -r (Join-Path $root "requirements-llm.txt")
    & $py -c "import torch, transformers; print('    torch', torch.__version__, '| transformers', transformers.__version__)"
    if ($LASTEXITCODE -eq 0) {
        Info "Local model stack ready in this app's .venv. The Qwen3 weights (~8 GB) download on the first objective run."
    } else {
        Warn "torch/transformers did not import after install - the objective planner will fall back to the heuristic."
    }
} else {
    Warn "-LocalModel not set: the Qwen objective planner (torch/transformers) is NOT installed in .venv."
    Warn "Add it later with:  .\setup.ps1 -LocalModel"
}

Info "Installing doc-analyzer (editable) ..."
& $py -m pip install -e $root

# --------------------------------------------------------------------- config
$cfg = Join-Path $root "config.toml"
if (-not (Test-Path $cfg)) {
    Copy-Item (Join-Path $root "config.example.toml") $cfg
    Info "Wrote config.toml (edit it to enable Claude or online sources)."
}

# --------------------------------------------------------------------- shortcuts
if (-not $NoShortcut) {
    $target = $pyw
    if (-not (Test-Path $target)) { $target = $py }
    $ws = New-Object -ComObject WScript.Shell
    $desktop   = [Environment]::GetFolderPath("Desktop")
    $startmenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
    foreach ($dir in @($desktop, $startmenu)) {
        $lnk = Join-Path $dir "Document Corpus Analyzer.lnk"
        $s = $ws.CreateShortcut($lnk)
        $s.TargetPath       = $target
        $s.Arguments        = "-m doc_analyzer"
        $s.WorkingDirectory = $root
        $s.Description       = "Analyze a folder or online repository of documents"
        $s.IconLocation      = "$target,0"
        $s.Save()
        Info "Shortcut: $lnk"
    }
}

# ------------------------------------------------------- local model resolution
& $py -c "import torch, transformers" 2>$null
if ($LASTEXITCODE -eq 0) {
    Info "Local model: this app's own .venv has torch/transformers - the Qwen planner runs self-contained."
    Write-Host "     The Qwen3 weights download on first objective run if not already cached."
} else {
    $msVenv = @(
        (Join-Path (Split-Path $root -Parent) "meeting-scribe\.venv\Scripts\python.exe"),
        (Join-Path (Split-Path $root -Parent) "meeting_scribe\.venv\Scripts\python.exe")
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($msVenv) {
        & $msVenv -c "import torch, transformers" 2>$null
        if ($LASTEXITCODE -eq 0) {
            Info "Local model: will reuse meeting-scribe's venv ($msVenv) for the Qwen planner."
            Write-Host "     Run  .\setup.ps1 -LocalModel  to make this app self-contained instead."
        } else {
            Warn "No torch/transformers in this .venv or meeting-scribe's."
            Warn "Run  .\setup.ps1 -LocalModel  (or meeting-scribe's setup.ps1 -Full)."
            Warn "Until then, objective analysis uses the deterministic heuristic planner."
        }
    } else {
        Warn "The Qwen objective planner is not installed (no torch/transformers in .venv,"
        Warn "no meeting-scribe checkout beside this app).  Run  .\setup.ps1 -LocalModel ."
        Warn "Until then, objective analysis uses the deterministic heuristic planner."
    }
}

# --------------------------------------------------------------------- selftest
Info "Running self-test on bundled sample_docs ..."
& $py -m doc_analyzer --selftest
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) { Info "Setup complete - self-test PASSED." } else { Warn "Setup finished but self-test FAILED (exit $code)." }
Write-Host ""
Write-Host "  Launch GUI  :  .\run.ps1        (or the 'Document Corpus Analyzer' shortcut)"
Write-Host "  Headless    :  .\.venv\Scripts\python -m doc_analyzer --cli --source `"C:\path\to\docs`""
Write-Host "  Diagnostics :  .\.venv\Scripts\python -m doc_analyzer --doctor"
Write-Host "  Objective   :  .\.venv\Scripts\python -m doc_analyzer --cli --source `"C:\docs`" --objective `"...`"  (add --no-llm to skip the model)"
Write-Host "  Sessions    :  $root\sessions"
Write-Host ""
exit $code
