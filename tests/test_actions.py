"""Tests for actions — side effects fired by the Stop hook classifier."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import actions


@pytest.fixture()
def isolated_taskpilot_dir(tmp_path, monkeypatch):
    """Point actions.TASKPILOT_DIR at a tmp dir so tests don't touch real state."""
    monkeypatch.setattr(actions, "TASKPILOT_DIR", tmp_path)
    return tmp_path


class TestMarkCompletedAndKill:
    def test_updates_db_status(self, isolated_taskpilot_dir):
        with patch.object(actions, "store") as mock_store, \
             patch.object(actions.subprocess, "Popen") as mock_popen, \
             patch.object(actions.subprocess, "run") as mock_run:
            mock_conn = MagicMock()
            mock_store.get_db.return_value = mock_conn
            mock_run.return_value = MagicMock(returncode=0)

            actions.mark_completed_and_kill("my-task")

            mock_store.update_status.assert_called_once_with(mock_conn, "my-task", "completed")
            mock_conn.close.assert_called_once()
            # kill-session must be among the Popen calls (legacy path captures via run, steady toggles via run).
            kill_calls = [
                c for c in mock_popen.call_args_list
                if "kill-session" in c[0][0]
            ]
            assert len(kill_calls) == 1

    def test_kills_tmux_session(self, isolated_taskpilot_dir):
        with patch.object(actions, "store"), \
             patch.object(actions.subprocess, "Popen") as mock_popen:
            actions.mark_completed_and_kill("my-task")

            args, kwargs = mock_popen.call_args
            assert args[0] == ["tmux", "kill-session", "-t", "my-task"]
            assert kwargs.get("start_new_session") is True

    def test_db_failure_does_not_block_kill(self, isolated_taskpilot_dir):
        # If store.get_db raises, we should still kill tmux (and not raise).
        with patch.object(actions, "store") as mock_store, \
             patch.object(actions.subprocess, "Popen") as mock_popen, \
             patch.object(actions.subprocess, "run") as mock_run:
            mock_store.get_db.side_effect = RuntimeError("db gone")
            mock_run.return_value = MagicMock(returncode=0)
            actions.mark_completed_and_kill("my-task")
            kill_calls = [
                c for c in mock_popen.call_args_list
                if "kill-session" in c[0][0]
            ]
            assert len(kill_calls) == 1

    def test_tmux_failure_does_not_raise(self, isolated_taskpilot_dir):
        with patch.object(actions, "store"), \
             patch.object(actions.subprocess, "Popen") as mock_popen:
            mock_popen.side_effect = OSError("tmux missing")
            # Must not raise.
            actions.mark_completed_and_kill("my-task")


class TestNotifyHuman:
    def test_writes_escalation_record(self, isolated_taskpilot_dir, monkeypatch):
        monkeypatch.delenv("TASKPILOT_NOTIFY_CMD", raising=False)

        actions.notify_human("my-task", "What database should I use?")

        log = isolated_taskpilot_dir / "my-task" / "escalations.jsonl"
        assert log.exists()
        record = json.loads(log.read_text().strip())
        assert record["task_id"] == "my-task"
        assert record["message"] == "What database should I use?"
        assert "at" in record  # ISO timestamp

    def test_appends_multiple_escalations(self, isolated_taskpilot_dir, monkeypatch):
        monkeypatch.delenv("TASKPILOT_NOTIFY_CMD", raising=False)

        actions.notify_human("my-task", "First question?")
        actions.notify_human("my-task", "Second question?")

        log = isolated_taskpilot_dir / "my-task" / "escalations.jsonl"
        lines = log.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["message"] == "First question?"
        assert json.loads(lines[1])["message"] == "Second question?"

    def test_creates_task_directory(self, isolated_taskpilot_dir, monkeypatch):
        monkeypatch.delenv("TASKPILOT_NOTIFY_CMD", raising=False)
        # Task dir doesn't exist — notify_human must create it.
        assert not (isolated_taskpilot_dir / "my-task").exists()

        actions.notify_human("my-task", "Stuck.")

        assert (isolated_taskpilot_dir / "my-task" / "escalations.jsonl").exists()

    def test_no_notify_cmd_means_no_subprocess(self, isolated_taskpilot_dir, monkeypatch):
        monkeypatch.delenv("TASKPILOT_NOTIFY_CMD", raising=False)
        with patch.object(actions.subprocess, "Popen") as mock_popen:
            actions.notify_human("my-task", "Anything?")
            mock_popen.assert_not_called()

    def test_notify_cmd_set_invokes_subprocess(self, isolated_taskpilot_dir, monkeypatch):
        monkeypatch.setenv("TASKPILOT_NOTIFY_CMD", "/usr/local/bin/notify-me")
        with patch.object(actions.subprocess, "Popen") as mock_popen:
            actions.notify_human("my-task", "Help.")

            assert mock_popen.called
            args, kwargs = mock_popen.call_args
            assert args[0] == ["sh", "-c", "/usr/local/bin/notify-me"]
            env = kwargs.get("env", {})
            assert env.get("TASKPILOT_TASK_ID") == "my-task"
            assert env.get("TASKPILOT_MESSAGE") == "Help."
            assert kwargs.get("start_new_session") is True

    def test_notify_cmd_failure_does_not_raise(self, isolated_taskpilot_dir, monkeypatch):
        monkeypatch.setenv("TASKPILOT_NOTIFY_CMD", "/bin/whatever")
        with patch.object(actions.subprocess, "Popen") as mock_popen:
            mock_popen.side_effect = OSError("nope")
            # Must not raise.
            actions.notify_human("my-task", "Help.")

    def test_log_write_failure_does_not_raise(self, isolated_taskpilot_dir, monkeypatch):
        monkeypatch.delenv("TASKPILOT_NOTIFY_CMD", raising=False)
        # Make log_path.parent.mkdir raise — function must swallow it.
        with patch.object(actions.Path, "mkdir", side_effect=PermissionError("nope")):
            actions.notify_human("my-task", "Help.")  # no raise
