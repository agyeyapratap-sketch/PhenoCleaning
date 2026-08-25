@echo off
REM ============================================================================
REM  PlantEye pipeline - one-click launcher (Windows, no Docker needed)
REM  Double-click this file, or run it from a terminal. Leave the window OPEN
REM  while you use the app; close it (or press Ctrl+C) to stop the app.
REM ============================================================================
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo.
echo   Starting the PlantEye pipeline app...
echo   A browser tab should open automatically at http://localhost:8501
echo   (if not, open that address yourself).
echo.
echo   Keep THIS window open while you work. Press Ctrl+C here to stop.
echo.

python -m streamlit run app\pe_app.py --server.port 8501

echo.
echo   The app has stopped. If there was an error above, copy it for help.
pause
