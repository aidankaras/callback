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
