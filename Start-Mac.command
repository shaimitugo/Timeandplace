#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo 'Please run Install-Mac.command first.'
  read -r -p 'Press Return to close.'
  exit 1
fi
echo 'Keep this window open while using the app.'
echo 'Once the server starts, open http://localhost:8000 in your browser.'
echo 'Choose Explore the local demo. Press Ctrl+C here to stop.'
.venv/bin/python -m app.server
read -r -p 'Press Return to close.'
