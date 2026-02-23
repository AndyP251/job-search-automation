@echo off
REM ===================================================
REM  Job Scraper - Run (Windows)
REM  Double-click this file to launch the dashboard.
REM ===================================================

echo.
echo  Starting Job Scraper Dashboard...
echo  Opening http://localhost:5050 in your browser...
echo.
echo  (Keep this window open while using the dashboard)
echo  (Press Ctrl+C to stop)
echo.

start http://localhost:5050
python scraper_ui.py
pause
