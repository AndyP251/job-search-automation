@echo off
REM ===================================================
REM  Job Scraper - One-Time Setup (Windows)
REM  Double-click this file to install everything.
REM ===================================================

echo.
echo  ==============================
echo   Job Scraper - Setup
echo  ==============================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [!] Python is not installed!
    echo.
    echo  Please download and install Python from:
    echo  https://www.python.org/downloads/
    echo.
    echo  IMPORTANT: Check the box that says
    echo  "Add Python to PATH" during installation!
    echo.
    pause
    exit /b
)

echo  [OK] Python found.
echo.
echo  Installing required packages...
echo.

pip install flask playwright requests beautifulsoup4 lxml

echo.
echo  Installing browser for scraping...
echo.

playwright install chromium

echo.
echo  ==============================
echo   Setup complete!
echo  ==============================
echo.
echo  You can now double-click "RUN.bat" to start.
echo.
pause
