"""Tests for the three-tier get_log read path in daemon.py and server.py."""
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import daemon as taskpilot_daemon
import server as taskpilot_server
import spawner

from fastapi import HTTPException


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(spawner, "TASKPILOT_DIR", tmp_path)
    return tmp_path


def _proc(returncode=0, stdout="", stderr=""):
    p = MagicMock(spec=subprocess.CompletedProcess)
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


# --- daemon.get_log ----------------------------------------------------------

def test_daemon_returns_tmux_when_alive(isolated):
    with patch("daemon.spawner.is_tmux_alive", return_value=True), \
         patch("daemon.subprocess.run", return_value=_proc(0, stdout="line1\nline2\nline3\n")):
        result = taskpilot_daemon.get_log("t1", lines=2)
    assert result["task_id"] == "t1"
    assert result["source"] == "tmux"
    assert result["output"] == "line2\nline3"


def test_daemon_returns_pane_log_when_tmux_dead(isolated):
    td = isolated / "t1"
    td.mkdir()
    (td / "pane.log").write_text("a\nb\nc\nd\ne\n")
    with patch("daemon.spawner.is_tmux_alive", return_value=False):
        result = taskpilot_daemon.get_log("t1", lines=2)
    assert result["task_id"] == "t1"
    assert result["source"] == "pane.log"
    assert result["output"] == "d\ne"


def test_daemon_404_when_tmux_dead_and_no_file(isolated):
    with patch("daemon.spawner.is_tmux_alive", return_value=False), \
         pytest.raises(HTTPException) as excinfo:
        taskpilot_daemon.get_log("t1")
    assert excinfo.value.status_code == 404
    assert "no log available" in excinfo.value.detail


def test_daemon_falls_through_when_capture_fails(isolated):
    """tmux alive but capture-pane returns nonzero → fall through to file."""
    td = isolated / "t1"
    td.mkdir()
    (td / "pane.log").write_text("from-file\n")
    with patch("daemon.spawner.is_tmux_alive", return_value=True), \
         patch("daemon.subprocess.run", return_value=_proc(1, stderr="boom")):
        result = taskpilot_daemon.get_log("t1")
    assert result["source"] == "pane.log"


def test_daemon_falls_through_when_capture_times_out(isolated):
    td = isolated / "t1"
    td.mkdir()
    (td / "pane.log").write_text("from-file\n")
    with patch("daemon.spawner.is_tmux_alive", return_value=True), \
         patch("daemon.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=5)):
        result = taskpilot_daemon.get_log("t1")
    assert result["source"] == "pane.log"


def test_daemon_pane_log_uses_capture_pane_with_pinned_pane(isolated):
    """Verify capture-pane is invoked with -t <session>:0.0 (not bare session)."""
    with patch("daemon.spawner.is_tmux_alive", return_value=True), \
         patch("daemon.subprocess.run", return_value=_proc(0, stdout="x\n")) as run:
        taskpilot_daemon.get_log("t1")
    args = run.call_args[0][0]
    assert "t1:0.0" in args


def test_daemon_lines_param_respected_against_file(isolated):
    td = isolated / "t1"
    td.mkdir()
    (td / "pane.log").write_text("\n".join(f"line{i}" for i in range(20)) + "\n")
    with patch("daemon.spawner.is_tmux_alive", return_value=False):
        result = taskpilot_daemon.get_log("t1", lines=3)
    lines = result["output"].split("\n")
    assert lines == ["line17", "line18", "line19"]


# --- server.get_task_log fallback (daemon down) ------------------------------

def test_server_falls_back_when_daemon_down_returns_tmux(isolated):
    with patch("server._daemon_call", return_value=None), \
         patch("server.spawner.is_tmux_alive", return_value=True), \
         patch("server.subprocess.run", return_value=_proc(0, stdout="alpha\nbeta\n")):
        result = taskpilot_server.get_task_log("t1", lines=10)
    assert result["task_id"] == "t1"
    assert result["source"] == "tmux"
    assert "alpha" in result["output"]


def test_server_falls_back_when_daemon_down_returns_file(isolated):
    td = isolated / "t1"
    td.mkdir()
    (td / "pane.log").write_text("from-file\n")
    with patch("server._daemon_call", return_value=None), \
         patch("server.spawner.is_tmux_alive", return_value=False):
        result = taskpilot_server.get_task_log("t1", lines=10)
    assert result["source"] == "pane.log"
    assert "from-file" in result["output"]


def test_server_falls_back_returns_error_when_no_log(isolated):
    with patch("server._daemon_call", return_value=None), \
         patch("server.spawner.is_tmux_alive", return_value=False):
        result = taskpilot_server.get_task_log("t1", lines=10)
    assert "error" in result


def test_server_forwards_daemon_response_when_daemon_up(isolated):
    with patch("server._daemon_call", return_value={"task_id": "t1", "output": "x", "source": "tmux"}):
        result = taskpilot_server.get_task_log("t1")
    assert result == {"task_id": "t1", "output": "x", "source": "tmux"}


# --- destroy_task removes both pane.log and sentinel ------------------------

def test_destroy_task_removes_pane_log_and_sentinel(isolated):
    """destroy_task uses shutil.rmtree on the task dir, which takes both files."""
    td = isolated / "t1"
    td.mkdir()
    (td / "pane.log").write_text("content\n")
    (td / "pane.log.attached").touch()
    (td / "state.json").write_text("{}")

    # Drive destroy_task with a fake "completed" task in a temp DB.
    fake_store = MagicMock()
    fake_conn = MagicMock()
    fake_store.get_db.return_value = fake_conn
    fake_store.get_task.return_value = {"task_id": "t1", "status": "completed"}

    with patch("server.store", fake_store), \
         patch("server.spawner.task_dir", return_value=td):
        result = taskpilot_server.destroy_task("t1")

    assert not td.exists(), "task dir should be rmtree'd"
    assert "destroyed" in str(result).lower() or result.get("status") in ("destroyed", "ok") or "error" not in result
