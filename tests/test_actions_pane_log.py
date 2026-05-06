"""Tests for actions.mark_completed_and_kill — pane.log flush branching.

The discriminator between steady and legacy paths is the existence of
pane.log.attached (the sentinel). This is the v3-bug regression fence.
"""
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import actions
import spawner


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Redirect taskpilot dirs into a temp tree."""
    monkeypatch.setattr(spawner, "TASKPILOT_DIR", tmp_path)
    monkeypatch.setattr(actions, "TASKPILOT_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def fake_store(monkeypatch):
    fake = MagicMock()
    fake_conn = MagicMock()
    fake.get_db.return_value = fake_conn
    monkeypatch.setattr(actions, "store", fake)
    return fake


def _make_task(isolated, task_id, sentinel=False, pane_log=False):
    td = isolated / task_id
    td.mkdir(parents=True, exist_ok=True)
    if pane_log:
        (td / "pane.log").write_text("prior content\n")
    if sentinel:
        (td / "pane.log.attached").touch()


# --- Steady path (sentinel present) ------------------------------------------

def test_steady_path_calls_toggle_off(isolated, fake_store):
    _make_task(isolated, "t1", sentinel=True, pane_log=True)
    calls = []

    def mock_run(*args, **kwargs):
        calls.append(("run", args[0]))
        return MagicMock(returncode=0)

    def mock_popen(*args, **kwargs):
        calls.append(("popen", args[0]))
        return MagicMock()

    with patch("actions.subprocess.run", side_effect=mock_run), \
         patch("actions.subprocess.Popen", side_effect=mock_popen), \
         patch("actions.time.sleep"):
        actions.mark_completed_and_kill("t1")

    # Look for: pipe-pane toggle off (no command argument), then kill-session
    pipe_pane_call = next(
        (c for c in calls if c[0] == "run" and c[1][:3] == ["tmux", "pipe-pane", "-t"]),
        None,
    )
    assert pipe_pane_call is not None
    assert pipe_pane_call[1] == ["tmux", "pipe-pane", "-t", "t1:0.0"]

    # No capture-pane in steady path
    assert not any(
        c[0] == "run" and "capture-pane" in c[1]
        for c in calls
    )

    # kill-session via Popen
    assert any(
        c[0] == "popen" and "kill-session" in c[1]
        for c in calls
    )


def test_steady_path_unlinks_sentinel(isolated, fake_store):
    _make_task(isolated, "t1", sentinel=True, pane_log=True)
    sentinel = spawner.pane_log_sentinel("t1")
    assert sentinel.exists()

    with patch("actions.subprocess.run", return_value=MagicMock(returncode=0)), \
         patch("actions.subprocess.Popen"), \
         patch("actions.time.sleep"):
        actions.mark_completed_and_kill("t1")

    assert not sentinel.exists()


def test_steady_path_writes_completion_separator(isolated, fake_store):
    _make_task(isolated, "t1", sentinel=True, pane_log=True)

    with patch("actions.subprocess.run", return_value=MagicMock(returncode=0)), \
         patch("actions.subprocess.Popen"), \
         patch("actions.time.sleep"):
        actions.mark_completed_and_kill("t1")

    log = spawner.pane_log_path("t1").read_text()
    assert "=== completed at " in log


# --- Legacy path (sentinel absent) -------------------------------------------

def test_legacy_path_calls_capture_pane(isolated, fake_store):
    _make_task(isolated, "t1", sentinel=False, pane_log=False)
    calls = []

    def mock_run(*args, **kwargs):
        calls.append(("run", args[0]))
        # Make capture-pane look successful with no output
        return MagicMock(returncode=0)

    def mock_popen(*args, **kwargs):
        calls.append(("popen", args[0]))
        return MagicMock()

    with patch("actions.subprocess.run", side_effect=mock_run), \
         patch("actions.subprocess.Popen", side_effect=mock_popen):
        actions.mark_completed_and_kill("t1")

    # capture-pane was invoked
    assert any(
        c[0] == "run" and "capture-pane" in c[1]
        for c in calls
    )
    # pipe-pane toggle-off was NOT (no sentinel)
    assert not any(
        c[0] == "run" and c[1][:3] == ["tmux", "pipe-pane", "-t"]
        for c in calls
    )


def test_legacy_path_writes_legacy_header(isolated, fake_store):
    _make_task(isolated, "t1", sentinel=False, pane_log=False)
    with patch("actions.subprocess.run", return_value=MagicMock(returncode=0)), \
         patch("actions.subprocess.Popen"):
        actions.mark_completed_and_kill("t1")
    log = spawner.pane_log_path("t1").read_text()
    assert "=== legacy capture (no live tee) at " in log
    assert "=== completed at " in log


def test_legacy_path_mode_0600_even_on_capture_failure(isolated, fake_store):
    """If capture-pane raises mid-write, the file must still be 0600.

    Regression test for the chmod-after-write race in v3.
    """
    if os.name != "posix":
        pytest.skip("POSIX-only")
    _make_task(isolated, "t1", sentinel=False, pane_log=False)

    # First subprocess.run call (capture-pane) raises; the actions code wraps
    # _legacy_capture in try/except so this should NOT propagate.
    def run_side_effect(*args, **kwargs):
        if "capture-pane" in args[0]:
            raise subprocess.TimeoutExpired(cmd="tmux", timeout=5)
        return MagicMock(returncode=0)

    with patch("actions.subprocess.run", side_effect=run_side_effect), \
         patch("actions.subprocess.Popen"):
        actions.mark_completed_and_kill("t1")

    log = spawner.pane_log_path("t1")
    assert log.exists()
    assert (log.stat().st_mode & 0o777) == 0o600


def test_failed_attach_scenario_takes_legacy_path(isolated, fake_store):
    """Spawn wrote separator but pipe-pane failed → no sentinel → legacy path.

    This is the v3 bug regression test. If the discriminator drifts back to
    pane.log.exists(), this test fails because the bad code routes to steady.
    """
    _make_task(isolated, "t1", sentinel=False, pane_log=True)  # pane.log exists, sentinel does NOT
    calls = []

    def mock_run(*args, **kwargs):
        calls.append(("run", args[0]))
        return MagicMock(returncode=0)

    with patch("actions.subprocess.run", side_effect=mock_run), \
         patch("actions.subprocess.Popen"):
        actions.mark_completed_and_kill("t1")

    # MUST take legacy path (capture-pane), not steady (pipe-pane toggle-off)
    assert any("capture-pane" in c[1] for c in calls if c[0] == "run")
    assert not any(
        c[0] == "run" and c[1][:3] == ["tmux", "pipe-pane", "-t"]
        for c in calls
    )


# --- Failure modes never block the kill --------------------------------------

def test_kill_runs_even_when_toggle_off_fails(isolated, fake_store):
    _make_task(isolated, "t1", sentinel=True, pane_log=True)
    popen_calls = []

    def mock_run(*args, **kwargs):
        if args[0][:3] == ["tmux", "pipe-pane", "-t"]:
            raise OSError("toggle off failed")
        return MagicMock(returncode=0)

    with patch("actions.subprocess.run", side_effect=mock_run), \
         patch("actions.subprocess.Popen") as popen, \
         patch("actions.time.sleep"):
        actions.mark_completed_and_kill("t1")

    # kill-session was called regardless
    assert any("kill-session" in args[0] for args, _ in popen.call_args_list)


def test_kill_runs_even_when_capture_fails(isolated, fake_store):
    _make_task(isolated, "t1", sentinel=False, pane_log=False)
    with patch("actions.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="t", timeout=5)), \
         patch("actions.subprocess.Popen") as popen:
        actions.mark_completed_and_kill("t1")
    assert any("kill-session" in args[0] for args, _ in popen.call_args_list)


def test_db_status_updated_regardless_of_flush_failures(isolated, fake_store):
    _make_task(isolated, "t1", sentinel=True, pane_log=True)
    with patch("actions.subprocess.run", side_effect=OSError("everything fails")), \
         patch("actions.subprocess.Popen"), \
         patch("actions.time.sleep"):
        actions.mark_completed_and_kill("t1")

    fake_store.update_status.assert_called_once_with(fake_store.get_db.return_value, "t1", "completed")


# --- Ordering -----------------------------------------------------------------

def test_steady_path_call_order(isolated, fake_store):
    """Verify the order: status update → toggle off → sleep → separator → kill."""
    _make_task(isolated, "t1", sentinel=True, pane_log=True)
    events = []

    def status_side(*args, **kwargs):
        events.append("status")

    def run_side(*args, **kwargs):
        if args[0][:3] == ["tmux", "pipe-pane", "-t"]:
            events.append("toggle-off")
        return MagicMock(returncode=0)

    def sleep_side(_):
        events.append("sleep")

    def popen_side(*args, **kwargs):
        if "kill-session" in args[0]:
            events.append("kill")
        return MagicMock()

    fake_store.update_status.side_effect = status_side

    with patch("actions.subprocess.run", side_effect=run_side), \
         patch("actions.subprocess.Popen", side_effect=popen_side), \
         patch("actions.time.sleep", side_effect=sleep_side):
        actions.mark_completed_and_kill("t1")

    # Expected order: status, toggle-off, sleep, kill
    assert events == ["status", "toggle-off", "sleep", "kill"]
