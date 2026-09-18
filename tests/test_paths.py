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
