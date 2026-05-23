#!/bin/bash
set -e

cd "$(dirname "$0")"

# Kill existing process on port 8003 (SIGTERM first, SIGKILL fallback)
PIDS=$(lsof -ti :8003 || true)
if [ -n "$PIDS" ]; then
  echo "Stopping PIDs: $(echo $PIDS | tr '\n' ' ')..."
  echo "$PIDS" | xargs kill -15

  # Wait up to 5 seconds for graceful shutdown
  for i in 1 2 3 4 5; do
    REMAINING=$(lsof -ti :8003 || true)
    if [ -z "$REMAINING" ]; then
      break
    fi
    sleep 1
  done

  # Force kill if still alive
  REMAINING=$(lsof -ti :8003 || true)
  if [ -n "$REMAINING" ]; then
    echo "Force killing remaining PIDs: $(echo $REMAINING | tr '\n' ' ')..."
    echo "$REMAINING" | xargs kill -9
    sleep 1
  fi
fi

# Build frontend (set -e ensures we exit on failure)
echo "Building frontend..."
cd frontend
if [ ! -d node_modules ]; then
  npm ci
fi
npm run build --silent
cd ..

# Start server
echo "Starting QuantGPT on :8003..."
mkdir -p logs
PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi
nohup "$PYTHON_BIN" -u -m quantgpt --transport http </dev/null > logs/server.log 2>&1 &
echo "PID: $!"
echo "Logs: logs/server.log"
