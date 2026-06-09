#!/usr/bin/env bash
# Stop the dashboard server.
if pkill -f "python3 app.py"; then
  echo "Dashboard stopped."
else
  echo "Dashboard was not running."
fi
