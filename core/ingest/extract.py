"""Reading a document this project did not produce.

An imported resume is untrusted twice over: it may have been written by
another tool entirely, and a PDF can carry text that a reader never sees.
`extract_document` never hands that text to a caller without first running
it through `core.checks.hidden` and reporting whatever that gate finds,
because a document built outside this project's own renderer can exercise
every channel that gate documents (see its module docstring for the ones it
checks and the ones it names as still open). The extracted text is returned
*in full*, hidden content included: a finding names the channel and the page,
not which part of `text` is affected, so a caller must treat a non-empty
`suspicious` list as a reason not to trust `text` at all.

**Why a subprocess, given `core` never touches the network.** A subprocess
is not a network call, so it does not violate that boundary — the reason to
spawn one here is different: it is the only cheap way to make a hostile
file's failure mode a *disposable* one. A PDF that is merely malformed makes
the parser raise, which is easy to catch in-process; a PDF built to make the
parser hang or to exhaust memory is not, because both failure modes can take
the caller's own process down with it. Running the parse in a child process
turns "hangs" into "times out" and "exhausts memory" into "the child dies,"
at the cost of one process spawn per import — an entirely acceptable price
on a path that runs once per document a person hands over, not in a hot
loop.

**Only `.pdf` is supported.** `.docx` import is deliberately not built:
accepting a second container format would double the untrusted-parsing
surface (a ZIP archive plus embedded XML, with its own history of entity-
expansion and decompression-bomb attacks) for a format this project does
not yet have a documented need to read. Handing it a `.docx` raises the
same "unsupported file type" error as any other unrecognised extension,
not a special case — see `test_docx_is_not_supported_yet` for the behaviour
this pins down.

**Why `path` is not resolved through `core.paths.resolve_inside`.** That
function's job is turning a relative *name* into a path underneath this
project's own private data directory, rejecting anything that would escape
it — it protects this project's own storage tree from a traversal or
symlink attack originating from inside the project. `path` here is the
opposite kind of value: an absolute location the person importing a resume
names themselves, pointing at a file that lives outside that data directory
by design, since the whole point of importing is reading something this
project did not put there. Routing it through `resolve_inside` would not
add a security property; it would just make importing a file from anywhere
other than the data directory impossible. If a caller later wants to retain
a copy of an imported file inside the data directory, that copy's
*destination* path is exactly the kind of value `resolve_inside` exists to
guard — this module only ever reads from `path`, never writes to it or
derives a stored path from it.
"""

from __future__ import annotations

import json
import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from core.checks.hidden import CANNOT_JUDGE, HiddenFinding, HiddenKind

REPO_ROOT = Path(__file__).resolve().parents[2]

MAGIC: dict[str, bytes] = {".pdf": b"%PDF"}

# Deliberately weak, and deliberately advisory only -- see `gap_audit`'s own
# docstring for why "starts with a number or one of these verbs" is not a
# real signal for "the candidate produced an outcome" and must never gate
# anything, only suggest a second look.
_ACTION = re.compile(r"\b\d|\b(?:reduced|cut|raised|grew|shipped|launched|saved|improved)\b", re.I)

# A leading bullet, dash or asterisk marker followed by whitespace -- the
# shape this project's own template emits (a literal bullet glyph in its
# own grid cell; see `templates/resume.typ`) and the shape a resume built
# elsewhere in Word or a plain list typically flattens to once extracted
# as text. Written with explicit code points, not literal glyphs, so
# nothing in this line is visually ambiguous in a diff: U+2022 bullet,
# U+002A asterisk, U+002D hyphen-minus, U+2013 en dash, U+2014 em dash.
_BULLET_PREFIX = re.compile(r"^[\u2022*\-\u2013\u2014]\s+")

MAX_GAPS = 20

# Run in a child process: `pdfplumber.open` and `check_hidden` both parse a
# file this project did not produce, and a file crafted to make that parsing
# hang or exhaust memory should only ever take down a disposable child, not
# the caller. `sys.executable` and `str(path)` are the only two values that
# vary; there is no `-c` shell involved, no `shell=True`, and nothing from
# `path`'s own bytes is ever interpreted as part of the command line.
_WORKER = """
import json
import sys
from pathlib import Path

import pdfplumber

from core.checks.hidden import check_hidden

path = Path(sys.argv[1])
with pdfplumber.open(str(path)) as pdf:
    text = "\\n".join(page.extract_text() or "" for page in pdf.pages)
    pages = len(pdf.pages)
_, findings = check_hidden(path)
found = [{"kind": str(f.kind), "detail": f.detail} for f in findings]
print(json.dumps({"text": text, "pages": pages, "findings": found}))
"""


def _worker_failure(completed: subprocess.CompletedProcess[str]) -> str:
    """Why the worker failed, in one line: how it exited, then the last line
    of its stderr -- a Python traceback's final line is the exception, and its
    first four hundred characters are not."""
    code = completed.returncode
    if code < 0:
        try:
            how = f"killed by {signal.Signals(-code).name}"
        except ValueError:
            how = f"killed by signal {-code}"
    else:
        how = f"exited with status {code}"
    lines = completed.stderr.strip().splitlines()
    return f"{how}: {lines[-1]}" if lines else how


class ExtractionError(ValueError):
    """Raised for every way `extract_document` refuses or fails to read a file.

    Kept as the one exception type this module raises -- including a worker
    timeout, which `subprocess.TimeoutExpired` does not subclass -- so a
    caller can catch a single type regardless of which stage failed: the
    extension check, the magic-byte check, or the subprocess itself.
    """


@dataclass(frozen=True)
class ExtractResult:
    """What came out of an imported document.

    `findings` is exactly what `core.checks.hidden.check_hidden` reported
    for this file (`suspicious` is the same list as `"kind: detail"` text,
    and `unscreened` is the subset meaning a page could not be judged) -- an
    empty list means that gate raised no finding, not that the document is
    proven free of every hidden channel. That gate documents several blind
    spots of its own (zero opacity, clipping, a horizontal-only squash, an
    opaque box colour-matched to the text in front of it, and a biased
    CMYK-to-RGB conversion), and it can also report `background_unknown`
    when a page's background cannot be determined at all -- a realistic
    outcome for an imported file with a gradient or patterned page fill,
    not an exotic one, and it means invisible-text screening could not run
    on that page rather than that the page passed it.
    """

    text: str
    findings: list[HiddenFinding]
    pages: int

    @property
    def suspicious(self) -> list[str]:
        """Every finding as `"kind: detail"` text."""
        return [f"{f.kind}: {f.detail}" for f in self.findings]

    @property
    def unscreened(self) -> list[HiddenFinding]:
        """The findings that mean part of the document could not be screened
        at all, as distinct from screened and found suspicious."""
        return [f for f in self.findings if f.kind in CANNOT_JUDGE]


def extract_document(path: Path, *, timeout_s: float = 20.0) -> ExtractResult:
    """Extract text from `path`, screening it for hidden content first.

    Raises `ExtractionError` (a `ValueError`) if `path`'s extension is not
    supported, if its first bytes do not match that extension, if the
    worker process times out, or if the worker process fails for any other
    reason. Never falls back to parsing in-process on any of those paths --
    a file that cannot be read safely in the sandboxed child is not read at
    all.
    """
    suffix = path.suffix.lower()
    if suffix not in MAGIC:
        raise ExtractionError(f"unsupported file type {suffix!r}; only .pdf is supported")

    # Bounded read: four bytes, not the whole file. A file chosen by someone
    # else has not been validated at all yet at this point, so reading it in
    # full just to inspect its header would let an oversized file cost
    # memory before a single check has run.
    try:
        with path.open("rb") as handle:
            header = handle.read(len(MAGIC[suffix]))
    except OSError as exc:
        raise ExtractionError(f"could not read {path.name}: {exc}") from exc
    if not header.startswith(MAGIC[suffix]):
        raise ExtractionError(f"file contents do not match the {suffix} extension")

    try:
        # `cwd` is pinned explicitly rather than left to whatever directory
        # the calling process happens to be running in. On an editable
        # install, `core` is already importable from anywhere -- the same
        # interpreter's site-packages resolve it regardless of the child's
        # working directory -- but that is a property of how the package
        # happens to be installed, not a guarantee this function makes
        # about itself. Pinning `cwd` removes the dependency on that detail
        # entirely, at no cost.
        completed = subprocess.run(
            [sys.executable, "-c", _WORKER, str(path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=REPO_ROOT,
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        # `TimeoutExpired` is not a `ValueError`, so left uncaught it would
        # leave this module raising two unrelated exception types depending
        # on which failure a hostile file triggered. Re-raised in this
        # module's own vocabulary, with the timeout that was exceeded kept
        # in the message so a caller knows whether retrying with a longer
        # one is worth trying at all.
        raise ExtractionError(f"reading {path.name} timed out after {timeout_s}s") from err

    if completed.returncode != 0:
        raise ExtractionError(f"could not read {path.name}: {_worker_failure(completed)}")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as err:
        raise ExtractionError(f"could not read {path.name}: malformed worker output") from err

    try:
        findings = [
            HiddenFinding(HiddenKind(f["kind"]), str(f["detail"])) for f in payload["findings"]
        ]
    except (KeyError, TypeError, ValueError) as err:
        # An unrecognised kind is a screen this module cannot interpret, so it
        # is refused rather than dropped or passed through as plain text.
        raise ExtractionError(f"could not read {path.name}: unexpected finding {err}") from err
    return ExtractResult(text=payload["text"], findings=findings, pages=int(payload["pages"]))


def gap_audit(text: str) -> list[str]:
    """Flag bullet lines that read like a duty rather than an outcome.

    This is advice, never a verdict, and nothing in this project may gate on
    it. The signal is deliberately weak -- "the bullet contains a digit or
    one of a handful of outcome verbs" -- far too weak to compute a score
    from, because a past-tense verb is not evidence the candidate did the
    thing. Here that same weak signal only ever produces one line of
    advice to the person who will edit their own bullet, and a false
    positive costs them a single glance rather than a wrong number anywhere
    downstream.

    A line with no recognised bullet marker (`•`, `-`, `*`, an en or em
    dash, each followed by whitespace) is left uninterpreted rather than
    guessed at: extracted text from an imported document runs headings,
    a name, dates and contact details together with the actual bullets, and
    treating all of it as if it were a duty statement would flag a person's
    own name as "no outcome or figure" as readily as a real bullet.

    Output is capped at `MAX_GAPS` lines with a trailing summary line if
    more were found, because a resume with many bullets producing one flag
    per bullet stops being advice and starts being noise the person tunes
    out.
    """
    flagged: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        marker = _BULLET_PREFIX.match(stripped)
        if marker is None:
            continue
        body = stripped[marker.end() :].strip()
        if body and not _ACTION.search(body):
            flagged.append(f"no outcome or figure: {body[:70]!r}")

    if len(flagged) <= MAX_GAPS:
        return flagged
    shown = flagged[:MAX_GAPS]
    shown.append(f"...{len(flagged) - MAX_GAPS} more bullet(s) flagged, not shown")
    return shown
