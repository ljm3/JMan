# Launch the Tracking Check window (run .\setup.ps1 once first).
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) { Write-Host "Not installed yet - run .\setup.ps1 first." -ForegroundColor Yellow; exit 1 }
Start-Process -FilePath $pyw -ArgumentList "-m", "tracking_checker" -WorkingDirectory $root
