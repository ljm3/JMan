<#
    Meeting Scribe - installer / bootstrapper (Windows, PowerShell)

    Usage:
        .\setup.ps1                 # install everything into .\.venv
        .\setup.ps1 -Recreate       # delete .\.venv first, then install
        .\setup.ps1 -SkipMl         # only the light deps (no torch/whisper/LLM)
        .\setup.ps1 -DownloadModels # also pre-download the Whisper + LLM models

    It will, in order:
      1. find or install Python 3.12 (via the py launcher, PATH, or winget)
      2. create a virtual environment in .\.venv
      3. install requirements.txt  (light)
      4. install requirements-ml.txt  (torch / faster-whisper / transformers / pyannote)
      5. write hardware-tuned defaults to the config file
#>
[CmdletBinding()]
param(
    [switch]$Recreate,
    [switch]$SkipMl,
    [switch]$DownloadModels
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$venv = Join-Path $root ".venv"
$py   = Join-Path $venv "Scripts\python.exe"

function Info($m){ Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m){ Write-Host "!!  $m" -ForegroundColor Yellow }

# --------------------------------------------------------------------- Python
function Find-Python {
    # 1. an existing 3.10-3.12 on PATH
    foreach ($cmd in @("python","python3")) {
        $p = (Get-Command $cmd -ErrorAction SilentlyContinue).Source
        if ($p) {
            try {
                $v = & $p -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
                if ($v -match '^3\.(1[0-2])$') { return $p }
            } catch {}
        }
    }
    # 2. the py launcher
    $pyl = (Get-Command py -ErrorAction SilentlyContinue).Source
    if ($pyl) {
        foreach ($ver in @("3.12","3.11","3.10")) {
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
    Warn "No suitable Python (3.10-3.12) found. Installing Python 3.12 via winget ..."
    $wg = (Get-Command winget -ErrorAction SilentlyContinue).Source
    if (-not $wg) {
        throw "winget is not available. Please install Python 3.12 (64-bit) from https://python.org and re-run this script."
    }
    & winget install --id Python.Python.3.12 --source winget --scope user --accept-package-agreements --accept-source-agreements -e
    Start-Sleep -Seconds 3
    $cand = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:PROGRAMFILES\Python312\python.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($cand) { $python = $cand }
    else {
        $python = Find-Python
        if (-not $python) { throw "Python was installed but could not be located. Open a new terminal and re-run setup.ps1." }
    }
}
Info "Using Python: $python"
& $python -c "import sys;print('    version', sys.version)"

# --------------------------------------------------------------------- venv
if ($Recreate -and (Test-Path $venv)) {
    Info "Removing existing virtual environment ..."
    Remove-Item -Recurse -Force $venv
}
if (-not (Test-Path $py)) {
    Info "Creating virtual environment in .venv ..."
    & $python -m venv $venv
}
if (-not (Test-Path $py)) { throw "venv creation failed ($py not found)." }

Info "Upgrading pip / wheel ..."
# setuptools must stay < 81: ctranslate2 (via faster-whisper) still imports
# pkg_resources, which setuptools 81 removed.
& $py -m pip install --upgrade pip wheel "setuptools<81"

# --------------------------------------------------------------------- deps
Info "Installing application dependencies (requirements.txt) ..."
& $py -m pip install -r (Join-Path $root "requirements.txt")

if (-not $SkipMl) {
    Info "Installing the ML stack (requirements-ml.txt) - this is a large download ..."
    & $py -m pip install -r (Join-Path $root "requirements-ml.txt")
} else {
    Warn "-SkipMl set: torch / faster-whisper / transformers / pyannote NOT installed."
}

Info "Installing Meeting Scribe (editable) ..."
& $py -m pip install -e $root

# --------------------------------------------------------------------- config
if (-not $SkipMl) {
    Info "Writing hardware-tuned defaults ..."
    & $py -m meeting_scribe --configure-defaults
}

if ($DownloadModels -and -not $SkipMl) {
    Info "Pre-downloading models (Whisper + alignment). LLM downloads on first run."
    & $py -c "from faster_whisper import WhisperModel; import meeting_scribe.config as c; m=c.Config.load().get('transcription','model'); print('whisper:',m); WhisperModel(m, device='cpu', compute_type='int8')"
    & $py -c "import torchaudio, meeting_scribe.config as c; b=c.Config.load().get('alignment','bundle'); getattr(torchaudio.pipelines,b).get_model(); print('alignment bundle ready:',b)"
}

Write-Host ""
Info "Setup complete."
Write-Host ""
Write-Host "  Launch the app :  .\run.ps1"
Write-Host "  Diagnostics    :  .\.venv\Scripts\python -m meeting_scribe --doctor"
Write-Host "  Headless run   :  .\.venv\Scripts\python -m meeting_scribe --cli --source <file> --docs minutes_with_synopsis,action_items --formats docx"
Write-Host ""
Write-Host "  For speaker separation, open File > Settings in the app and paste a"
Write-Host "  Hugging Face token after accepting the conditions for"
Write-Host "  pyannote/speaker-diarization-3.1 and pyannote/segmentation-3.0 on huggingface.co."
Write-Host ""
