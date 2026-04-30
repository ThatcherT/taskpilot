# CLAUDE.md — taskpilot

Spawn and manage long-running autonomous Claude Code sessions. Each task runs in its own tmux session, addressable through session-bridge by its task id.

## Quick Reference

| Command | What it does |
|---------|-------------|
| `/taskpilot:spawn` | Create and launch a new autonomous task |
| `/taskpilot:status` | Dashboard of all tasks with health status |
| `/taskpilot:manage` | Send messages, view logs, kill tasks |

## Stack

- Python 3.11+, FastMCP, SQLite
- tmux (session management)
- session-bridge (message routing)

## How It Works

1. `create_task()` writes config to `~/.taskpilot/<id>/`.
2. `spawn_task()` launches the agent:
   - **`kind=task`** (default): launches directly in tmux.
   - **`kind=service`**: generates `start.sh` + systemd user service, survives reboots.
3. Claude is launched with `--name <task_id>`. session-bridge's `channel.mjs` parses that flag from `/proc/<ppid>/cmdline` at MCP boot and registers the session under that name — no agent-side `set_name` call required.
4. Claude is also launched with `--settings <task_dir>/hook-settings.json` so per-task `Stop` and `Notification` hooks fire (see "Lifecycle Hooks" below).
5. The initial task prompt is POSTed to `http://127.0.0.1:8910/sessions/<task_id>/message`.
6. External callers (taskboard "msg" button, cron schedules) send messages the same way.
7. On crash or exit, the while-loop in tmux respawns via `rotation.py`.
8. Task completes when either the agent writes `"phase": "done"` to state.json, or its final assistant message matches the completion regex in `rotation.py`.

## Lifecycle Hooks

Spawned agents run with two Claude Code hooks registered via `--settings`:

- **`Stop`** → `hooks/on-stop.py` — fires when the assistant finishes a turn. Records `last_assistant_message`, timestamp, and session id to `state/agent.json`, then classifies and acts.
- **`Notification`** → `hooks/on-notification.py` — fires when Claude has been idle at a prompt past ~6s. Records `notification_type` (`permission_prompt` / `elicitation_dialog` / `elicitation_url_dialog`) plus message and title.

Both hooks share `hooks/_record.py` for the read-modify-write of `agent.json` and the `events.jsonl` audit log. They no-op when `TASKPILOT_TASK_ID` is unset, which keeps them safe if the settings file is loaded outside a taskpilot context.

### Stop hook classify + act

`classifier.py` buckets the final assistant message:

| Bucket | Trigger | Action (in `actions.py`) |
|---|---|---|
| `resolved` | message tail matches `COMPLETION_PATTERNS` | `mark_completed_and_kill` — flips DB to `completed`, detached `tmux kill-session` |
| `question` | message tail ends in `?` | `notify_human` — appends to `escalations.jsonl`, shells out to `$TASKPILOT_NOTIFY_CMD` if set |
| `uneventful` | neither | no-op; agent stays at the prompt |

`$TASKPILOT_NOTIFY_CMD` is the user's plug point for whichever notification transport they want (Slack webhook, phone bridge, `notify-send`, etc.). The script gets `TASKPILOT_TASK_ID` and `TASKPILOT_MESSAGE` in env. Resolved bucket false-positives cause premature completion, so the regex is conservative; question bucket false-positives only cost a stray notification, so the rule is loose.

### rotation.py as safety net

The Stop hook handles the common path. `rotation.py` only runs after Claude actually exits, and serves as a backstop when the hook didn't fire (crash, OOM, `/exit` without an explicit completion message):

1. DB status not `running` → never respawn (covers the hook's mark-completed path).
2. `state.json` has `phase` in `done|completed` → mark completed.
3. Re-runs `classifier.classify` on the last recorded Stop → if `resolved`, mark completed.
4. Default → respawn.

The `Notification` record is written but not yet acted on. Next layer: auto-yes for permission prompts within the agent's tool scope; escalate elicitation dialogs through `notify_human`.

## Service Persistence

Agents created with `kind="service"` get a systemd user service (`taskpilot-<id>.service`) that auto-starts on boot. Systemd runs `start.sh`, which launches tmux + Claude with a `while tmux has-session` tail loop for lifecycle tracking.

- Start script: `~/.taskpilot/<id>/start.sh`
- Systemd unit: `~/.config/systemd/user/taskpilot-<id>.service`
- Check status: `systemctl --user status taskpilot-<id>`
- `kill_task()` stops and disables the systemd service

## Architecture

- Messaging routes through the session-bridge daemon at `http://127.0.0.1:8910`.
- Project-scoped MCPs from the task's `cwd/.claude/settings.json` are registered into `~/.claude.json` at launch (and cleaned up on kill via `project_mcps.json`).
- Trust dialog + channels warning auto-accepted via `tmux send-keys Enter`.

## Data

- Database: `~/.taskpilot/taskpilot.db` (SQLite with WAL mode)
- Task configs: `~/.taskpilot/<task_id>/` (CLAUDE.md, state.json, brief.json)

## Development

```bash
pip install "mcp[cli]"
python server.py                # run MCP server
make test                       # run tests
```

Install as plugin:
```bash
claude --plugin-dir /home/thatcher/projects/softwaresoftware/projects/plugins/providers/taskpilot
```

## MCP Tools

- `create_task(name, description, plugins?, operating_brief?, model?, kind?, host?)` — create task config + allocate port. kind="service" for reboot-persistent agents. host="<peer>" to launch the agent on a remote mesh host (forwards spawn to that peer's session-bridge `/spawn`).
- `spawn_task(task_id)` — launch tmux session (~16s startup). When the task carries `host` and that host is not self, forwards to the peer's `POST /spawn` instead.
- `list_tasks(status?)` — list all tasks with live health
- `get_task(task_id)` — full detail + state.json
- `send_message(task_id, message)` — POST to channel
- `kill_task(task_id)` — kill tmux + clean up
- `get_task_log(task_id, lines?)` — capture tmux pane output
- `schedule_task(name, plugin, skill, interval, enabled?)` — create/update a cron schedule
- `list_scheduled_tasks()` — list schedules for current task
- `remove_scheduled_task(name)` — remove a schedule and its crontab entry

## Operating Brief

The `operating_brief` parameter to `create_task` accepts a dict with:

| Key | Type | Purpose |
|-----|------|---------|
| `objectives` | list[str] | Measurable goals |
| `workflows` | list[str] | Ordered phases/steps |
| `success_criteria` | list[str] | Completion conditions |
| `boundaries` | list[str] | What NOT to do |
| `capabilities` | list[str] | Required capabilities (auto-resolved via nov-dependency-resolver) |
| `schedule` | str | Cron expression for recurring agents (scheduling is built-in) |

Capabilities declared in the brief are automatically resolved to provider plugins via nov-dependency-resolver at task creation time. The agent's CLAUDE.md is dynamically generated with sections for each declared capability.

Environment variable `TASKPILOT_TASK_ID` is exported in the tmux session so capability plugins can scope their storage per-task.
