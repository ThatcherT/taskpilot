# taskpilot v0.8.1 backlog

Open items deferred from v0.8.0 (persistent pane logs).

## Reconciler-side mid-flight rotation

**Problem**: a `kind=service` agent that runs for weeks without crashing grows `pane.log` unboundedly. The v0.8.0 size cap only enforces at spawn boundaries (which only fire on respawn).

**Approach**: extend the reconciler's per-tick walk (`daemon.reconcile_once`) to check `pane.log` size on alive tasks. When over cap, perform a toggle-off → truncate → re-attach dance (must be atomic w.r.t. concurrent pipe-pane writer). Or rotate to `pane.log.1`, `pane.log.2`, ... and let `tail_lines` walk multiple files.

**Tests needed**:
- Mid-flight rotation does not lose bytes (writer-during-rotate test).
- `get_task_log` returns recent content from the active file even after rotation.
- Configurable max-rotation-count to prevent disk fill from rotating-in-place.

## Daemon `/health` version alignment

`daemon.py` reports `version="0.1.0"` from its `/health` endpoint while the plugin is at `0.8.0`. The values represent different things (HTTP API surface vs plugin version) but the divergence is confusing. Either:

1. Read the plugin version from `.claude-plugin/plugin.json` at daemon startup and report that.
2. Remove the version field from `/health` and rely on the plugin manifest as source of truth.

## Cleanup `project_mcps` on completion

`actions.mark_completed_and_kill` does not call `spawner.cleanup_project_mcps(task_id)`, but `daemon.kill` does. Latent bug: completed tasks leave their project MCPs registered in `~/.claude.json` until manual cleanup or `destroy_task` runs.

Fix: add the `cleanup_project_mcps` call to the completion path. Add a test asserting symmetry between `kill` and `complete` for MCP cleanup.

## Per-invocation log segments (optional)

Today all invocations of a service share one `pane.log` with separator lines. Some users may prefer `pane.1.log`, `pane.2.log`, ... Decide based on user feedback after v0.8.0.
