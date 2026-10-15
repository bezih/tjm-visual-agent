"""Check what the UI actually saved. Reading files is verification, not automation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileCheck:
    ok: bool
    reason: str


def verify_saved(path: Path, expected: str) -> FileCheck:
    if not path.exists():
        return FileCheck(False, f"{path} does not exist")
    if not path.is_file():
        return FileCheck(False, f"{path} is not a regular file")
    raw = path.read_bytes()
    if raw.startswith(b"{\\rtf"):
        return FileCheck(False, "saved as rich text (RTF), not plain text")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return FileCheck(False, "not valid UTF-8")
    if text == expected:
        return FileCheck(True, "content matches exactly")
    if text.rstrip("\n") == expected:
        return FileCheck(False, "content matches except trailing newline(s)")
    first = next(
        (i for i, (a, b) in enumerate(zip(text, expected)) if a != b), min(len(text), len(expected))
    )
    return FileCheck(
        False,
        f"content differs at char {first}: saved {text[first : first + 20]!r} "
        f"vs expected {expected[first : first + 20]!r}",
    )
