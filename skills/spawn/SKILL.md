---
name: spawn
description: Spawn a long-running autonomous Claude Code session for a background task
version: 0.2.0
---

# /taskpilot:spawn

Spawn a new autonomous agent session.

## Workflow

1. **Understand the task.** Ask the user what they want done. Get a clear description.

2. **Determine kind.** Should this agent survive reboots?
   - **`kind="task"`** (default): One-shot jobs that run, complete, and die. Use for tests, migrations, one-time research, build tasks.
   - **`kind="service"`**: Always-on agents that auto-restart on reboot via systemd. Use for persistent agents (vault knowledge base, email triage, monitoring), anything the user describes as "always running", "persistent", or "keep alive".

3. **Build the operating brief.** Based on the task complexity, gather additional context:
   - **Objectives**: What are the measurable goals? (e.g., "identify 5 profitable niches", "post 3x/week")
   - **Workflows**: What ordered steps/phases should the agent follow?
   - **Success criteria**: How do we know the task is done?
   - **Boundaries**: What should the agent NOT do? (e.g., "don't spend money", "don't post without approval")
   - **Capabilities**: What capabilities does the agent need? Available:
     - `memory` — persistent knowledge across sessions
     - `scheduling` — cron-driven recurring events
     - `human-approval` — gate actions behind human confirmation
     - `notification` — alert the user
   - **Schedule**: If this is a recurring agent, what's the cadence? (cron expression)

   For simple tasks, the brief can be minimal. For long-running business agents, fill out as much as makes sense.

4. **Determine plugins needed.** Based on the task, identify which plugins the spawned session needs access to. Capabilities are auto-resolved via nov-dependency-resolver — you only need to specify plugins that aren't covered by the capability system.

5. **Choose model (if requested).** If the user wants a specific model, pass it as the `model` parameter. Valid values: `"sonnet"`, `"opus"`, `"haiku"`, or a full model ID like `"claude-sonnet-4-6"`. If not specified, the agent uses the default model.

6. **Create the task.** Call `create_task(name, description, plugins, operating_brief, model, kind)` from the taskpilot MCP. The operating brief is a dict with keys: objectives, workflows, success_criteria, boundaries, capabilities, schedule.

7. **Spawn the task.** Call `spawn_task(task_id)`. This takes ~16 seconds for tasks, ~40 seconds for services (systemd + tmux + channel init).

8. **Confirm.** Tell the user:
   - The task is running
   - The tmux session name (they can `tmux attach -t <name>` to watch)
   - The channel port (they can `curl -s -d 'message' http://localhost:<port>` to send messages)
   - What capabilities were resolved and loaded
   - How to check status: `/taskpilot:status`
   - How to manage: `/taskpilot:manage`
   - For services: the systemd unit name (`systemctl --user status taskpilot-<id>`) and that it will auto-restart on reboot
