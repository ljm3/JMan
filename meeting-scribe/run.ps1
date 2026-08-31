<#  Launch Meeting Scribe (desktop app).  Pass --cli / --doctor to forward to the CLI.  #>
param([Parameter(ValueFromRemainingArguments = $true)] $Args)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root ".venv\Scripts\pythonw.exe"
$pyc = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $pyc)) {
    Write-Host "Virtual environment not found. Run .\setup.ps1 first." -ForegroundColor Yellow
    exit 1
}

if ($Args -and ($Args -contains "--cli" -or $Args -contains "--doctor" -or $Args -contains "--configure-defaults")) {
    & $pyc -m meeting_scribe @Args
} else {
    # GUI: use pythonw so no console window lingers
    if (Test-Path $py) { & $py -m meeting_scribe @Args }
    else { & $pyc -m meeting_scribe @Args }
}
