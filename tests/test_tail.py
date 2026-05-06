"""Tests for taskpilot.tail."""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from tail import tail_lines, tail_str


def test_empty_file(tmp_path):
    p = tmp_path / "empty.log"
    p.write_bytes(b"")
    assert tail_lines(p, 10) == ""


def test_zero_n(tmp_path):
    p = tmp_path / "f.log"
    p.write_text("a\nb\n")
    assert tail_lines(p, 0) == ""
    assert tail_lines(p, -1) == ""


def test_simple_tail(tmp_path):
    p = tmp_path / "f.log"
    p.write_text("a\nb\nc\n")
    assert tail_lines(p, 2) == "b\nc"


def test_no_trailing_newline(tmp_path):
    p = tmp_path / "f.log"
    p.write_text("a\nb\nc")
    assert tail_lines(p, 2) == "b\nc"


def test_fewer_lines_than_n(tmp_path):
    p = tmp_path / "f.log"
    p.write_text("only one\n")
    assert tail_lines(p, 5) == "only one"


def test_100_lines(tmp_path):
    p = tmp_path / "big.log"
    p.write_text("\n".join(f"line{i}" for i in range(100)) + "\n")
    result = tail_lines(p, 5)
    assert result.split("\n") == ["line95", "line96", "line97", "line98", "line99"]


def test_multi_chunk_path(tmp_path):
    """Force the multi-chunk read path."""
    p = tmp_path / "big.log"
    # 200 lines × 1 KB each = 200 KB; chunk_size 64 KB means 4 chunks
    line = "x" * 1023 + "\n"
    p.write_text(line * 200)
    result = tail_lines(p, 50, chunk_size=65536)
    lines = result.split("\n")
    assert len(lines) == 50
    for line in lines:
        assert line == "x" * 1023


def test_multibyte_within_single_chunk(tmp_path):
    """Multibyte UTF-8 entirely inside one chunk decodes correctly."""
    p = tmp_path / "utf8.log"
    p.write_text("先頭\n中間\n末尾\n", encoding="utf-8")
    assert tail_lines(p, 2) == "中間\n末尾"


def test_multibyte_across_chunk_boundary(tmp_path):
    """A multibyte UTF-8 sequence split across a 64 KB read boundary reconstructs correctly.

    Build a file where bytes \\xc3\\xa9 (é) sit across a 64 KB chunk seam.
    The function reads from end backwards in chunk_size chunks; we want
    one chunk to end with \\xc3 and the next (later in file order, earlier
    in read order) to start with \\xa9 — or vice versa.
    """
    chunk_size = 64
    # Layout: 60 bytes "x", then \xc3\xa9 ("é"), then 60 more bytes "y\n"...
    # We want the read window to land inside the multibyte. With chunk_size=64
    # reading from the end, the boundary lands at byte (size - 64). Engineer
    # the file so the é straddles that.
    fill_a = b"a" * 63
    multibyte = "é".encode("utf-8")  # b'\xc3\xa9', 2 bytes
    fill_b = b"b" * 63 + b"\n"
    # Total: 63 + 2 + 64 = 129 bytes. Reading the last 64 bytes lands at byte 65,
    # which is the second byte of é — splitting it across the boundary.
    p = tmp_path / "split.log"
    p.write_bytes(fill_a + multibyte + fill_b)
    result = tail_lines(p, 1, chunk_size=chunk_size)
    # The single line should contain "é" as a real character.
    assert "é" in result
    # And no replacement characters (which would indicate decode broke).
    assert "�" not in result


def test_lone_invalid_byte_replaced(tmp_path):
    """A truly invalid UTF-8 byte (lone \\xff) is replaced with U+FFFD, no crash."""
    p = tmp_path / "bad.log"
    p.write_bytes(b"foo\n\xff\nbar\n")
    result = tail_lines(p, 3)
    assert "�" in result
    # All three lines preserved structurally
    assert result.count("\n") == 2


def test_tail_str():
    assert tail_str("a\nb\nc", 2) == "b\nc"
    assert tail_str("", 5) == ""
    assert tail_str("x", 0) == ""


def test_perf_on_10mb_file(tmp_path):
    """tail_lines on a 10 MB file completes in <500ms (perf budget)."""
    p = tmp_path / "huge.log"
    line = "x" * 99 + "\n"  # 100 bytes
    with p.open("wb") as f:
        for _ in range(105_000):  # ~10.5 MB
            f.write(line.encode())
    start = time.monotonic()
    result = tail_lines(p, 100)
    elapsed = time.monotonic() - start
    assert len(result.split("\n")) == 100
    assert elapsed < 0.5, f"tail_lines took {elapsed:.3f}s on 10 MB file"
