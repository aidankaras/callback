# 03 — Open findings, with reproductions

Defects found and verified but not yet fixed. Each entry is a reproduction, not a
description: paste it and watch it fail. Delete an entry when its fix lands and a
test pins it.

These are recorded here rather than in a working file because reasoning that
outlives its evidence is an anecdote — and because the most valuable thing about
each is the *reproduction*, which is the part a summary loses.

## Watch — the round-trip gate's regional matching is the recurring defect class

`core/checks/roundtrip.py` has taken seven `fix:` commits, all the same shape:
a declared value matched against the wrong region of the extracted text (a
value inside a longer value, one entry's field satisfied by another's, a
section's skills found in the section before or after it). Each is fixed and
pinned. The approach, containment matching region by region, is prone to this
by construction. Any new declared field or region needs a fixture in which
its value also appears in a neighbouring region, from the start. Recorded by
the standards audit.
