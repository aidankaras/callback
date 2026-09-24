"""No tracked file may carry an unresolved merge conflict.

A three-way apply once left nested conflict markers in a document, and every
other check passed: the command names each side mentioned still existed, so
the documentation test was satisfied by the mess.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = re.compile(r"^(<{7}|>{7}|\|{7})( |$)|^={7}$", re.MULTILINE)


def _tracked_text_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return [ROOT / name for name in listed if not name.endswith((".pdf", ".png", ".otf", ".ttf"))]


def test_no_tracked_file_has_conflict_markers() -> None:
    files = _tracked_text_files()
    assert len(files) > 100  # premise: the listing found the repository
    bad = [
        str(path.relative_to(ROOT))
        for path in files
        if path.is_file() and MARKER.search(path.read_text(encoding="utf-8", errors="replace"))
    ]
    assert bad == []


def test_the_pattern_finds_a_real_conflict() -> None:
    sample = "a\n<<<<<<< ours\nx\n=======\ny\n>>>>>>> theirs\nb\n"
    assert len(MARKER.findall(sample)) == 3
    assert not MARKER.search("a heading\n=======\n".replace("=======", "======"))
