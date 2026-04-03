"""Spawner — writes config files, registers channel MCP, launches tmux session."""

import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

TASKPILOT_DIR = Path.home() / ".taskpilot"
CLAUDE_JSON = Path.home() / ".claude.json"
PLUGIN_ROOT = Path(__file__).parent
CHANNEL_TEMPLATE = PLUGIN_ROOT / "channel_template.mjs"

# Marketplace and plugin registry paths
CLAUDE_DIR = Path.home() / ".claude"
MARKETPLACE_PATH = CLAUDE_DIR / "plugins" / "marketplaces" / "nov-plugins" / ".claude-plugin" / "marketplace.json"
INSTALLED_PLUGINS_PATH = CLAUDE_DIR / "plugins" / "installed_plugins.json"
PLUGIN_CACHE_DIR = CLAUDE_DIR / "plugins" / "cache" / "nov-plugins"

# Absolute node path — nvm isn't in MCP subprocess PATH, and /usr/bin/node
# is v12 which can't run ES modules with top-level await.
# Must resolve to a node >= 18.
_node = shutil.which("node")
if _node and os.path.realpath(_node).startswith("/usr"):
    # System node is too old, find nvm version
    _node = None
if not _node:
    nvm_dir = Path.home() / ".nvm" / "versions" / "node"
    if nvm_dir.exists():
        versions = sorted(nvm_dir.iterdir(), reverse=True)
        for v in versions:
            candidate = v / "bin" / "node"
            if candidate.exists():
                _node = str(candidate)
                break
NODE_BIN = _node or "/usr/bin/node"


def slugify(name: str) -> str:
    """Convert task name to a valid slug for tmux session and task_id."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:50]


def task_dir(task_id: str) -> Path:
    return TASKPILOT_DIR / task_id


def write_task_config(
    task_id: str,
    name: str,
    description: str,
    plugins: list[str],
    operating_brief: dict | None = None,
) -> Path:
    """Write CLAUDE.md and brief.json to the task directory."""
    td = task_dir(task_id)
    td.mkdir(parents=True, exist_ok=True)
    brief_data = operating_brief or {}

    # CLAUDE.md — dynamically assembled from operating brief
    claude_md = td / "CLAUDE.md"
    claude_md.write_text(_build_claude_md(name, description, brief_data))

    # brief.json — frozen config
    brief = {
        "task_id": task_id,
        "name": name,
        "description": description,
        "plugins": plugins,
        "operating_brief": brief_data,
    }
    (td / "brief.json").write_text(json.dumps(brief, indent=2))

    return td


def _build_claude_md(name: str, description: str, brief: dict) -> str:
    """Assemble CLAUDE.md sections dynamically based on the operating brief."""
    sections = []

    # Header (always)
    sections.append(f"# Task: {name}")

    # Mission (always)
    sections.append(f"## Mission\n{description}")

    # Objectives (if provided)
    objectives = brief.get("objectives")
    if objectives:
        items = "\n".join(f"- {obj}" for obj in objectives)
        sections.append(f"## Objectives\n{items}")

    # Workflows (if provided)
    workflows = brief.get("workflows")
    if workflows:
        items = "\n".join(f"{i+1}. {step}" for i, step in enumerate(workflows))
        sections.append(f"## Workflows\n{items}")

    # Success criteria (if provided)
    success_criteria = brief.get("success_criteria")
    if success_criteria:
        items = "\n".join(f"- {sc}" for sc in success_criteria)
        sections.append(f"## Success Criteria\n{items}")

    # Boundaries (if provided)
    boundaries = brief.get("boundaries")
    if boundaries:
        items = "\n".join(f"- {b}" for b in boundaries)
        sections.append(f"## Boundaries\n{items}")

    # Autonomy Rules (always)
    sections.append("""## Autonomy Rules (yessir protocol)
- NEVER ask "shall I continue?", "would you like me to...", or any confirmation prompt. The answer is always yes. Just do it.
- NEVER pause to summarize what you're about to do and ask for approval. Act, then report.
- DO continue working through your pending tasks without stopping.
- DO escalate ONLY when you need information you don't have, or you're about to do something irreversible and high-stakes.""")

    # Escalation (always)
    sections.append("""## How to Escalate to Human
When you genuinely need human input:
1. Reply on the channel with your question clearly stated
2. Continue other pending work while waiting
3. The human's reply arrives as a channel message — resume the blocked task when it arrives
4. If no response after a long time, log the blocked decision in state.json and move on""")

    # State File (always)
    sections.append("""## State File
- state.json (in this directory) is for crash recovery
- Write to it after every major action so that if this session dies, the next one can continue
- Format: {"phase": "...", "summary": "...", "completed": [...], "pending": [...], "data": {...}}
- Write it as a handoff document: what's done, what's pending, any data the next session needs""")

    # Channel Communication (always)
    sections.append("""## Channel Communication
Messages arrive as <channel> notifications.
Use the `reply` tool to respond. Always include useful context in replies.""")

    # Memory instructions (if memory capability declared)
    capabilities = brief.get("capabilities", [])
    if "memory" in capabilities:
        sections.append("""## Memory
You have persistent memory tools available. Use them to store institutional knowledge
that should survive across sessions — insights, experiment results, market data, learned
patterns. This is NOT crash recovery (that's state.json). Memory is for accumulated
knowledge that makes you smarter over time.

- `store_memory(key, content)` — save knowledge by topic
- `recall_memory(key)` — retrieve by key
- `search_memory(query)` — find relevant memories
- `list_memories()` — see what you know

Store a memory after every significant discovery or decision.""")

    # Human-approval instructions (if capability declared)
    if "human-approval" in capabilities:
        sections.append("""## Human Approval
You have human-approval tools available. Before taking any high-stakes or irreversible
action (posting publicly, spending money, sending external communications), use
`request_approval(action, context)` and wait for confirmation before proceeding.

Check approval status with `check_approval(request_id)`. If approval times out,
skip the action and log it to state.json.""")

    # Scheduling instructions (if capability declared)
    if "scheduling" in capabilities:
        sections.append("""## Scheduling
You have scheduling tools available. Use them to set up recurring workflows that
should run on a cadence — daily research, periodic checks, content schedules.

- `schedule_task(name, plugin, skill, interval)` — create a recurring event
- `list_scheduled_tasks()` — see active schedules
- `remove_scheduled_task(name)` — cancel a schedule

Scheduled events arrive as channel messages. Process them when they arrive.""")

    # On Startup (always)
    sections.append("""## On Startup
If state.json exists, read it first to understand your previous progress, then continue with pending items.""")

    return "\n\n".join(sections) + "\n"


def _read_json(path: Path) -> dict | None:
    """Read a JSON file, return None if missing or invalid."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _is_plugin_installed(name: str) -> bool:
    """Check installed_plugins.json for a plugin by name."""
    data = _read_json(INSTALLED_PLUGINS_PATH)
    if not data:
        return False
    for key in data.get("plugins", {}):
        if key.split("@")[0] == name:
            return True
    return False


def _get_install_path(name: str) -> str | None:
    """Get the installPath for an installed plugin, or None."""
    data = _read_json(INSTALLED_PLUGINS_PATH)
    if not data:
        return None
    for key, entries in data.get("plugins", {}).items():
        if key.split("@")[0] == name and entries:
            return entries[0].get("installPath")
    return None


def _check_environment(env_reqs: dict) -> bool:
    """Check if all environment requirements are satisfied."""
    for key, value in env_reqs.items():
        values = value if isinstance(value, list) else [value]
        if key == "os":
            if not any(platform.system().lower() == v for v in values):
                return False
        elif key == "binary":
            if not any(shutil.which(v) is not None for v in values):
                return False
        elif key == "plugin":
            if not any(_is_plugin_installed(v) for v in values):
                return False
        elif key == "file":
            if not any(Path(os.path.expanduser(v)).exists() for v in values):
                return False
    return True


def _clone_plugin(name: str, repo: str, version: str = "latest") -> str | None:
    """Clone a plugin from GitHub into the standard cache path. Returns path or None."""
    target = PLUGIN_CACHE_DIR / name / version
    if target.exists():
        return str(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--depth", "1",
         f"https://github.com/{repo}.git", str(target)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        if target.exists():
            shutil.rmtree(target)
        return None
    return str(target)


def resolve_capabilities(capabilities: list[str]) -> list[str]:
    """Resolve capability names to plugin directory paths.

    For each capability:
    1. Find providers in marketplace.json (plugins with capability in 'provides')
    2. Filter by environment match (os, binary, etc.)
    3. Prefer already-installed providers
    4. If no installed provider matches, clone the best one from GitHub
    5. Return plugin directory paths

    Returns:
        List of plugin directory paths.
    """
    if not capabilities:
        return []

    marketplace = _read_json(MARKETPLACE_PATH)
    if not marketplace:
        return []

    plugins = marketplace.get("plugins", [])
    resolved_paths = []

    for cap in capabilities:
        providers = [p for p in plugins if cap in p.get("provides", [])]
        if not providers:
            continue

        # Filter by environment match, track install status
        candidates = []
        for p in providers:
            if not _check_environment(p.get("environment", {})):
                continue
            candidates.append({
                "name": p["name"],
                "source": p.get("source", {}),
                "version": p.get("version", "latest"),
                "installed_path": _get_install_path(p["name"]),
            })

        if not candidates:
            continue

        # Prefer installed providers
        candidates.sort(key=lambda c: c["installed_path"] is None)
        best = candidates[0]

        if best["installed_path"]:
            path = best["installed_path"]
        else:
            repo = best["source"].get("repo", "")
            if not repo:
                continue
            path = _clone_plugin(best["name"], repo, best["version"])
            if not path:
                continue

        if path not in resolved_paths:
            resolved_paths.append(path)

    return resolved_paths


def register_channel_mcp(task_id: str, port: int) -> None:
    """Add the task's channel MCP server to ~/.claude.json."""
    server_name = f"task-{task_id}"
    channel_path = str(CHANNEL_TEMPLATE)

    data = json.loads(CLAUDE_JSON.read_text())
    data.setdefault("mcpServers", {})
    data["mcpServers"][server_name] = {
        "command": NODE_BIN,
        "args": [channel_path],
        "env": {
            "TASKPILOT_PORT": str(port),
            "TASKPILOT_NAME": server_name,
        },
    }
    CLAUDE_JSON.write_text(json.dumps(data, indent=2))


def unregister_channel_mcp(task_id: str) -> None:
    """Remove the task's channel MCP server from ~/.claude.json."""
    server_name = f"task-{task_id}"
    data = json.loads(CLAUDE_JSON.read_text())
    data.get("mcpServers", {}).pop(server_name, None)
    CLAUDE_JSON.write_text(json.dumps(data, indent=2))


def tmux_session_name(task_id: str) -> str:
    return f"taskpilot-{task_id}"


def spawn_tmux(task_id: str, port: int, plugins: list[str], model: str | None = None) -> bool:
    """Launch the Claude session in tmux with channel."""
    session = tmux_session_name(task_id)
    server_name = f"task-{task_id}"
    td = task_dir(task_id)

    # Build plugin-dir flags
    plugin_flags = ""
    for p in plugins:
        plugin_flags += f" --plugin-dir {p}"

    # Build model flag
    model_flag = f" --model {model}" if model else ""

    # The tmux command: while-loop for crash recovery
    # rotation.py handles respawn decisions
    # Export TASKPILOT_TASK_ID so capability plugins can scope their storage
    cmd = f"""export TASKPILOT_TASK_ID={task_id}
while true; do
  cd {td} && \\
  claude --dangerously-skip-permissions \\
    --dangerously-load-development-channels server:{server_name} \\
    {plugin_flags}{model_flag} \\
    --name {task_id}
  python {PLUGIN_ROOT / 'rotation.py'} {task_id} || break
  sleep 5
done"""

    result = subprocess.run(
        ["tmux", "new-session", "-d", "-s", session, f"bash -lc '{cmd}'"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False

    # Auto-accept trust dialog
    time.sleep(7)
    subprocess.run(["tmux", "send-keys", "-t", session, "Enter"])

    # Auto-accept channels warning
    time.sleep(4)
    subprocess.run(["tmux", "send-keys", "-t", session, "Enter"])

    # Wait for channel health
    for _ in range(20):
        try:
            resp = subprocess.run(
                ["curl", "-sf", f"http://localhost:{port}/health"],
                capture_output=True,
                timeout=3,
            )
            if resp.returncode == 0:
                break
        except subprocess.TimeoutExpired:
            pass
        time.sleep(1)

    # Unregister from .claude.json now that the task session owns the MCP process.
    # This prevents other Claude sessions from stealing the channel.
    unregister_channel_mcp(task_id)

    # Brief settle time for MCP connection
    time.sleep(3)
    return True


def send_initial_prompt(port: int, description: str) -> bool:
    """POST the initial task prompt to the channel."""
    try:
        result = subprocess.run(
            ["curl", "-s", "-d", description, f"http://localhost:{port}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def kill_tmux(task_id: str) -> bool:
    """Kill the tmux session for a task."""
    session = tmux_session_name(task_id)
    result = subprocess.run(
        ["tmux", "kill-session", "-t", session],
        capture_output=True,
    )
    return result.returncode == 0


def is_tmux_alive(task_id: str) -> bool:
    """Check if the tmux session is running."""
    session = tmux_session_name(task_id)
    result = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
    )
    return result.returncode == 0


def channel_healthy(port: int) -> bool:
    """Check if the channel HTTP server is responding."""
    try:
        result = subprocess.run(
            ["curl", "-sf", f"http://localhost:{port}/health"],
            capture_output=True,
            timeout=3,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
