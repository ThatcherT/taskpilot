#!/usr/bin/env bash
# Watches a task's SSE reply stream and forwards to phone Claude.
# Usage: bash task_relay.sh <task_id> <port>
# Started by spawner_cli.py as a background process.

TASK_ID="$1"
PORT="$2"
PHONE="100.74.17.91:8788"

if [ -z "$TASK_ID" ] || [ -z "$PORT" ]; then
  echo "Usage: task_relay.sh <task_id> <port>" >&2
  exit 1
fi

# Wait for channel to be healthy before starting
for i in $(seq 1 30); do
  curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1 && break
  sleep 1
done

# Read SSE stream and forward task replies to phone
curl -sN "http://localhost:$PORT/events" | while IFS= read -r line; do
  case "$line" in
    data:*)
      msg="${line#data: }"
      # Forward to phone Claude with source=taskpilot
      payload=$(python3 -c "import json,sys; print(json.dumps({'source':'taskpilot','task_id':'$TASK_ID','body':sys.argv[1]}))" "$msg" 2>/dev/null)
      if [ -n "$payload" ]; then
        curl -s -d "$payload" "http://$PHONE" >/dev/null 2>&1
      fi
      ;;
  esac
done
