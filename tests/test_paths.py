import stat
from pathlib import Path

import pytest

from core.paths import OutsideHomeError, callback_home, ensure_home, resolve_inside


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
