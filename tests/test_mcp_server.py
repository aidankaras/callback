from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from core.mcp_server import server
from core.paths import ensure_home


def call(name: str, arguments: dict[str, object]) -> object:
    return asyncio.run(server.call_tool(name, arguments))


def test_confirmation_and_deletion_are_not_tools() -> None:
    """Only the candidate, at a terminal, can confirm a fact; no tool may.
    Mutation: register a confirm tool -- this test fails."""
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {
        "build",
        "facts_list",
        "watch",
        "triage",
        "apply",
        "interview",
        "applications",
        "funnel",
    } <= names
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
    an error result for the client. Either way it is never an empty success,
    and an expected failure carries its reason: a model told only "Error
    executing tool build" cannot fix the document. Mutation: swallow the error
    and return {}, or let the reason drop -- this fails."""
    from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    ensure_home()
    with pytest.raises(ToolError, match="No such file or directory") as raised:
        call("build", {"doc_path": str(tmp_path / "missing.yaml"), "job_id": "x"})
    assert not isinstance(raised.value, UnexpectedToolError)


def test_a_callback_error_reaches_the_model_with_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason a gate or a store refused is what the model needs to act on."""
    from mcp.server.mcpserver.exceptions import ToolError

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    with pytest.raises(ToolError, match="no note 'n9'"):
        call("notes_retire", {"note_id": "n9"})


def test_a_bug_still_arrives_without_its_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only failures callback means to explain pass their message on; a crash
    is not a message written for anyone."""
    from mcp.server.mcpserver.exceptions import UnexpectedToolError

    def crash() -> object:
        raise KeyError("internal detail")

    monkeypatch.setattr("core.notes.load", crash)
    with pytest.raises(UnexpectedToolError) as raised:
        call("notes_list", {})
    assert "internal detail" not in str(raised.value)


def test_the_eval_runner_grants_every_registered_tool() -> None:
    """`evals/run.sh` finds the tools by the decorator that registers them. If
    the two drift, every eval runs without callback's tools and still scores."""
    import subprocess

    root = Path(__file__).resolve().parents[1]
    found = subprocess.run(
        ["bash", str(root / "evals" / "run.sh"), "--list-tools"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    registered = {
        f"mcp__plugin_callback_callback__{t.name}" for t in asyncio.run(server.list_tools())
    }
    assert len(registered) > 20  # premise: the server registers its tools
    assert set(found) == registered


def test_every_command_that_only_reads_is_also_a_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session can see what it recorded. Mutation: drop the applications
    tool -- this fails."""
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    ensure_home()
    assert call("applications", {}).structured_content == {"applications": []}  # type: ignore[attr-defined]
    funnel = call("funnel", {}).structured_content  # type: ignore[attr-defined]
    assert funnel == {
        "sources": [
            {
                "source": "all",
                "applied": 0,
                "replied": 0,
                "reply_rate": 0.0,
                "positive": 0,
                "interviews": 0,
                "offers": 0,
                "median_days_to_reply": None,
                "comparable": False,
            }
        ],
        "silent": [],
    }


def test_a_proposed_note_is_not_listed_as_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    proposed = call(
        "notes_propose",
        {"kind": "preference", "text": "One page always", "source_context": "acme-ml"},
    )
    assert proposed.structured_content == {"id": "n1", "status": "proposed"}  # type: ignore[attr-defined]
    everything = call("notes_list", {"include_inactive": True})
    assert [n["id"] for n in everything.structured_content["notes"]] == ["n1"]  # type: ignore[attr-defined]
    assert call("notes_list", {}).structured_content == {"notes": []}  # type: ignore[attr-defined]


def test_no_tool_can_make_a_note_active() -> None:
    """A session may propose or retire a note, never activate one.

    By name and by what the server imports, since a tool called something
    innocuous could still call `activate`. Mutation: import `activate` in
    `notes_list` -- this test fails.
    """
    import ast

    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"notes_list", "notes_propose", "notes_retire"} <= names  # premise: they exist
    assert not any("activate" in n for n in names), names
    source = (Path(__file__).resolve().parents[1] / "core" / "mcp_server.py").read_text()
    used = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module == "core.notes"
        for alias in node.names
    }
    assert used and used <= {"load", "propose", "retire"}, used


def test_a_session_can_retire_a_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    call("notes_propose", {"kind": "preference", "text": "One page always"})
    retired = call("notes_retire", {"note_id": "n1"})
    assert retired.structured_content == {"id": "n1", "status": "retired"}  # type: ignore[attr-defined]


PLANTED = "zqxplantedzqx"


@pytest.mark.parametrize(
    "body",
    [
        f"db:\n  password: {PLANTED}\n",
        f"token: {PLANTED}\nkey: [unclosed\n",
        f"- {PLANTED}\n",
    ],
)
def test_a_refusal_names_the_problem_without_quoting_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    """A tool pointed at a file that is not a callback document refuses it with
    the reason, never with its contents: otherwise `build(doc_path=<any file>)`
    reads files the session's own permissions would not let it open."""
    from mcp.server.mcpserver.exceptions import ToolError

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    ensure_home()
    secret_file = tmp_path / "hosts.yml"
    secret_file.write_text(body, encoding="utf-8")
    assert PLANTED in secret_file.read_text()  # premise: the file holds it
    calls: tuple[tuple[str, dict[str, object]], ...] = (
        ("build", {"doc_path": str(secret_file), "job_id": "x"}),
        ("letter", {"doc_path": str(secret_file)}),
        ("answers", {"doc_path": str(secret_file)}),
        ("outreach", {"doc_path": str(secret_file)}),
    )
    for tool, args in calls:
        with pytest.raises(ToolError) as raised:
            call(tool, args)
        assert PLANTED not in str(raised.value), (tool, str(raised.value))
        assert "zqxpl" not in str(raised.value), tool  # nor a truncated head of it
    # `facts_add` refuses a file that is not a list, and reports each bad entry
    # of one that is; neither may quote the file.
    try:
        text = str(call("facts_add", {"path": str(secret_file)}).structured_content)  # type: ignore[attr-defined]
    except ToolError as exc:
        text = str(exc)
    assert PLANTED not in text and "zqxpl" not in text, text


def test_a_corrupt_notebook_is_refused_without_quoting_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    ensure_home()
    (tmp_path / "cb" / "notes.yaml").write_text(
        f"notes:\n- {{id: n1, kind: nonsense, text: {PLANTED}}}\n", encoding="utf-8"
    )
    with pytest.raises(ToolError) as raised:
        call("notes_list", {})
    assert "kind" in str(raised.value)  # the reason survives
    assert PLANTED not in str(raised.value) and "zqxpl" not in str(raised.value)


def test_a_rejected_entry_is_named_by_position_unless_its_id_is_an_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    ensure_home()
    path = tmp_path / "x.json"
    path.write_text(
        json.dumps([{"id": {"token": PLANTED}}, {"id": PLANTED + " x"}, {"id": "ok-1"}])
    )
    called = call("facts_add", {"path": str(path)})
    result = cast("dict[str, Any]", called.structured_content)  # type: ignore[attr-defined]
    assert [r["id"] for r in result["rejected"]] == ["#0", "#1", "ok-1"]
    assert PLANTED not in str(result)


@pytest.mark.parametrize(
    "body",
    [
        f"identity: {{name: A, email: a@b.co}}\n{PLANTED}_key: 1\n",
        f"recipient: 'api_key={PLANTED}'\n",
    ],
)
def test_an_unknown_key_or_a_rejected_value_is_not_quoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    ensure_home()
    doc = tmp_path / "doc.yaml"
    doc.write_text(body, encoding="utf-8")
    for tool in ("build", "outreach"):
        args: dict[str, object] = {"doc_path": str(doc)}
        if tool == "build":
            args["job_id"] = "x"
        with pytest.raises(ToolError) as raised:
            call(tool, args)
        assert PLANTED not in str(raised.value), (tool, str(raised.value))


@pytest.mark.parametrize(
    ("setup", "tool", "args"),
    [
        ("outreach-yaml", "outreach", {}),
        ("profile", "triage", {}),
        ("binary", "answers", {}),
    ],
)
def test_every_expected_failure_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setup: str, tool: str, args: dict[str, object]
) -> None:
    """Never the bare "Error executing tool": a model told only that cannot act."""
    from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    ensure_home()
    doc = tmp_path / "doc.yaml"
    if setup == "outreach-yaml":
        doc.write_text("recipient: [unclosed\n", encoding="utf-8")
    elif setup == "binary":
        doc.write_bytes(b"\xff\xfe\x00garbage")
    else:
        (tmp_path / "cb" / "profile.yaml").write_text("work_authorization: [\n", encoding="utf-8")
    if setup != "profile":
        args = {"doc_path": str(doc)}
    with pytest.raises(ToolError) as raised:
        call(tool, args)
    assert not isinstance(raised.value, UnexpectedToolError), str(raised.value)
