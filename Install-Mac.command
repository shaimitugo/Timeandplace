#!/bin/bash
set -e
cd "$(dirname "$0")"
trap 'echo "Installation failed. Keep the error message for troubleshooting."; read -r -p "Press Return to close."' ERR
python3 -c 'import sys; assert sys.version_info >= (3,12), "Please install Python 3.12 or newer from python.org."'
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
echo 'Installation complete. Open Start-Mac.command next.'
read -r -p 'Press Return to close.'
