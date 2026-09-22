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
