import os
import stat
from pathlib import Path

import pytest

from core.paths import (
    FILE_MODE,
    OutsideHomeError,
    callback_home,
    delete_all,
    ensure_home,
    resolve_inside,
    write_private,
)


def test_home_honours_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    assert callback_home() == tmp_path / "cb"


def test_ensure_home_creates_private_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    home = ensure_home()
    mode = stat.S_IMODE(home.stat().st_mode)
    assert mode == 0o700, f"expected 0700, got {mode:o}"


def test_ensure_home_creates_missing_parents_privately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Intermediate parents that `ensure_home` has to create must also land at 0700.

    A pre-existing parent is a different case: it belongs to whoever put it there,
    so `ensure_home` must leave its mode exactly as it found it.
    """
    existing = tmp_path / "existing"
    existing.mkdir(mode=0o755)
    existing.chmod(0o755)  # mkdir's mode is subject to umask; set it explicitly.
    home = existing / "level1" / "level2" / "cb"
    monkeypatch.setenv("CALLBACK_HOME", str(home))

    ensure_home()

    for created in (existing / "level1", existing / "level1" / "level2", home):
        mode = stat.S_IMODE(created.stat().st_mode)
        assert mode == 0o700, f"expected 0700 for {created}, got {mode:o}"
    existing_mode = stat.S_IMODE(existing.stat().st_mode)
    assert existing_mode == 0o755, f"pre-existing parent must be untouched, got {existing_mode:o}"


def test_resolve_inside_rejects_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    ensure_home()
    with pytest.raises(OutsideHomeError):
        resolve_inside("../escaped.yaml")


def test_resolve_inside_rejects_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    home = ensure_home()
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / "sneaky").symlink_to(outside)
    with pytest.raises(OutsideHomeError):
        resolve_inside("sneaky/file.yaml")


def test_write_private_sets_file_mode(tmp_path: Path) -> None:
    target = tmp_path / "secret.txt"
    write_private(target, "hello")
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == FILE_MODE, f"expected {FILE_MODE:o}, got {mode:o}"


def test_write_private_preserves_existing_content_on_failed_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write that fails must never truncate what was already on disk.

    The old implementation wrote directly to the target, so a failure partway
    through could lose every previously stored fact, not just the one being
    added. Simulating the failure on the temp file, rather than the target,
    is what proves the fix: the target is never opened for writing at all.
    """
    target = tmp_path / "facts.yaml"
    target.write_text("original", encoding="utf-8")
    target.chmod(FILE_MODE)

    def _boom(self: Path, *_args: object, **_kwargs: object) -> int:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(Path, "write_text", _boom)

    with pytest.raises(OSError):
        write_private(target, "replacement")

    assert target.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.iterdir()) == [target], "no leftover temp file should remain"


def test_delete_all_unlinks_a_fifo_instead_of_skipping_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A FIFO (or a socket) is neither a plain file, a directory nor a
    symlink, so the old three-way dispatch in `_walk` left it on disk with
    no error and no mention in what was removed -- a silent skip on a
    command whose whole job is deleting personal data. Every non-directory
    entry must be unlinked, whatever kind it is."""
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    home = ensure_home()
    fifo = home / "leftover.fifo"
    os.mkfifo(fifo)

    removed = list(delete_all())

    assert fifo in removed
    assert not fifo.exists()
    assert list(home.iterdir()) == []


def test_delete_all_yields_each_removal_as_it_happens_not_after_the_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`delete_all` must report what it already removed even when a later
    removal fails partway through -- the old implementation built the full
    removed-list before returning it, so a mid-walk exception left the
    caller with nothing to show for what had already vanished. Simulating
    the failure on the second unlink call proves removals already made are
    visible to the caller before the failure propagates.
    """
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    home = ensure_home()
    (home / "a.txt").write_text("a")
    (home / "b.txt").write_text("b")

    calls = {"count": 0}
    original_unlink = Path.unlink

    def _flaky_unlink(self: Path, missing_ok: bool = False) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("simulated failure")
        original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", _flaky_unlink)

    already_removed: list[Path] = []
    with pytest.raises(OSError):
        for removed in delete_all():
            already_removed.append(removed)

    assert len(already_removed) == 1, "the first successful removal must already be visible"
