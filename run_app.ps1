# PlantEye pipeline - one-click launcher (PowerShell, no Docker needed)
# Run:  powershell -ExecutionPolicy Bypass -File run_app.ps1
# Keep this window open while you use the app; press Ctrl+C to stop.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) {
    Write-Host "Python was not found on PATH. Install Python 3.12+ and try again." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}

Write-Host ""
Write-Host "  Starting the PlantEye pipeline app..." -ForegroundColor Green
Write-Host "  A browser tab should open at http://localhost:8501 (open it yourself if not)."
Write-Host "  Keep this window open while you work. Press Ctrl+C to stop."
Write-Host ""

python -m streamlit run app\pe_app.py --server.port 8501

Write-Host ""
Write-Host "  The app has stopped."
Read-Host "Press Enter to close"
