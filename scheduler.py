"""Cron-based recurring events for taskpilot agents.

A "schedule" here is a crontab entry that POSTs a synthetic message to a
running agent on a cadence. The agent receives the message via session-bridge
and decides what to do — scheduling is just a trigger, not a job runner.

Three things happen for each schedule:

1. A crontab line is written/removed via `crontab -l` / `crontab -`.
2. A registry file at `~/.taskpilot/schedules.json` mirrors the crontab so
   we can list/remove without re-parsing the crontab.
3. Each crontab line carries a tag comment `# taskpilot-schedule:<task_id>:<name>`
   so we can identify and remove our own entries without touching the user's.

Pure functions; no MCP, no DB. The MCP tool wrappers in server.py decorate
these and inject TASKPILOT_TASK_ID from the agent's env.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import spawner

SCHEDULES_FILE = Path.home() / ".taskpilot" / "schedules.json"


# --- Registry IO ---


def _read_schedules() -> dict:
    if not SCHEDULES_FILE.exists():
        return {}
    try:
        return json.loads(SCHEDULES_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_schedules(schedules: dict) -> None:
    SCHEDULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULES_FILE.write_text(json.dumps(schedules, indent=2))


# --- Crontab IO ---


def _get_current_crontab() -> str:
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5,
        )
        return result.stdout if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _set_crontab(content: str) -> bool:
    try:
        result = subprocess.run(
            ["crontab", "-"], input=content, capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _tag(task_id: str, name: str) -> str:
    """Crontab line marker so we can find our own entries on remove."""
    return f"# taskpilot-schedule:{task_id}:{name}"


# --- Interval parsing ---


def human_to_cron(interval: str) -> str | None:
    """Convert human-readable intervals to cron expressions.

    Supports cron expressions (5 fields), "every Xm/Xh/Xd", "daily", "hourly", "weekly".
    Returns None if the input doesn't match any pattern.
    """
    interval = interval.strip()
    if len(interval.split()) == 5:
        return interval

    lower = interval.lower()
    if lower == "daily":
        return "0 9 * * *"
    if lower == "hourly":
        return "0 * * * *"
    if lower == "weekly":
        return "0 9 * * 1"

    if lower.startswith("every "):
        spec = lower[6:].strip()
        if spec.endswith("m"):
            try:
                return f"*/{int(spec[:-1])} * * * *"
            except ValueError:
                pass
        elif spec.endswith("h"):
            try:
                return f"0 */{int(spec[:-1])} * * *"
            except ValueError:
                pass
        elif spec.endswith("d"):
            try:
                return f"0 9 */{int(spec[:-1])} * *"
            except ValueError:
                pass
    return None


# --- Public API (called from server.py MCP wrappers) ---


def schedule(task_id: str, name: str, plugin: str, skill: str, interval: str, enabled: bool = True) -> dict:
    """Create or update a schedule for a task. Returns a result dict."""
    cron_expr = human_to_cron(interval)
    if not cron_expr:
        return {"error": f"Invalid interval: '{interval}'. Use cron (5 fields), 'every Xm/Xh/Xd', 'daily', 'hourly', or 'weekly'."}

    tag = _tag(task_id, name)
    message = f"[scheduled:{name}] Time to run {skill} (plugin: {plugin})"
    payload = json.dumps({"text": message, "from_session": "cron"})
    target_url = f"{spawner.SESSION_BRIDGE_URL}/sessions/{task_id}/message"
    cron_line = (
        f"{cron_expr} curl -s -X POST -H 'Content-Type: application/json' "
        f"-d {json.dumps(payload)} {target_url} > /dev/null 2>&1 {tag}"
    )

    current = _get_current_crontab()
    lines = [l for l in current.splitlines() if tag not in l]
    if enabled:
        lines.append(cron_line)
    new_crontab = "\n".join(lines)
    if new_crontab and not new_crontab.endswith("\n"):
        new_crontab += "\n"
    if not _set_crontab(new_crontab):
        return {"error": "Failed to update crontab"}

    schedules = _read_schedules()
    schedules[f"{task_id}:{name}"] = {
        "name": name,
        "task_id": task_id,
        "plugin": plugin,
        "skill": skill,
        "interval": interval,
        "cron_expr": cron_expr,
        "target_url": target_url,
        "enabled": enabled,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_schedules(schedules)

    return {
        "scheduled": True,
        "name": name,
        "cron_expr": cron_expr,
        "target_url": target_url,
        "message": message,
        "enabled": enabled,
    }


def list_for_task(task_id: str) -> dict:
    """List schedules belonging to a task."""
    schedules = _read_schedules()
    task_schedules = [s for s in schedules.values() if s.get("task_id") == task_id]
    return {"task_id": task_id, "count": len(task_schedules), "schedules": task_schedules}


def remove(task_id: str, name: str) -> dict:
    """Remove a schedule by (task_id, name) — both crontab and registry."""
    tag = _tag(task_id, name)

    current = _get_current_crontab()
    lines = [l for l in current.splitlines() if tag not in l]
    new_crontab = "\n".join(lines)
    if new_crontab and not new_crontab.endswith("\n"):
        new_crontab += "\n"
    _set_crontab(new_crontab)

    schedules = _read_schedules()
    key = f"{task_id}:{name}"
    removed = key in schedules
    schedules.pop(key, None)
    _write_schedules(schedules)

    return {"removed": removed, "name": name, "task_id": task_id}
