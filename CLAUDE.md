# Working in this repository

Guidance for anyone, human or agent, contributing to callback.

## What this project is

callback is a Python package (`core`) of deterministic quality gates for a generated
document — grounding, round-trip parseability, hidden-channel detection, layout lints,
and a hash-chained ledger — that will later be exposed over MCP as a Claude Code plugin.
Phase 1 builds and proves the deterministic substrate with no LLM anywhere in `core`;
the plugin surface (MCP server, playbook skills, subagent definitions, write guard) is
the final phase-1 task, built once the substrate underneath it is trustworthy.

## Non-negotiables

These are correctness constraints, not preferences. A change that violates one is a bug
even if every test passes.

1. **No LLM call anywhere in `core`.** If a function needs a judgement call, it belongs
   in a playbook (phase 2+), not in this package. `core` exists so that a quality claim
   is verifiable by rerunning it, not by trusting a model's self-report.
2. **No PyMuPDF, ever.** It is AGPL; this repo is MIT. PDF parsing is `pdfplumber` (MIT)
   and `pypdf` (BSD-3) only. This is a licensing constraint, not a technical preference —
   do not reach for PyMuPDF to fix a parsing gap no matter how well it would close it.
3. **`uv` only, with `uv.lock` committed.** CI and the MCP server both run
   `uv run --locked --no-sync`; a dependency change that is not reflected in the
   committed lockfile will pass locally and fail in CI or at plugin-install time.
4. **`pyright` strict, zero errors. `ruff` lint and format, both checked in CI — not
   lint alone.** `DTZ` rules are on: every date/time value is timezone-aware. A naive
   `date.today()` in a stored field is not a style nit; it is undefined behavior across
   a DST boundary or a machine in a different timezone, and it has already been caught
   once here (Task 3: the fact-store default was corrected to
   `datetime.now(tz=UTC).date()` before it shipped).
5. **User data lives only under `$CALLBACK_HOME`** (default `~/.callback`), directories
   `0700`, files `0600`. `core.paths.resolve_inside` is the only sanctioned way to turn a
   relative name into a path; anything that builds a path by string concatenation instead
   is a bug, because it bypasses the traversal and symlink-escape checks that make the
   later write guard (phase-1 Task 22) meaningful.
6. **No `Co-Authored-By` trailer, in any commit, ever.** Verified by grep before every
   task closes in this build; keep doing that.
7. **Audience: every docstring, comment, commit message and doc is written for a senior
   engineer reading this repo cold.** Never reference a plan, a task number, a brief, a
   report, "the controller," "the reviewer," or how a piece of code was produced. This
   constraint has already been violated and caught twice in seven tasks — a docstring in
   `core/checks/derive.py` once read "see the task report for a documented case" (Task 6
   review), and an earlier docstring in `core/paths.py` plus a commit message referenced
   a task number by name (Task 2). Both required a history rewrite to remove. Before any
   commit, grep the diff for `\bTask [0-9]+\b|superpowers|sdd/|task report|the brief|the
   plan\b|the ruling|controller|reviewer|implementer` — this is the standing pattern this
   build converged on after the leak recurred once with a narrower pattern.
8. **Planning documents live outside the repo.** The spec and phase plans live under
   `~/.claude/references/callback/`, never inside this tree, so planning and personal
   context can never reach what will become a public repo. Do not create a `docs/plan/`
   or similar inside this tree, however convenient it seems mid-task.
9. **No PII in the repo, checked structurally, not by memory.** `.gitignore` already
   covers `*.db`, `*.pdf`, `inbox/`, `outputs/`, `.callback/`. All fixtures are synthetic
   personas — a real resume, including the maintainer's own, must never produce a
   committed artifact, including a README screenshot.
10. **A gate never returns a bare bool.** Verdicts are three-valued (`pass` / `warn` /
    `fail`) wherever the spec says so, because collapsing "derived and verified" and
    "literally absent" into the same boolean is exactly the failure mode that made v1 of
    this project's grounding rule false-fail 6 of 7 legitimate bullets before this phase
    began (spec §0.2.1) — the type signature is what prevents that regressing silently.
11. **Never pipe a gating command into `tail`/`head` inside an `&&` chain.** The exit
    status becomes the pager's, not the command's, and a red CI run can go green for the
    wrong reason.

12. **Stage commits by filename, never `git add -A`.** More than one contributor can have
    files in the working tree at once, and a blanket add commits someone else's
    half-finished work. This is not hypothetical: it was caught here with three untracked
    files belonging to unrelated work sitting in the tree at commit time.
13. **A fixture that renders through Typst raises on a non-empty warnings list.** A warning
    during fixture construction means the fixture is not the document the test believes in,
    and an adversarial fixture that quietly failed to become adversarial is the most
    expensive kind of passing test. See the failure modes below.

## Ways a check can pass without running

The non-negotiables above constrain what the code must do. These constrain what may be
*claimed* about it, and they share one shape: a verification that reports success without
having executed. The check was not wrong — it never ran, said nothing, and silence renders
as a pass.

The diagnostic question is therefore not "is this check correct?" but **"what would it print
if it hadn't happened?"** When the answer is the same thing it prints on success, it belongs
in this section. Every entry below was found the hard way, and each was invisible until
someone asked that question of it.

- **A claimed test count is verified independently, not trusted.** This build has already
  shipped one report that stated "28/28 full suite" passing when the real, independently
  verified count was 39/39 (Task 4) — nothing was lost, but the report was wrong, and the
  report is the only evidence a reviewer has unless they rerun the suite themselves.
  Before treating any "N passed" claim as done, run `uv run pytest -q` and read the tail
  line yourself.

  When more than one contributor has files in the tree, the full-suite total is **not**
  attributable to any one change: uncommitted work from elsewhere is collected too. A suite
  that goes from 210 to 221 across a commit adding two tests is not a discrepancy to chase —
  it is nine tests belonging to someone else. Report the count for the file you changed, and
  read the whole-suite number as a check that nothing broke rather than as a measure of what
  you added.
- **A mutation's restore is verified, not assumed.** Copy the file aside, restore from the
  copy, and `cmp` to confirm the restore happened. `git checkout -- <path>` silently does
  nothing on an untracked file — it prints a pathspec error and returns non-zero, which a
  script without `set -e` walks straight past. A sibling project stacked four mutations on
  one file that way, and every result after the first measured a file nobody had read; the
  output table looked entirely normal, because nothing in it could say "these rows are
  garbage."
- **A mutation is run with the bytecode cache cleared, or its result means nothing.**
  `find . -name __pycache__ -type d -exec rm -rf {} +` before each one, or
  `PYTHONDONTWRITEBYTECODE=1`. CPython invalidates a `.pyc` on the source's size and its
  mtime truncated to whole seconds, so a same-length edit tested within one second never
  takes effect and the suite stays green for the wrong reason. Failure mode 7 below has the
  measurement.
- **Reasoning that outlives its evidence is an anecdote.** A conclusion recorded without
  the measurement that produced it cannot be re-checked when the method turns out to be
  flawed — it can only be withdrawn. This applies to where the record lives as well as
  whether it exists: notes kept only in a git-ignored scratch directory read as absent once
  that directory is removed, and absent is indistinguishable from never-written. Design
  decisions a future reader would question belong in `docs/lessons/`, not in a working file.
- **Pinned Action SHAs and dependency versions are verified against the source, not
  assumed correct because they appear in a plan.** `gh api repos/<org>/<repo>/git/ref/tags/<tag>
  --jq .object.sha` before any CI change lands (this caught a wrong SHA in the plan itself
  during Task 1).

## Failure modes this codebase has actually hit

Read this section before reviewing a diff here. Every entry is a real defect from this
repository, with the instance that produced it. They are listed because reviewing against
a project's own history finds more than reviewing against a generic rubric, and because
each of these survived at least one review that was not looking for it.

**1. A check that cannot fail.** This has appeared three times, by three different
mechanisms, and reading the assertion catches none of them — only running the failing state
does.

- *The condition is unobservable.* A determinism test asserted a rendered PDF carried no
  hyphenation artefacts. Setting `hyphenate: true` in the template and re-rendering **still
  passed**: the assertion never inspected anything hyphenation changes. Three reviewers
  approved it.
- *The bound is too loose to separate right from wrong.* `assert last_line_words >= 1`
  passes whether the property indexes the last line or the first. `assert 0 < page_fill < 1`
  passes whether the denominator subtracts the page margins or not — the wrong denominator
  measured 0.1948, comfortably inside. Both were replaced with the measured value.
- *The adversarial input is not adversarial.* A two-column fixture built to prove the
  pessimistic extractor scrambles reading order used 39 entries, which is **exactly one
  column's capacity** on that page. The body rendered wholly in column one, 0 of 273 words
  landed in the right half, and reading order came out perfectly ascending, so
  `assert order != sorted(order)` could never fail.

The remedy that works: name the mutation, then actually apply it, watch the test fail,
revert, watch it pass. And **a negative fixture must assert its own premise** — before
asserting the hostile condition is detected, assert the fixture exhibits it.

**2. A string-matching primitive needs a fixture where the values collide, not just where
the layout misbehaves.** The round-trip gate passed a document whose reading order was
fully inverted, reporting `verdict=pass, order_ok=True, char_ratio=1.0`. Cause: a declared
heading `"Analytics"` matched inside a different entry's `"Northwind Analytics Group"`, so
the order cursor pinned to the wrong segment and never advanced. The layout axis had been
tested exhaustively — one column, two columns, several entry counts, several font sizes —
while every fixture used mutually non-substring headings, so the value axis was never
tested at all.

**3. A document-scoped check hides per-entity content loss.** Field recovery searched every
segment for every declared value, so an ordinary case defeated it: two jobs in the same
city, the renderer loses one entry's location, and the *other* entry's identical
`"Boston, MA"` satisfies the claim. The gate reported a field it could not see as recovered
and passed the document. Fields are now scoped to their own entry's region of the page.

**4. A test that reimplements the code it checks cannot notice that code changing.** A
threshold test recomputed `_space_advance`'s median formula locally, because importing the
private helper tripped a strict-typing rule, and carried a comment asking future readers to
keep the copy in step by hand. Changing the real statistic to a mean would have left the
test passing against its own copy while the production threshold moved. Assert the
*behaviour* instead: what gets split, not the arithmetic that decides it.

**5. Prose that outruns or misstates its own measurements.** Four instances, all in
explanatory text written carefully:

- "three orders of magnitude inside the tolerance" where the real ratio is 90x–160x — two
  orders.
- `unicodedata.normalize("NFKC", ...)` credited with folding dashes and curly quotes. It
  leaves every one of them unchanged; a translation table does that work.
- a right-half word count of "217 of 273" that spliced one fixture size's total onto a
  different size's measurement.
- a test named for an en dash that this renderer never emits: Typst folds `--` but leaves a
  single spaced hyphen alone, so the test passed with nothing to absorb.

A docstring that explains *why* something is true is a second claim, independent of the
first, and the tests only ever check the first. Every instance of this found here had a
correct conclusion resting on a false mechanism: the NFKC entry above is one, and a
determinism note once justified a decision by a precision loss that does not occur — the
decision was right, the stated reason was not, and no passing test could have said so
because the defeat was in the explanation rather than the behaviour. When a comment answers
"why", verify the "why" separately from the "what".

A number in a docstring is a claim. Re-derive it with the function it describes, not with an
equivalent you wrote while documenting — `statistics.median` averages the two central values
of an even-length list where this codebase's helper takes the upper one, which is how a
whole table of figures came to be 2–3% wrong.

**7. A mutation that preserves a file's byte count can be invisible, which makes the
mutation evidence itself vacuous.** CPython invalidates a cached `.pyc` on the pair
(source mtime **truncated to whole seconds**, source size). A mutate-test-revert cycle
that rewrites a file to the same length inside one second therefore runs against stale
bytecode, and the mutation never takes effect. Measured directly — writing `VALUE = 9` over
`VALUE = 1` within the same second imports as `1`, while `VALUE = 123` over `VALUE = 1`
imports as `123`, because only the second changes the size.

The direction of the error is worth stating, because it decides what an uncleared run is
evidence of. A mutation that never takes effect leaves the suite green, which is
indistinguishable from the suite genuinely failing to notice the change. So the failure
always lands on the same side: it **manufactures survivors**. Using a mutation to prove a
test bites, that is the conservative direction — the test looks weaker than it is and you
strengthen something already sound. Computing a mutation score across a suite, it is not:
the score understates the suite, and every phantom survivor invites a redundant test.

This was caught here on a `1pt` → `8pt` edit in a fixture, which is size-preserving, and it
threatens every mutation claim in this repository rather than one test. Two mutations used
during this build were size-preserving (`gap_factor: float = 2.0` → `0.3` among them) and
had to be re-verified afterwards to confirm the evidence was real.

When running a mutation, either change the byte count, clear `__pycache__`, or set
`PYTHONDONTWRITEBYTECODE=1`. Prefer clearing the cache: it costs nothing and does not make
the choice of mutation depend on an implementation detail of the interpreter.

**6. Record a tolerance's margin, not its ratio.** "A tolerance of 0.3x the font size" tells
a reader nothing about whether it is safe. "3.0pt against this template's 5.5pt line pitch,
roughly 1.8x" tells them what breaks it. Where a default is safe only because of a template
fact, name the fact: the bullet-continuation threshold is safe because the marker occupies
its own 10pt grid column, which is a 10x margin against flush-left headings.

## Commands

```bash
uv run pytest -q                  # whole suite; -m slow selects the PDF-rendering tests
uv run ruff check .               # lint
uv run ruff format --check .      # format, checked separately -- lint alone is not enough
uv run pyright                    # strict; never `.venv/bin/pyright`, which resolves a
                                  # different environment and reports phantom errors
uv run callback --help            # the console entry point
```

All four gates pass before any commit. Run them as `uv run <tool>`, the way CI does, rather
than by invoking a binary path directly.
