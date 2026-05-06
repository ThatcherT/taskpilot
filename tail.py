"""Efficient tail-of-file helpers for pane.log reads.

Both daemon and MCP server use these to return the last N lines of a possibly
large log file without slurping the entire thing into memory.
"""
from __future__ import annotations

from pathlib import Path


def tail_lines(path: Path, n: int, chunk_size: int = 65536) -> str:
    """Return the last n newline-delimited lines of a file as a string.

    Reads from EOF backwards in `chunk_size`-byte chunks until n newlines are
    counted (or BOF is reached). Decodes the assembled bytes as UTF-8 with
    `errors="replace"` so multibyte sequences split across chunk boundaries
    reconstruct correctly (bytes are concatenated before decode).

    Returns "" on empty file or non-positive n.
    """
    if n <= 0:
        return ""
    size = path.stat().st_size
    if size == 0:
        return ""
    chunks: list[bytes] = []
    newlines = 0
    with path.open("rb") as f:
        f.seek(0, 2)
        pos = f.tell()
        while pos > 0 and newlines <= n:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size)
            chunks.append(buf)
            newlines += buf.count(b"\n")
    data = b"".join(reversed(chunks))
    text = data.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-n:])


def tail_str(s: str, n: int) -> str:
    """Return the last n lines of an in-memory string."""
    if n <= 0 or not s:
        return ""
    return "\n".join(s.splitlines()[-n:])
