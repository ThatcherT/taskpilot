"""Tests for classifier — buckets the agent's final message via LLM judge.

The classifier shells out to `claude -p`. These tests mock the subprocess so
they're hermetic; we cover prompt construction, output parsing, and the
fail-soft path (any error → uneventful).
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import classifier


def _mock_run(stdout: str = "", returncode: int = 0):
    """Build a fake CompletedProcess for patching subprocess.run."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class TestNoJudgeCalls:
    """Cases that short-circuit before invoking the subprocess."""

    def test_empty_string(self):
        with patch("classifier.subprocess.run") as run:
            assert classifier.classify("") == "uneventful"
            run.assert_not_called()

    def test_none(self):
        with patch("classifier.subprocess.run") as run:
            assert classifier.classify(None) == "uneventful"
            run.assert_not_called()


class TestVerdictParsing:
    def test_resolved_bare(self):
        with patch("classifier.subprocess.run", return_value=_mock_run("resolved")):
            assert classifier.classify("Done.") == "resolved"

    def test_question_bare(self):
        with patch("classifier.subprocess.run", return_value=_mock_run("question")):
            assert classifier.classify("Should I proceed?") == "question"

    def test_uneventful_bare(self):
        with patch("classifier.subprocess.run", return_value=_mock_run("uneventful")):
            assert classifier.classify("Reading the file.") == "uneventful"

    def test_handles_trailing_punctuation(self):
        with patch("classifier.subprocess.run", return_value=_mock_run("resolved.")):
            assert classifier.classify("All wrapped up.") == "resolved"

    def test_handles_capitalization(self):
        with patch("classifier.subprocess.run", return_value=_mock_run("RESOLVED")):
            assert classifier.classify("Finished.") == "resolved"

    def test_handles_explanatory_prefix(self):
        # Haiku occasionally adds reasoning before the verdict despite the
        # "one word" instruction. Last-bucket-mentioned wins via membership check.
        with patch(
            "classifier.subprocess.run",
            return_value=_mock_run("The agent met the success criteria. resolved"),
        ):
            assert classifier.classify("Done.") == "resolved"


class TestFailSoft:
    def test_subprocess_timeout_returns_uneventful(self):
        with patch("classifier.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=60)):
            assert classifier.classify("Done.") == "uneventful"

    def test_claude_binary_missing_returns_uneventful(self):
        with patch("classifier.subprocess.run", side_effect=FileNotFoundError("claude not found")):
            assert classifier.classify("Done.") == "uneventful"

    def test_nonzero_returncode_returns_uneventful(self):
        with patch("classifier.subprocess.run", return_value=_mock_run("resolved", returncode=1)):
            assert classifier.classify("Done.") == "uneventful"

    def test_empty_stdout_returns_uneventful(self):
        with patch("classifier.subprocess.run", return_value=_mock_run("")):
            assert classifier.classify("Done.") == "uneventful"

    def test_unparseable_output_returns_uneventful(self):
        with patch("classifier.subprocess.run", return_value=_mock_run("¯\\_(ツ)_/¯")):
            assert classifier.classify("Done.") == "uneventful"


class TestPromptConstruction:
    def test_brief_loaded_from_task_dir(self, tmp_path, monkeypatch):
        tid = "test-task-123"
        task_dir = tmp_path / tid
        task_dir.mkdir()
        brief = {
            "objectives": ["Triage one email."],
            "success_criteria": ["Slack summary posted.", "Email archived."],
            "boundaries": ["Do not delete."],
        }
        (task_dir / "brief.json").write_text(json.dumps(brief))
        monkeypatch.setattr(classifier, "_TASKPILOT_DIR", tmp_path)

        with patch("classifier.subprocess.run", return_value=_mock_run("resolved")) as run:
            classifier.classify("Archived. Posted summary. Done.", tid)

        prompt = run.call_args.kwargs["input"]
        assert "Triage one email." in prompt
        assert "Slack summary posted." in prompt
        assert "Do not delete." in prompt
        assert "Archived. Posted summary. Done." in prompt

    def test_missing_brief_does_not_break(self, tmp_path, monkeypatch):
        monkeypatch.setattr(classifier, "_TASKPILOT_DIR", tmp_path)
        with patch("classifier.subprocess.run", return_value=_mock_run("uneventful")) as run:
            assert classifier.classify("Working...", "no-such-task") == "uneventful"
            assert run.called  # judge still invoked, just with empty brief

    def test_no_task_id_does_not_break(self):
        with patch("classifier.subprocess.run", return_value=_mock_run("resolved")) as run:
            assert classifier.classify("Done.") == "resolved"
            assert run.called
            prompt = run.call_args.kwargs["input"]
            assert "(no brief available)" in prompt

    def test_uses_print_and_haiku_flags(self):
        with patch("classifier.subprocess.run", return_value=_mock_run("resolved")) as run:
            classifier.classify("Done.")
            argv = run.call_args.args[0]
            assert "--print" in argv
            assert "haiku" in argv

    def test_strips_stale_env_vars_from_subprocess(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "stale-key")
        monkeypatch.setenv("TASKPILOT_TASK_ID", "some-task")
        monkeypatch.setenv("UNRELATED_VAR", "kept")
        with patch("classifier.subprocess.run", return_value=_mock_run("resolved")) as run:
            classifier.classify("Done.")
            env = run.call_args.kwargs["env"]
            assert "ANTHROPIC_API_KEY" not in env
            assert "TASKPILOT_TASK_ID" not in env
            assert env.get("UNRELATED_VAR") == "kept"

    def test_prompt_passed_via_stdin(self):
        with patch("classifier.subprocess.run", return_value=_mock_run("resolved")) as run:
            classifier.classify("Done.")
            assert "Done." in run.call_args.kwargs["input"]
            # And NOT as a positional argv (avoids --tools "" trap)
            argv = run.call_args.args[0]
            assert "Done." not in argv
