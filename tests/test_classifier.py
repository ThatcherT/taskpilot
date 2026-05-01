"""Tests for classifier — buckets the agent's final message."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import classifier


class TestClassifyUneventful:
    def test_empty_string(self):
        assert classifier.classify("") == "uneventful"

    def test_none(self):
        assert classifier.classify(None) == "uneventful"

    def test_plain_statement(self):
        assert classifier.classify("Reading the file now.") == "uneventful"

    def test_mid_flow_punctuation(self):
        assert classifier.classify("Got it. Continuing with the next step.") == "uneventful"


class TestClassifyResolved:
    def test_task_complete(self):
        assert classifier.classify("Task complete.") == "resolved"

    def test_task_is_complete(self):
        assert classifier.classify("The task is complete.") == "resolved"

    def test_task_done(self):
        assert classifier.classify("Task done.") == "resolved"

    def test_task_resolved(self):
        assert classifier.classify("Task resolved successfully.") == "resolved"

    def test_task_finished(self):
        assert classifier.classify("Task finished.") == "resolved"

    def test_all_done(self):
        assert classifier.classify("All done!") == "resolved"

    def test_everything_done(self):
        assert classifier.classify("Everything done, tests pass.") == "resolved"

    def test_nothing_left_to_do(self):
        assert classifier.classify("Nothing left to do here.") == "resolved"

    def test_nothing_else_to_do(self):
        assert classifier.classify("Nothing else to do.") == "resolved"

    def test_finished_the_work(self):
        assert classifier.classify("Finished the work.") == "resolved"

    def test_finished_the_job(self):
        assert classifier.classify("Finished the job.") == "resolved"

    def test_wrapping_up(self):
        assert classifier.classify("Wrapping up now.") == "resolved"

    def test_marking_complete(self):
        assert classifier.classify("Marking this complete.") == "resolved"

    def test_marking_task_complete(self):
        assert classifier.classify("Marking the task complete.") == "resolved"

    def test_case_insensitive(self):
        assert classifier.classify("TASK COMPLETE.") == "resolved"
        assert classifier.classify("Task Done.") == "resolved"

    def test_completion_in_long_message(self):
        prefix = "Did a bunch of things. " * 20
        assert classifier.classify(prefix + "Task complete.") == "resolved"


class TestClassifyQuestion:
    def test_simple_question(self):
        assert classifier.classify("Should I proceed?") == "question"

    def test_question_with_trailing_whitespace(self):
        assert classifier.classify("Which database?  \n") == "question"

    def test_question_with_trailing_markdown_emphasis(self):
        assert classifier.classify("Want me to continue?*") == "question"

    def test_question_with_trailing_quote(self):
        assert classifier.classify('Did you mean "main"?"') == "question"

    def test_question_with_trailing_paren(self):
        assert classifier.classify("Should I run it (with verbose)?)") == "question"

    def test_question_after_long_explanation(self):
        body = "Here's what I found. " * 30
        assert classifier.classify(body + "Should I proceed?") == "question"


class TestClassifyResolvedWinsTies:
    def test_completion_phrase_with_question_mark(self):
        # Both signals present — resolved should win.
        assert classifier.classify("Task complete. Anything else?") == "resolved"

    def test_completion_in_tail_with_trailing_question(self):
        assert classifier.classify("Wrapping up. Right?") == "resolved"


class TestClassifyTailWindow:
    def test_completion_only_in_old_history(self):
        # Old completion phrase outside the 400-char tail window — should NOT resolve.
        old = "Task complete." + ("filler text " * 50)  # ~700 chars after completion
        assert len(old) > 400
        result = classifier.classify(old)
        assert result == "uneventful"

    def test_completion_at_very_end_within_tail(self):
        prefix = "filler " * 50  # ~350 chars, ends in whitespace so \b matches
        msg = prefix + "Task complete."
        assert classifier.classify(msg) == "resolved"


class TestClassifyEdgeCases:
    def test_just_a_question_mark(self):
        assert classifier.classify("?") == "question"

    def test_question_mark_inside_no_trailing(self):
        # Mid-sentence question mark, but ends with a period — not a question.
        assert classifier.classify("Is this it? Probably not.") == "uneventful"

    def test_word_complete_without_task_context(self):
        # COMPLETION_PATTERNS require "task complete" specifically, not bare "complete".
        assert classifier.classify("The build is complete.") == "uneventful"

    def test_done_alone_does_not_match(self):
        # Bare "done." doesn't match any pattern — needs "task done" or "all done" etc.
        assert classifier.classify("done.") == "uneventful"
