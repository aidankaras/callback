# 03 — Open findings, with reproductions

Defects found and verified but not yet fixed. Each entry is a reproduction, not a
description: paste it and watch it fail. Delete an entry when its fix lands and a
test pins it.

These are recorded here rather than in a working file because reasoning that
outlives its evidence is an anecdote — and because the most valuable thing about
each is the *reproduction*, which is the part a summary loses.

## Critical — a pattern text fill defeats the contrast check

`core/checks/hidden.py`. `to_rgb` returns `None` for a fill it cannot read as a
colour. `_invisible_fill` appends a contrast score only when that parse succeeds,
then guards on `if contrasts and max(contrasts) < MIN_CONTRAST_RATIO`. With
nothing parsed the list is empty, the guard is false, and the glyph is treated as
**visible**.

```typst
#text(fill: gradient.linear(white, white))[Python AWS Kubernetes React TensorFlow]
```
Measured: the run's `non_stroking_color` is `('p0',)` — a pattern name.
`check_hidden` returns `('pass', [])`, and `extract_text()` returns the payload
verbatim. Invisible, machine-readable keyword stuffing passes clean.

This is the same ambiguity `page_background` was changed to report as
`background_unknown`, left unfixed one level down. The fix is the same shape: a
glyph whose fill and explicit stroke both fail to parse is a case this module
cannot answer, and saying so beats guessing.

## Critical — an empty document passes grounding

`core/checks/grounding.py`.

```python
check_document([])  # (Verdict.PASS, [])
check_document_gate([])  # verdict=Verdict.PASS, ok=True
```

`check_document` loops over its bullets; with nothing to iterate, no verdict
check fires and it falls through to `PASS`. That output is byte-identical to a
document whose every bullet was independently verified. An upstream assembly bug
that yields zero bullets therefore reports the document clean.

## Critical — a skills-only section's region is unbounded

`core/checks/roundtrip.py`. `_section_regions` narrows a section's `end` from its
own entries' anchors; a skills-only section has no entries, so `end` is always
`None` and its region runs to the end of the document regardless of what follows.
A declared skill absent from its own section can then be satisfied by an unrelated
bullet in a later section — the fifth instance of the pattern this module's scope
machinery was built to close, leaking through the one category that has no anchors
of its own.

## Important — a page with no text layer passes every check

`core/checks/hidden.py`. An image-only page has no characters, so
`_invisible_fill`, `_tiny_text` and `_offpage_text` iterate nothing and
`_unicode_channel` reads `""`. A page that defeats extraction *completely* gets
the same clean verdict as a fully legible one. Distinct from the five documented
blind spots, none of which describe an absent text layer.

## Important — `page_background` assumes white on a degenerate page

`core/checks/hidden.py`. The `area > 0` guard that avoids a division by zero also
skips the whole rect scan, so a zero-height page falls into the "no qualifying
rect, assume white" branch instead of returning `None`. Narrow trigger — Typst
will not produce such a page — but it reintroduces, on an uncovered path, exactly
the silent-white default the gradient fix removed.

## Important — a blank bullet with a valid source grounds at the strongest verdict

`core/checks/grounding.py` with `core/resume/models.py`. `Bullet.text` carries no
minimum length, so an empty bullet makes no checkable claim and every loop in
`check_bullet` finds nothing to flag. Reported by review with a demonstration;
not independently reproduced here, because constructing the fact it needs hit an
unrelated validation error.

## Minor — a written minus sign is dropped

`core/checks/numbers.py`. `parse_numbers("Cut costs by -15%")` yields
`('15%', 15.0, True)`; the magnitude survives and the sign does not. Low impact,
since percentage comparison is deliberately by absolute value, but the token
itself is silently changed.
