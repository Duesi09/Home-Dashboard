#!/usr/bin/env bash
# Start the dashboard so it keeps running even after you close the terminal.
cd "$(dirname "$0")" || exit 1

# already running? bail out
if pgrep -f "python3 app.py" >/dev/null; then
  echo "Dashboard already running -> http://localhost:5000"
  exit 0
fi

# setsid detaches it into its own session so it survives the terminal closing
setsid bash -c 'PYTHONPATH=./libs python3 app.py > server.log 2>&1' < /dev/null &
disown

sleep 1
if pgrep -f "python3 app.py" >/dev/null; then
  echo "Dashboard started -> http://localhost:5000"
  echo "Logs: tail -f server.log   |   Stop: ./stop.sh"
else
  echo "Failed to start. Check server.log:"
  tail -n 20 server.log
fi
