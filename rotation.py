#!/usr/bin/env python3
"""Context rotation decision script.

Called after Claude exits in the tmux while-loop.
Exit 0 = respawn (continue). Exit 1 = stop (break).

The Stop hook (hooks/on-stop.py) handles the common path: when the agent
declares completion mid-session, the hook marks the DB completed and kills
tmux. By the time we run, the DB status check below is enough to break
the loop.

This script remains as a safety net for crash paths where the hook didn't
get a chance to fire — Claude exited before completing a turn (segfault,
OOM, /exit). In that case we re-classify the most recent recorded turn.

Decision sources, in priority order:
  1. DB status — killed/paused/completed tasks never respawn.
  2. state.json (`phase` written by the agent itself) — explicit completion.
  3. state/agent.json `last_stop` — classifier says resolved.
  4. Default — respawn.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import classifier
import store


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _explicit_completion(task_id: str) -> bool:
    """state.json with phase=done|completed → the agent declared itself done."""
    state = _read_json(Path.home() / ".taskpilot" / task_id / "state.json")
    if not state:
        return False
    phase = (state.get("phase") or "").lower()
    return phase in ("done", "completed")


def _implicit_completion(task_id: str) -> bool:
    """Classifier on the last recorded Stop says resolved."""
    agent = _read_json(Path.home() / ".taskpilot" / task_id / "state" / "agent.json")
    if not agent:
        return False
    last_stop = agent.get("last_stop") or {}
    msg = last_stop.get("last_assistant_message") or ""
    return classifier.classify(msg) == "resolved"


def _mark_completed(task_id: str) -> None:
    conn = store.get_db()
    store.update_status(conn, task_id, "completed")
    conn.close()


def should_respawn(task_id: str) -> bool:
    conn = store.get_db()
    task = store.get_task(conn, task_id)
    conn.close()

    if not task:
        return False

    # Externally killed/paused — never respawn.
    if task["status"] not in ("running",):
        return False

    if _explicit_completion(task_id) or _implicit_completion(task_id):
        _mark_completed(task_id)
        return False

    # Increment invocation count and continue.
    conn = store.get_db()
    store.increment_invocation(conn, task_id)
    conn.close()
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <task_id>", file=sys.stderr)
        sys.exit(1)

    task_id = sys.argv[1]
    if should_respawn(task_id):
        sys.exit(0)  # continue loop
    else:
        sys.exit(1)  # break loop
