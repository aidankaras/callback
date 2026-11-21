# 03 — Open findings, and the defect class to watch

Defects found and verified but not yet fixed. Each entry is a reproduction, not a
description: paste it and watch it fail. Delete an entry when its fix lands and a
test pins it.

## Open

### A hedge anywhere earlier in the sentence softens a length knockout

`_role_lengths` treats a length as hedged when a hedge appears anywhere between
the clause start and the number, so "up to" qualifying a head count turns a
24-week role into a stretch. Admit direction: the posting is shown, not hidden.
Narrower reading: a hedge immediately before the number.

```python
from core.triage import Profile, check_eligibility

p = Profile(
    work_authorization="permanent resident",
    graduation="December 2026",
    degree="master",
    seasons=("summer",),
    max_weeks=12,
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
