"""Suite-wide guards."""

from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may reach the network: a passing test must not depend on a
    remote service being up, or on what it happens to return today. Local
    (Unix-domain) sockets are still allowed."""
    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address: object) -> None:
        if self.family == socket.AF_UNIX:
            real_connect(self, address)  # type: ignore[arg-type]
            return
        raise RuntimeError(f"tests may not open network connections (tried {address!r})")

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture(autouse=True)
def _no_real_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No test may read or write the machine's real data home. A test that
    forgets to set `CALLBACK_HOME` gets an empty one of its own instead of
    whatever `~/.callback` holds -- a pass that depended on it would be a
    pass on someone's personal data. A test that sets its own still wins."""
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path_factory.mktemp("unset-home") / "cb"))
