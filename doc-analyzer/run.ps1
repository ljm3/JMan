<#  Launch the Document Corpus Analyzer.
    No args           -> GUI
    --cli / --doctor / --selftest / ... -> forwarded to the CLI  #>
param([Parameter(ValueFromRemainingArguments = $true)] $Args)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py  = Join-Path $root ".venv\Scripts\python.exe"
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"

if (-not (Test-Path $py)) {
    Write-Host "Virtual environment not found. Run .\setup.ps1 first." -ForegroundColor Yellow
    exit 1
}

if ($Args -and ($Args -contains "--cli" -or $Args -contains "--doctor" -or $Args -contains "--selftest" -or $Args -contains "--version")) {
    & $py -m doc_analyzer @Args
} else {
    if (Test-Path $pyw) { & $pyw -m doc_analyzer @Args } else { & $py -m doc_analyzer @Args }
}
