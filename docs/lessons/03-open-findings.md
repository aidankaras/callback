# 03 — Open findings, and the defect class to watch

Defects found and verified but not yet fixed. Each entry is a reproduction, not a
description: paste it and watch it fail. Delete an entry when its fix lands and a
test pins it.

These are recorded here rather than in a working file because reasoning that
outlives its evidence is an anecdote — and because the most valuable thing about
each is the *reproduction*, which is the part a summary loses.

## Open

### A first `build` on a fresh home fails with a raw OS error

`build` creates `outputs/<job>/draft/` with `paths.private_dir`, which creates
directories up to `$CALLBACK_HOME` but never the home itself. A command that
calls `ensure_home` first (`profile`, for one) hides this; `build` as the first
command does not.

```bash
CALLBACK_HOME=$(mktemp -d)/home uv run callback build docs/example/resume.yaml --job x
# callback build: [Errno 2] No such file or directory: '.../home/outputs'
```

### `same_url` merges hash-routed postings

The comparison key drops the fragment, so two postings on a single-page board
that routes by fragment compare equal, and applying to one suppresses the
other's deadline and duplicate check.

```bash
uv run python -c 'from core.apply import same_url
print(same_url("https://jobs.example.com/#/job/123", "https://jobs.example.com/#/job/456"))'
# True
```

### A hedge anywhere earlier in the sentence softens a length knockout

`_role_lengths` treats a length as hedged when a hedge appears anywhere between
the clause start and the number, so "up to" qualifying a head count turns a
24-week role into a stretch. Admit direction: the posting is shown, not hidden.
Narrower reading: a hedge immediately before the number.

```python
from core.triage import Profile, check_eligibility

p = Profile(
    work_authorization="US citizen",
    graduation="May 2028",
    degree="bachelor",
    seasons=("summer",),
    max_weeks=14,
)
for t in [
    "We take 10 students for a 24-week internship.",
    "We take up to 10 students for a 24-week internship.",
]:
    print([(f.rule, f.level) for f in check_eligibility(t, p).findings])
# [('duration', 'knockout')]
# [('duration', 'stretch')]
```

### A "Graduates:" label hides a season

With the same profile, "Fall 2027 internship." is a season stretch;
"Graduates: Fall 2027 internship." produces no season finding at all. Admit
direction again.

## Watch — the round-trip gate's regional matching is the recurring defect class

Six of the seven `fix:` commits to `core/checks/roundtrip.py` have the same shape
(the seventh moved a skill check to the model boundary):
a declared value matched against the wrong region of the extracted text (a
value inside a longer value, one entry's field satisfied by another's, a
section's skills found in the section before or after it). Each is fixed and
pinned. The approach, containment matching region by region, is prone to this
by construction. Any new declared field or region needs a fixture in which
its value also appears in a neighbouring region, from the start.
