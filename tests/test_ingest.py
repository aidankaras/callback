from __future__ import annotations

import subprocess
from pathlib import Path

import pdfplumber
import pytest

from core.checks.hidden import HiddenKind, check_hidden
from core.ingest.extract import MAX_GAPS, REPO_ROOT, ExtractionError, extract_document, gap_audit
from core.render.typst import FONT_DIR
from tests.fixtures.make_hidden import GRADIENT_BACKGROUND, WHITE, build


@pytest.mark.slow
def test_visible_text_is_extracted(tmp_path: Path) -> None:
    from core.profile.models import Identity
    from core.render.typst import render
    from core.resume.models import Bullet, Entry, ResumeDoc, Section

    doc = ResumeDoc(
        identity=Identity(name="Dana Vex", email="d@example.com"),
        sections=[
            Section(
                title="Experience",
                entries=[
                    Entry(
                        heading="Northwind Analytics",
                        bullets=[Bullet(text="Shipped the loader", sources=["a"])],
                    )
                ],
            )
        ],
    )
    out = tmp_path / "in.pdf"
    render(doc, out)
    result = extract_document(out)
    assert "Northwind" in result.text
    assert result.pages == 1
    assert result.suspicious == []


@pytest.mark.slow
def test_hidden_text_is_reported_not_silently_ingested(tmp_path: Path) -> None:
    out = tmp_path / "white.pdf"
    build(WHITE, out, FONT_DIR)

    # The fixture's own premise, checked directly, before trusting that
    # screening surfaces it: the payload text is actually present in the
    # PDF's content stream, and `check_hidden` independently calls it
    # invisible. Mutation: point `build` at a fixture with no hidden text
    # (e.g. `NAVY_BACKGROUND_WHITE_TEXT`) -- both of these assertions fail.
    with pdfplumber.open(str(out)) as pdf:
        raw_text = pdf.pages[0].extract_text() or ""
    assert "kubernetes" in raw_text.lower(), "fixture's hidden payload never made it onto the page"
    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.INVISIBLE_TEXT for f in findings)

    result = extract_document(out)
    assert result.suspicious, "invisible text was not reported"
    assert any("invisible_text" in s for s in result.suspicious)
    # The extracted text itself still contains the payload -- screening
    # reports suspicious content, it does not redact it from `result.text`.
    assert "kubernetes" in result.text.lower()


@pytest.mark.slow
def test_background_unknown_is_surfaced_not_swallowed(tmp_path: Path) -> None:
    """A page whose fill is a gradient has no single background colour to
    compare a glyph's fill against, so `check_hidden` reports
    `background_unknown` for that page rather than guessing -- and an
    imported file with a patterned or gradient background is a realistic
    case, not an exotic one. Mutation: build `NAVY_BACKGROUND_WHITE_TEXT`
    (a solid background) instead -- `check_hidden` then finds a background
    to check against, `background_unknown` never fires, and this fails.
    """
    out = tmp_path / "gradient.pdf"
    build(GRADIENT_BACKGROUND, out, FONT_DIR)

    verdict, findings = check_hidden(out)
    assert verdict == "fail"
    assert any(f.kind == HiddenKind.BACKGROUND_UNKNOWN for f in findings)

    result = extract_document(out)
    assert any("background_unknown" in s for s in result.suspicious)


def test_unknown_extension_is_refused(tmp_path: Path) -> None:
    bogus = tmp_path / "resume.rtf"
    bogus.write_bytes(b"{\\rtf1}")
    with pytest.raises(ValueError, match="unsupported"):
        extract_document(bogus)


def test_docx_is_not_supported_yet(tmp_path: Path) -> None:
    """`.docx` import is deferred: the specification defers it pending
    better evidence, and a second untrusted container format only widens
    the surface this module has to defend. A `.docx` file is refused the
    same way any other unrecognised extension is, not given special
    handling.
    """
    docx = tmp_path / "resume.docx"
    docx.write_bytes(b"PK\x03\x04 pretend this is a docx")
    with pytest.raises(ValueError, match="unsupported"):
        extract_document(docx)


def test_magic_bytes_must_match_the_extension(tmp_path: Path) -> None:
    liar = tmp_path / "resume.pdf"
    liar.write_bytes(b"PK\x03\x04 not really a pdf")
    with pytest.raises(ValueError, match="do not match"):
        extract_document(liar)


def test_magic_byte_check_does_not_load_the_whole_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The header check reads four bytes, not the whole file: on the one
    code path that accepts a file chosen by someone else, `path.read_bytes()
    [:4]` would load a 5 MB file in full just to learn its first four bytes,
    before any validation has happened at all. Mutation: replace the bounded
    `handle.read(4)` in `extract_document` with `path.read_bytes()[:4]` --
    this test's tripwire (a `Path.read_bytes` call made anywhere during the
    call) then fires and the assertion fails.
    """
    # A supported extension with a header that will not match, so the
    # header-read line runs and then fails the comparison -- never reaching
    # the subprocess, but only after actually performing the read this test
    # is watching.
    huge = tmp_path / "resume.pdf"
    with huge.open("wb") as fh:
        fh.write(b"NOPE1.4\n")
        fh.write(b"0" * (5 * 1024 * 1024))

    calls: list[int] = []
    real_read_bytes = Path.read_bytes

    def tracking_read_bytes(self: Path, *args: object, **kwargs: object) -> bytes:
        calls.append(1)
        return real_read_bytes(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_bytes", tracking_read_bytes)
    with pytest.raises(ValueError, match="do not match"):
        extract_document(huge)
    assert not calls, "the magic-byte check read the file with a whole-file read"


def test_gap_audit_flags_bullets_without_outcomes() -> None:
    """Mutation: invert the flagging condition (`if body and
    _ACTION.search(body)` instead of `not _ACTION.search(body)`) -- both
    bullets below still produce exactly one gap either way, since one of
    the two always matches the action pattern and the other never does, so
    `len(gaps) == 1` alone cannot tell the two directions apart. The content
    check below can: inverted, the flagged bullet becomes the one with an
    outcome ("Cut latency...") rather than the one without, so asserting on
    which bullet's text appears in the message is what actually fails.
    """
    gaps = gap_audit("• Responsible for data pipelines\n• Cut latency from 900ms to 300ms\n")
    assert len(gaps) == 1
    assert "Responsible for data pipelines" in gaps[0]
    assert not any("Cut latency" in g for g in gaps)


def test_gap_audit_ignores_lines_with_no_bullet_marker() -> None:
    """Headings, names and contact lines in extracted text carry no bullet
    marker and must not be scored as if they were duty statements.
    Mutation: drop the `_BULLET_PREFIX` check so every non-blank line is
    scored -- the heading line below then gets flagged too, and `len == 1`
    fails.
    """
    text = "Dana Vex\nSenior Analyst, Northwind Analytics\n• Responsible for data pipelines\n"
    gaps = gap_audit(text)
    assert len(gaps) == 1
    assert "Dana Vex" not in gaps[0]


def test_gap_audit_caps_advisory_output() -> None:
    """A resume with many flaggable bullets produces a capped list plus one
    summary line, not one line per bullet. Mutation: delete the
    `len(flagged) <= MAX_GAPS` cap -- this then returns `MAX_GAPS + 5`
    plain entries with no summary line, and both assertions below fail.
    """
    bullets = "\n".join("• Responsible for various operational duties" for _ in range(MAX_GAPS + 5))
    gaps = gap_audit(bullets)
    assert len(gaps) == MAX_GAPS + 1
    assert "more" in gaps[-1]


def test_extraction_runs_regardless_of_the_callers_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Extraction must not depend on the caller's own working directory.

    In this project's own environment this contract already holds even
    without an explicit `cwd`: `core` is installed editable (a `.pth` entry
    in the venv's `site-packages`), so `sys.executable` finds it regardless
    of the child's working directory, and a mutation dropping `cwd=REPO_ROOT`
    from the `subprocess.run` call does **not** reproduce an import failure
    here (verified directly). This test still pins the observable behaviour
    down as a regression guard; `test_worker_pins_its_own_working_directory`
    below is the one that actually mutation-tests the `cwd=REPO_ROOT` line
    itself, since a caller relying only on an editable install being present
    is exactly the kind of environment detail this project prefers not to
    depend on implicitly.
    """
    out = tmp_path / "white.pdf"
    build(WHITE, out, FONT_DIR)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = extract_document(out)
    assert result.text


def test_worker_pins_its_own_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`extract_document` must pass `cwd=REPO_ROOT` to the worker subprocess
    explicitly, rather than leaving it to whatever directory the calling
    process happens to be running in when it invokes this function.
    Mutation: delete the `cwd=REPO_ROOT` keyword argument from the
    `subprocess.run` call in `core.ingest.extract` -- the captured call below
    then carries no `"cwd"` key at all, and `calls[0]["cwd"]` raises
    `KeyError` instead of comparing equal to `REPO_ROOT`.
    """
    out = tmp_path / "white.pdf"
    build(WHITE, out, FONT_DIR)

    calls: list[dict[str, object]] = []
    real_run = subprocess.run

    def spy_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("core.ingest.extract.subprocess.run", spy_run)
    extract_document(out)

    assert calls, "subprocess.run was never called"
    assert calls[0]["cwd"] == REPO_ROOT


def test_worker_timeout_is_reported_in_the_modules_own_vocabulary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`subprocess.TimeoutExpired` is not a `ValueError`
    (`issubclass(subprocess.TimeoutExpired, ValueError)` is `False`), so a
    file that merely hangs must not raise a different exception type than
    one that is malformed. Mutation: remove the `except
    subprocess.TimeoutExpired` clause in `extract_document` -- the raw
    `TimeoutExpired` then propagates, `pytest.raises(ValueError, ...)`
    below does not match it, and the test errors.
    """
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    def fake_run(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd=["python"], timeout=7.5)

    monkeypatch.setattr("core.ingest.extract.subprocess.run", fake_run)
    with pytest.raises(ValueError, match=r"7\.5"):
        extract_document(pdf, timeout_s=7.5)


def test_worker_failure_is_reported_not_raised_as_a_bare_parser_exception(tmp_path: Path) -> None:
    """A file whose header matches but whose body is not a real PDF makes
    the worker's own parser raise inside the child process (verified
    directly: this fixture makes `pdfplumber` raise `PdfminerException("No
    /Root object!")` and exit non-zero with a traceback on stderr); that
    failure must surface as this module's `ExtractionError`, carrying a
    piece of the worker's own traceback, not as an empty, silently-successful
    result. The assertion checks for `"Traceback"` rather than just "could
    not read": that weaker substring is also produced by the unrelated
    malformed-JSON branch, which a returncode-check mutation cannot reach
    (confirmed: disabling the `completed.returncode != 0` check outright
    still passed a `match="could not read"` assertion, because
    `json.loads("")` then raises and takes the *other* `ExtractionError`
    path). Mutation: replace `completed.returncode != 0` with `False` --
    this fails, because `stdout` is empty here and the resulting
    `json.JSONDecodeError` produces a message with no `"Traceback"` in it.
    """
    corrupt = tmp_path / "resume.pdf"
    corrupt.write_bytes(b"%PDF-1.4\nthis is not a real pdf body at all, just garbage\n%%EOF")
    with pytest.raises(ExtractionError, match="Traceback"):
        extract_document(corrupt)
