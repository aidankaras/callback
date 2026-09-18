# Limitations

This document tracks what `callback`'s verification gates do and do not guarantee today, and
separately records functionality that is planned but not yet built, and functionality that is
explicitly out of scope rather than merely unimplemented.

## What's implemented today

- **Grounding**: every factual claim in a generated document is derivable from data the candidate
  supplied. This is a claim about traceability, not about truthfulness of the underlying facts —
  `callback` cannot verify that a fact the candidate entered is itself accurate — and it does not
  catch every kind of invented content; see Known constraints below for the specific gap.

## Planned, not yet built

No document rendering, text extraction, or metadata inspection exists in `core/` yet, so neither of
the following is checked today:

- **Parseability**: a document's rendered layout will be checked against an independent,
  deliberately pessimistic text extraction to confirm its field structure and reading order survive
  intact. This will prove the document is *structurally* unambiguous to a generic text extractor —
  it is not intended as a compatibility claim about any specific applicant-tracking system.
- **No hidden channels**: extracted text will be checked against visible text, and document metadata
  will be constrained to carry nothing beyond title and author, ruling out a document that presents
  different content to a human reader than to an automated one.

## Explicit non-goals

- No job-board search, scraping for open positions, or credential storage.
- No browser automation or automated submission to any platform.
- No attempt to evade or test against AI-generated-text detectors.
- No claim of compatibility with any named applicant-tracking system or vendor.
- No at-rest encryption of locally stored data.

## Known constraints

- The grounding check operates on the facts and derivations it is given; it cannot detect a fact
  that is internally consistent but factually false.
- The grounding check's entity matching is narrow by design: a word in generated text is checked
  against the cited facts only when it is a known technology (matched case-insensitively against
  the alias table) or "identifier-shaped" — it contains a digit, `+`, `#` or `.`, or is 2-6 letters
  that are all capitals — and starts with a capital letter. An invented tool or product name that is
  neither a known alias nor identifier-shaped (a plausible-looking but nonexistent name in ordinary
  title case) passes through unchecked; catching that class of fabrication is a job for later
  review, not for this gate.
- The grounding check's number matching is by value, not by occurrence: it asks "does some cited
  fact or derivation produce this value anywhere," not "does this specific claim in the text trace
  to this specific fact." A bullet with two independent numeric claims that happen to share a value
  (a genuinely sourced `42` and an unrelated, fabricated `42%` in the same sentence) will ground
  both from the one stored figure. Linking a specific span of text to a specific fact is out of
  scope.
- The sensitive-attribute screen on fact storage is a heuristic refusal of self-attributed
  protected attributes (e.g. "I am married", "Date of birth: ..."), not a general PII classifier;
  it may occasionally refuse legitimate text that happens to match a pattern, and the remedy is to
  rephrase the fact rather than to bypass the check.
