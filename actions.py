"""Side-effecting actions taken in response to classified agent state.

Called from hooks/on-stop.py. Each function is fire-and-forget where
possible — failures should never crash the hook.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import store

TASKPILOT_DIR = Path.home() / ".taskpilot"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mark_completed_and_kill(task_id: str) -> None:
    """Mark the task completed in the DB and tear down its tmux session.

    Detaches the tmux kill so it survives our own death — the hook is running
    inside the agent's process tree, and tearing tmux down here will SIGHUP
    the chain that includes us.
    """
    try:
        conn = store.get_db()
        store.update_status(conn, task_id, "completed")
        conn.close()
    except Exception:
        pass

    try:
        subprocess.Popen(
            ["tmux", "kill-session", "-t", task_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def notify_human(task_id: str, message: str) -> None:
    """Record an escalation and (optionally) shell out to a notification command.

    Always appends to ~/.taskpilot/<task_id>/escalations.jsonl — durable,
    pollable by taskboard / dashboards / human eyeballs.

    If TASKPILOT_NOTIFY_CMD is set in the agent's env, we run it detached
    with TASKPILOT_TASK_ID and TASKPILOT_MESSAGE exported. Lets the user
    plug in any notification transport (Slack webhook, phone bridge,
    notify-send, etc.) without taskpilot needing to know which.
    """
    record = {
        "task_id": task_id,
        "at": _now_iso(),
        "message": message,
    }

    log_path = TASKPILOT_DIR / task_id / "escalations.jsonl"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass

    cmd = os.environ.get("TASKPILOT_NOTIFY_CMD")
    if not cmd:
        return

    try:
        env = {**os.environ, "TASKPILOT_TASK_ID": task_id, "TASKPILOT_MESSAGE": message}
        subprocess.Popen(
            ["sh", "-c", cmd],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass
