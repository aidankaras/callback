# 03 — Open findings, with reproductions

Defects found and verified but not yet fixed. Each entry is a reproduction, not a
description: paste it and watch it fail. Delete an entry when its fix lands and a
test pins it.

These are recorded here rather than in a working file because reasoning that
outlives its evidence is an anecdote — and because the most valuable thing about
each is the *reproduction*, which is the part a summary loses.

## Minor — a written minus sign is dropped

`core/checks/numbers.py`. `parse_numbers("Cut costs by -15%")` yields
`('15%', 15.0, True)`; the magnitude survives and the sign does not. Low impact,
since percentage comparison is deliberately by absolute value, but the token
itself is silently changed.

## Important — an import cannot tell a skipped screen from a finding

`core/ingest/extract.py`. `ExtractResult.suspicious` is a flat `list[str]` of
`f"{kind}: {detail}"`, so `background_unknown` ("the invisible-text screen did not
run on this page") and `invisible_text` ("it ran and found something") are told apart
only by parsing a string prefix — while `text` still carries whatever the skipped
screen would have caught.

A PDF whose page is covered by a shading-pattern fill, with white text on it:
`suspicious == ['background_unknown: page 0: ...']` and `text` contains the white
payload. Fix: return structured findings (kind plus detail) and name the pages the
screen could not run on separately, so a consumer cannot read one as the other.
