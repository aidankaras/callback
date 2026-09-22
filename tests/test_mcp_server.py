from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.mcp_server import server
from core.paths import ensure_home


def call(name: str, arguments: dict[str, object]) -> object:
    return asyncio.run(server.call_tool(name, arguments))


def test_confirmation_and_deletion_are_not_tools() -> None:
    """Only the candidate, at a terminal, can confirm a fact; no tool may.
    Mutation: register a confirm tool -- this test fails."""
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"build", "facts_list", "watch", "triage", "apply", "interview"} <= names
    assert not any("confirm" in n or "delete" in n for n in names), names


def test_a_tool_returns_structured_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    ensure_home()
    facts = tmp_path / "facts.yaml"
    facts.write_text(
        "- id: a\n  kind: skill\n  text: Python\n"
        "  provenance: {source: 'import:x.pdf', trust: import_unconfirmed}\n",
        encoding="utf-8",
    )
    added = call("facts_add", {"path": str(facts)})
    assert added.structured_content == {"added": ["a"], "rejected": []}  # type: ignore[attr-defined]
    listed = call("facts_list", {"unconfirmed_only": True})
    assert listed.structured_content["facts"][0]["confirmed"] is False  # type: ignore[attr-defined]


def test_a_failure_is_a_tool_error_not_an_empty_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Called directly, a failing tool raises; the MCP session turns that into
    an error result for the client. Either way it is never an empty success.
    Mutation: swallow the error and return {} -- this fails."""
    from mcp.server.mcpserver.exceptions import UnexpectedToolError

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    ensure_home()
    with pytest.raises(UnexpectedToolError, match="build"):
        call("build", {"doc_path": str(tmp_path / "missing.yaml"), "job_id": "x"})
