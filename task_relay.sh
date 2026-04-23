#!/usr/bin/env bash
# Watches a task's SSE reply stream and forwards to phone Claude.
# Usage: bash task_relay.sh <task_id>
# Started by spawner_cli.py as a background process.
#
# Looks up the session's channel port from session-bridge — the task's
# claude launches with --name <task_id>, and session-bridge auto-names
# it at register time. We poll /sessions/<task_id> until it reports a
# channel_port, then tail that port's /events SSE stream.

TASK_ID="$1"
PHONE="100.74.17.91:8788"
SESSION_BRIDGE="http://127.0.0.1:8910"

if [ -z "$TASK_ID" ]; then
  echo "Usage: task_relay.sh <task_id>" >&2
  exit 1
fi

# Wait for session-bridge to have a channel port for this task
PORT=""
for i in $(seq 1 60); do
  PORT=$(curl -sf -m 2 "$SESSION_BRIDGE/sessions/$TASK_ID" \
         | python3 -c "import json, sys; d = json.load(sys.stdin); p = d.get('channel_port'); print(p or '', end='')" 2>/dev/null)
  if [ -n "$PORT" ]; then
    break
  fi
  sleep 1
done

if [ -z "$PORT" ]; then
  echo "task_relay: no channel_port for $TASK_ID after 60s" >&2
  exit 1
fi

# Read SSE stream and forward task replies to phone
curl -sN "http://localhost:$PORT/events" | while IFS= read -r line; do
  case "$line" in
    data:*)
      msg="${line#data: }"
      payload=$(python3 -c "import json,sys; print(json.dumps({'source':'taskpilot','task_id':'$TASK_ID','body':sys.argv[1]}))" "$msg" 2>/dev/null)
      if [ -n "$payload" ]; then
        curl -s -d "$payload" "http://$PHONE" >/dev/null 2>&1
      fi
      ;;
  esac
done
