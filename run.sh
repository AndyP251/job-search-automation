#!/bin/bash
# ===================================================
#  Job Scraper - Run (Mac / Linux)
#  Double-click or run this to launch the dashboard.
# ===================================================

# cd to the folder this script is in
cd "$(dirname "$0")"

echo ""
echo " Starting Job Scraper Dashboard..."
echo " Opening http://localhost:5050 in your browser..."
echo ""
echo " (Keep this window open while using the dashboard)"
echo " (Press Ctrl+C to stop)"
echo ""

# Open browser (works on Mac and most Linux)
if command -v open &> /dev/null; then
    open http://localhost:5050 &
elif command -v xdg-open &> /dev/null; then
    xdg-open http://localhost:5050 &
fi

python3 scraper_ui.py
