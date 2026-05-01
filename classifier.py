"""Classify an agent's final assistant message into an action bucket.

Used by:
- hooks/on-stop.py — to act in real time when the agent finishes a turn
- rotation.py — as a safety net when the agent exits before the hook can act

Three buckets:
  resolved    — the agent declared the task done.
  question    — the agent ended with a direct question to the human.
  uneventful  — neither; agent stopped mid-flow or paused without asking.

False positives on `resolved` cause premature task completion (bad), so the
patterns are conservative and require explicit completion phrasing.
False positives on `question` cause an unneeded notification (cheap), so we
use the loose "tail ends in '?'" rule. Resolved wins ties.
"""

import re

COMPLETION_PATTERNS = [
    r"\btask (?:is )?(?:complete|completed|done|resolved|finished)\b",
    r"\b(?:all|everything) done\b",
    r"\bnothing (?:left|else) to do\b",
    r"\bfinished (?:the )?(?:task|work|job)\b",
    r"\bwrapping up\b",
    r"\bmarking (?:this |the )?(?:task )?complete\b",
]
_COMPLETION_RE = re.compile("|".join(COMPLETION_PATTERNS), re.IGNORECASE)

# Strip trailing markdown / formatting before checking for "?"
_TRAILING = " \t\n.*_`>)]\""


def classify(message: str) -> str:
    """Return one of: 'resolved', 'question', 'uneventful'."""
    if not message:
        return "uneventful"

    tail = message[-400:]
    if _COMPLETION_RE.search(tail):
        return "resolved"

    if tail.rstrip(_TRAILING).endswith("?"):
        return "question"

    return "uneventful"
