#!/bin/bash
# ===================================================
#  Job Scraper - One-Time Setup (Mac / Linux)
#  Run this file once to install everything.
# ===================================================

echo ""
echo " =============================="
echo "  Job Scraper - Setup"
echo " =============================="
echo ""

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo " [!] Python 3 is not installed!"
    echo ""
    echo " Mac:   Install from https://www.python.org/downloads/"
    echo "        or run:  brew install python3"
    echo ""
    echo " Linux: sudo apt install python3 python3-pip"
    echo ""
    exit 1
fi

echo " [OK] Python 3 found."
echo ""
echo " Installing required packages..."
echo ""

pip3 install flask playwright requests beautifulsoup4 lxml

echo ""
echo " Installing browser for scraping..."
echo ""

python3 -m playwright install chromium

echo ""
echo " =============================="
echo "  Setup complete!"
echo " =============================="
echo ""
echo " Run:  ./run.sh   (or double-click run.sh)"
echo ""
