# Standards audit

Read-only audit of `callback` against eight claimed properties. Findings cite file/line;
absence of a finding in a section means nothing demonstrable was found there.

## 1. No fabrication enforced in code

The specific historical defect (a mutable `Provenance` letting one attribute
assignment flip trust levels) is fixed and directly tested:

- `core/profile/models.py:100-121` — `Provenance` is `frozen=True` and its
  `trust_matches_source` validator (`model_validator(mode="after")`) refuses
  to construct a `Provenance` where `source` starts with `"web:"` but
  `trust` isn't `Trust.WEB`, or `source` starts with `"import:"` and `trust`
  is already `Trust.USER_CONFIRMED`.
- `core/profile/models.py:124-167` — `Fact` itself is also `frozen=True`
  (not just `Provenance`), closing the second bypass the docstring names:
  `fact.provenance = Provenance(...)` replacing the whole field with a
  differently-trusted one without going through the validator. Only
  `model_copy(update=...)` can produce a changed fact, and that still
  constructs a fresh, validated `Provenance`.
- `tests/test_fact_store.py:150-183` exercises both: constructing a
  `web:`-sourced fact with `trust=user_confirmed` raises `ValidationError`,
  and `fact.provenance = Provenance(...)` on a frozen `Fact` raises.
  Confirmed passing: `uv run pytest -q tests/test_fact_store.py -k
  "provenance or frozen"` → `4 passed`.
- The one gate that actually decides whether a fact backs generated text,
  `Fact.selectable()` (`core/profile/models.py:165-167`), reads only
  `self.provenance.trust is Trust.USER_CONFIRMED` — a single frozen field,
  not a mutable derived flag — and `core/checks/grounding.py:459-467` and
  `core/checks/claims.py:140-142` both call it before using a cited fact for
  anything, failing closed (`UNSELECTABLE_FACT`) when it doesn't hold.

No fabrication path was found: constructing an unconfirmed `Fact` and then
mutating it into a confirmed one is refused at both the field level and the
whole-object level, and the check code paths that would use a fact's trust
level to admit content route through `selectable()` uniformly.

## 2. Grounding as derivability, not containment

The specific historical defect (any 1900-2100 integer inside a date hull
read as a year, so "Cut the $2020 licensing cost" passed grounding with no
findings) is fixed and directly tested:

- `core/checks/grounding.py:185-204` — `_looks_like_year` now only marks a
  value as *plausibly* a year; `_year_context` additionally requires a date
  preposition, a month name, or a dash/`to`/`through` range marker
  immediately before or after the number's span before it's read as a year
  at all (`check_bullet`, `core/checks/grounding.py:520-525`, requires all
  three: `_looks_like_year`, `_year_context`, and `_year_within_hull`).
- `tests/test_grounding.py:428-524` names this "Ruling 4" directly in a
  comment quoting the original failure mode, and tests both directions: a
  genuine year range in `from ... to`, hyphen, en-dash, month-prefixed,
  `in`/`until`/`since` forms all pass, while a dollar amount, a
  unit-suffixed quantity, a bare count and a "last quarter" count that all
  fall inside the same hull and the same 1900-2100 range are rejected as
  `unresolved_number` (`test_dollar_amount_that_looks_like_a_year_is_not_accepted`
  etc., lines 500-524). Confirmed passing: `uv run pytest -q
  tests/test_grounding.py -k "year"` → 12 passed, 0 failed.
- Recomputation (not string matching) is genuinely load-bearing:
  `core/checks/derive.py:118-177` (`compute`) executes `OPS[derivation.op]`
  against values pulled from the cited facts' own `numbers` dict — it never
  reads or trusts a value the model itself supplied — and
  `core/checks/grounding.py:497-519` compares a bullet's parsed numbers
  against this recomputed set, not against substrings of fact text.
- Structural claims (`core/checks/claims.py`) hold entry headers to
  equality, not containment, by design and by docstring
  (`core/checks/claims.py:8-17`; `_mismatches` at lines 115-130 uses `!=`
  comparisons field-by-field against exactly one anchor fact, not a
  containment search across every cited fact) — this is the fix for a
  different, related historical defect named in `CLAUDE.md` ("failure mode
  2": one entry's fields spliced from two different facts).

No remaining containment-style grounding logic was found in
`core/checks/grounding.py` or `core/checks/claims.py`.

## 3. Declared error boundaries hold

One violation of a module's own declared boundary was found and reproduced:

- **`core/ingest/extract.py:126-133`** — `ExtractionError`'s docstring
  states it is "the one exception type this module raises... so a caller
  can catch a single type regardless of which stage failed." But in
  `extract_document` (`core/ingest/extract.py:225-238`), only the
  `payload["findings"]` access is inside the `try`/`except (KeyError,
  TypeError, ValueError)` block (lines 230-237); the final `return
  ExtractResult(text=payload["text"], findings=findings,
  pages=int(payload["pages"]))` on line 238 reads `payload["text"]` and
  `payload["pages"]` *outside* that guard. Reproduced directly: monkeypatching
  `core.ingest.extract.subprocess.run` (the same technique the module's own
  `test_an_unknown_finding_kind_from_the_worker_fails_closed`, around line
  337-348, already uses) to return an exit-0 process whose stdout is valid
  JSON but omits `"text"`/`"pages"` (`'{"findings": []}'`) makes
  `extract_document` raise a bare `KeyError: 'text'`, not `ExtractionError`.
  In the current, trusted worker script (`_WORKER`,
  `core/ingest/extract.py:81-92`) all three keys are always emitted
  together on success, so this isn't reachable through the real subprocess
  today — but the module's own boundary claim is about the exception type
  the *function* can raise, not about what the current worker script
  happens to always do, and the test suite already establishes mocking the
  subprocess as the normal way this module is tested. No existing test
  covers a payload missing `text`/`pages`.

- `core/discovery/store.py:17-22` makes a weaker, explicitly non-exhaustive
  claim ("Every function here raises `DiscoveryError`... and `resolve_inside`
  may additionally raise `OutsideHomeError`... That pair, with pydantic's
  `ValidationError` and `OSError`, is what a caller must catch") — checked
  against `core.cli._discovery_errors()` (`core/cli.py:224-230`), which
  declares exactly `(DiscoveryError, ValidationError, OSError,
  OutsideHomeError)`. Traced `resolve_page` → `posting_at`
  (`core/discovery/page.py:433-453`) → `read_postings`
  (`core/discovery/page.py:340-412`): the one bare `ValueError` raiser in
  this file, `_date` (`core/discovery/page.py:296-312`), is called only from
  sites that locally catch `ValueError` (lines 368-371, 383-389, 390-393) —
  no bare `ValueError` escapes to a caller of `posting_at`. This module's
  boundary holds.

- `core/profile/store.py:81-92` (`StoreError`) makes the same kind of
  exhaustive claim for `add_fact`/`load_facts`. Traced every call
  `add_fact` makes (`screen_sensitive`, `canonicalize`, `Fact.model_copy`,
  `load_facts`, `save_facts`) — none can raise outside the declared
  `StoreError` family under normal Pydantic/YAML failure modes; no
  violation found here.

`BuildError`, `ApplyError`, `RenderError`, `FinalizeError`, `InterviewError`,
`AnswersError`, `ConfirmError` do not make an exhaustive "only this type"
claim in their docstrings, so they were not held to this standard.

## 4. Checks that can actually fail

Every specific historical instance CLAUDE.md names under "Failure modes this
codebase has actually hit" was checked against the current test suite and
found fixed, with the fix itself directly evidenced by a test that names the
original failure:

- The unobservable hyphenation assertion: `tests/test_render.py:102-136` now
  uses a bullet (`_HYPHENATION_BOUNDARY_BULLET`) specifically constructed and
  documented (lines 102-109) to wrap inside "infra-"/"structure" if
  `hyphenate` were true, rather than `DOC`'s original bullets, which the
  comment explains never wrap at all. `test_no_line_ends_in_a_hyphen`
  (line 132) would fail against a `hyphenate: true` template.
- The loose `last_line_words >= 1` and `0 < page_fill < 1` bounds:
  `tests/test_geometry.py:89-127` replaced both with measured, tight
  goldens (`== 5`, `0.20 < fills[0] < 0.23`) and each docstring states the
  specific wrong value (0.1948) the old bound would not have caught.
- The 39-entry two-column fixture that rendered entirely in one column:
  `tests/test_naive_extract.py:137-160` now asserts its own premise first
  (`len(right) > 100`, line 155) before trusting the scramble assertion, and
  asserts a specific expected interleaved order (`order[:4] == [1, 40, 2,
  41]`, line 158) rather than a bare `!= sorted(order)` inequality.
- The finding-kind-only assertion with no verdict check: every
  `findings[0].kind == ...` assertion found in `tests/test_grounding.py`
  (16 call sites) is paired with `assert verdict is Verdict.FAIL` (or the
  relevant verdict) immediately above it — confirmed by reading each site,
  e.g. lines 62-64, 96-97, 103-104, 109-115, 128-129, 145-146.
- The substring-collision defeat of round-trip order checking (`"Analytics"`
  matching inside `"Northwind Analytics Group"`): reproduced verbatim as
  `tests/test_roundtrip.py:194-258`
  (`test_colliding_heading_reports_out_of_order`), which documents the exact
  mechanism of the original defect in its own docstring and asserts
  `result.out_of_order == ["Northwind Analytics Group"]` on a document built
  to trigger it.
- `tests/test_roundtrip.py:139-190`
  (`test_two_column_document_fails_on_order_not_recovery`) isolates order
  from recovery explicitly: it asserts every critical field is recovered
  and `char_ratio >= 0.98` so that a future change weakening those other
  checks cannot make this test pass for the wrong reason — the test's own
  docstring states the entry count (20) is chosen to avoid a documented
  secondary effect above ~22 entries.

No new instance of an unfalsifiable check was found. A broad sweep for the
same shapes (`assert x >= 1`, `assert 0 < x < 1`, a finding-kind check with
no adjacent verdict check, a negative fixture with no premise assertion)
across `tests/test_lints.py`, `tests/test_hidden.py`, `tests/test_letter.py`,
`tests/test_answers.py` and `tests/test_outreach.py` turned up nothing of
the same shape as the fixed cases above.

## 5. Documentation does not outrun code

The specific historical defect named (README claiming gates with no
implementation) was not found to still apply: every row of README.md's
"What the checks guarantee" table and every command/exit-code claim in
`docs/using-callback.md` and `skills/tailor/SKILL.md` was checked directly
against the implementation and found accurate:

- The denied-domain list README/`docs/using-callback.md` both name
  ("LinkedIn, Indeed, Handshake, Workday and Glassdoor") matches
  `DENIED_DOMAINS` exactly (`core/discovery/fetch.py:31-32`:
  `{"linkedin.com", "indeed.com", "joinhandshake.com",
  "myworkdayjobs.com", "glassdoor.com"}`).
- `robots.txt` is genuinely honoured (`core/discovery/page.py:174-210`,
  `_allowed_by_robots`), not merely claimed.
- "Layout lints... can only warn. They never fail a document" —
  `core/gates.py:160-163` hardcodes the lints outcome to `"warn"` or
  `"pass"`, never `"fail"`, confirming the claim structurally rather than
  by convention.
- The MCP tool list README and `docs/using-callback.md` both give (`build`,
  `letter`, `answers`, `outreach`, `facts_add`, `facts_list`, `watch`,
  `triage`, `postings_take`, `postings_resolve`, `applications`, `funnel`,
  `eligibility`, `apply`, `interview`, `verify`) matches exactly the 16
  `@server.tool()`-decorated functions in `core/mcp_server.py`, and
  `facts confirm`/`delete-all` are confirmed absent from that file, matching
  the "deliberately not tools" claim in both the module docstring
  (`core/mcp_server.py:8-10`) and the docs.
- `callback import`'s claimed exit codes (`0` clean, `3` unreadable, `4`
  hidden content found or unscreened) match `_cmd_import`
  (`core/cli.py:677-703`) exactly: `return 3` on `(ExtractionError,
  OSError)`, `return 4 if result.findings else 0`.
- The ledger's claimed tamper-evidence ("Each ledger entry also carries an
  HMAC under the same key, so a ledger rewritten from scratch fails even
  when every entry chains correctly") matches `core/finalize.py`: `hmac.new`
  at line 61 (`_mac`), `hmac.compare_digest` checks at lines 177 and 208.
- `tests/test_docs_match_cli.py` exists specifically to keep the CLI help
  text and `docs/using-callback.md` from drifting apart, and passes
  (`uv run pytest -q tests/test_docs_match_cli.py` → 3 passed).

`docs/limitations.md` is unusually well hedged and dated where it makes a
network-reachability claim ("Checked 2026-09-22 and not read: SmartRecruiters'
and Recruitee's posting APIs...") — that specific claim is about third-party
services outside this repository and was not independently re-verified, since
`core` makes no network call during this audit and doing so was out of scope
for a read-only, offline review.

No present-tense claim of unimplemented behaviour was found in README.md,
`docs/using-callback.md`, `docs/limitations.md` or `skills/tailor/SKILL.md`.

One finding in the opposite direction — prose describing code that has since
been *removed*, not code that was never built — was found in
`docs/lessons/02-gate-result-shapes.md`:

- The document's main body (lines 1-91, "Decision" through "Implemented by")
  is written entirely in the present tense about a `GateResult` type and a
  `core/checks/result.py` module: "`GateResult` exists and only the grounding
  gate returns one" (line 3), and the "Implemented by" section (lines 84-91)
  names `core/checks/result.py` as implementing `GateResult` and
  `core/checks/grounding.py` as "the only producer, plus the two local
  imports that mark the cycle."
- `core/checks/result.py` does not exist (`ls core/checks/` lists `claims.py,
  derive.py, grounding.py, hidden.py, lints.py, naive_extract.py, numbers.py,
  roundtrip.py, skills.py, __init__.py` — no `result.py`), and a repo-wide
  search for `GateResult` and `check_document_gate` across `core/` and
  `tests/` returns nothing.
- The document's own final section, "Outcome (recorded later)" (lines
  93-103), says exactly this: "`GateResult` and `check_document_gate` were
  left with no production caller... Both were removed." This section was
  evidently appended after the fact, but the sections above it (including
  "The state a reader will find" and "Implemented by", which a reader
  checking the claim by hand would follow first) were not updated to match,
  so the document now contradicts itself internally — present tense claiming
  the type exists, then a later section saying it was deleted.

## 6. Design and economy

`git log --pretty=format: --name-only | grep '^core/.*\.py$'` was used to
count commits per file under `core/`, and the top results
(`core/cli.py`: 22, `core/checks/grounding.py`: 18,
`core/checks/roundtrip.py`: 11) were checked individually against the
instruction to look for a fix that recurs for the same class of defect.

- **`core/checks/roundtrip.py` is exactly this pattern, and the project has
  already found and fixed it.** Its `fix:` commit history
  (`9fe7d34`, `bad63b2`, `6a573e8`, `44b9e9e`, `e3d02fa`, `ef96a19`) is a
  repeated sequence of "a declared value matched against the wrong region of
  the extracted text" bugs — scope an entry's own fields, then identity
  fields, then section titles, then a skills-only section's boundary, then a
  skill found only after its own section title. `docs/lessons/03-open-findings.md`
  names this explicitly: "`core/checks/roundtrip.py` has taken seven `fix:`
  commits, all the same shape... Any new declared field or region needs a
  fixture in which its value also appears in a neighbouring region, from the
  start." The current code (`core/checks/roundtrip.py:202-249`) generalises
  this into a `_Scope` enum (`HEADER`/`SECTION`/`ENTRY`/`DOCUMENT`) with a
  single `_region` resolver, and the enum's own docstring narrates exactly
  this history ("Three other kinds of declared value were each, in turn,
  given `DOCUMENT` scope by omission before this type existed, and each time
  an unscoped search let a value missing from where it belonged be satisfied
  by an unrelated, genuine value somewhere else"). This is the recurring
  defect the audit was asked to look for, already recognised and consolidated
  into one primitive rather than left as five ad hoc per-field-type patches —
  worth noting as a positive instance of exactly the process CLAUDE.md asks
  for, not a currently-open design problem. `docs/lessons/03`'s framing as a
  "Watch," not a "fixed," is still accurate at a different level, though:
  `_Scope` organises *which region* is searched, but matching *within* a
  region (`_matches`, `core/checks/roundtrip.py:404-467`) is still containment
  based, so the class of bug (a short declared value matching inside a longer
  one) remains structurally possible within a single region — the note's
  closing guidance ("any new declared field or region needs a fixture...")
  is still live advice, not a stale warning about a solved problem.
- `core/checks/grounding.py`'s 18-commit history and `core/checks/numbers.py`'s
  6-commit history were both read in full
  (`git log --oneline --follow -- core/checks/grounding.py` /
  `core/checks/numbers.py`). Each `fix:` commit names a distinct defect class
  (digit runs in identifiers, hyphen components, bare-year context, percent
  sign and magnitude, grouped-decimal parsing, currency symbols) rather than
  the same bug recurring — this reads as incremental hardening of two
  genuinely hard parsing problems (natural-language numbers, natural-language
  dates), not a design problem in the sense the audit is checking for.
- `core/cli.py`'s 22 commits are consistent with it being the hub every new
  subcommand touches once (each `feat:` commit for a new command necessarily
  edits `cli.py` to register it) — not evidence of a recurring defect.
- Shared logic between the cover-letter, form-answer and outreach checkers is
  genuinely consolidated, not duplicated: `core/answers.py` and
  `core/outreach.py` both import `check_sentence`, `context_for` and
  `heading_findings` from `core/letter/checks.py`
  (`core/answers.py:28`, `core/outreach.py:22-23`), and `core/letter/checks.py`
  itself reuses `core/checks/grounding.check_bullet` and
  `core/checks/claims.identity_findings` rather than reimplementing bullet
  grounding for letters (`core/letter/checks.py:39-40`).

No abstraction earning nothing, and no case of over-consolidation needing a
split, was found in the modules read for this audit.

## 7. Dependency reality

Every third-party (non-stdlib, non-`core`) module imported anywhere under
`core/` was extracted with a Python AST walk (`ast.Import`/`ast.ImportFrom`
nodes, including lazy imports inside function bodies, across every `.py`
file in `core/`), not a text grep, so a deferred `import` inside a CLI
handler function is included:

```
mcp, pdfplumber, pydantic, pypdf, typst, yaml
```

Checked against `project.dependencies` in `pyproject.toml:6-13`:

```
"pydantic>=2.9", "pyyaml>=6.0.2", "typst==0.15.0",
"pdfplumber>=0.11.10", "pypdf>=6.0", "mcp>=1.0"
```

Every one of the six imported names resolves to a declared non-optional
dependency: `mcp` → `mcp>=1.0`, `pdfplumber` → `pdfplumber>=0.11.10`,
`pydantic` → `pydantic>=2.9`, `pypdf` → `pypdf>=6.0`, `typst` →
`typst==0.15.0`, `yaml` → `pyyaml>=6.0.2` (the import name for the `pyyaml`
distribution). None of the six is provided only by `[dependency-groups]
dev` (`pyproject.toml:19-20`: `pytest`, `hypothesis`, `ruff`, `pyright` —
none of which `core/` imports) or by an optional extra (this project
declares none). No import under `core/` was found that only resolves inside
the dev environment.

## 8. Prose by audience

`docs/lessons/*.md` is consistently well-suited to its stated audience (a
reviewer who will check the mechanism by hand): each file follows a
Decision → state-a-reader-will-find → why → what-was-considered-and-rejected
→ what-a-reviewer-asks structure, and specific, checkable numbers are given
throughout rather than qualitative claims — e.g. `01-adversarial-extraction.md`
gives the exact three-row text a real render produced
("`'Company 12 Extended International <location> <dates>'`...") rather than
describing the failure in the abstract, and `04-network-policy.md` gives the
exact four allowed hosts and five denied domains rather than "a short list."
Spot-checked against source and both were accurate (`core/checks/naive_extract.py:31,142`
for the banding formula; `core/discovery/fetch.py:28-32` for the host lists).

The one passage that does not fit either of its two readers is
`docs/lessons/02-gate-result-shapes.md`'s "Implemented by" section discussed
under Property 5 above: a reader orienting themselves reads it as current
state and is misled into looking for a file that no longer exists; a reader
checking the mechanism by hand (the audience this whole document series is
written for, per `docs/lessons/README.md`) hits a contradiction between the
document's own present-tense body and its "Outcome (recorded later)" section
that the document itself does not resolve — the "Implemented by" list was
left unedited when the outcome invalidated it.

No section written for a newcomer-orientation audience (README.md,
`docs/using-callback.md`) was found to be too dense to orient with — both
stay at the level of naming a command, a table row, or a one-sentence
guarantee, and defer specifics to `CLAUDE.md` or the `docs/lessons/` notes
for a reader who wants the mechanism. No section written for a
mechanism-checking audience was found to be too vague to verify — the
`CLAUDE.md` non-negotiables and `docs/lessons/` notes consistently name the
exact file, function, or measured number a claim rests on.
