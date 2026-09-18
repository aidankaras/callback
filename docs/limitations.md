# Limitations

This document tracks what `callback`'s verification gates do and do not guarantee, and records
functionality that is explicitly out of scope rather than merely unimplemented.

## What the verification gates prove

- **Grounding**: every factual claim in a generated document is derivable from data the candidate
  supplied. This is a claim about traceability, not about truthfulness of the underlying facts —
  `callback` cannot verify that a fact the candidate entered is itself accurate.
- **Parseability**: a document's rendered layout survives an independent, deliberately pessimistic
  text extraction with its field structure and reading order intact. This proves the document is
  *structurally* unambiguous to a generic text extractor — it is not a compatibility claim about any
  specific applicant-tracking system, and none is made.
- **No hidden channels**: extracted text matches visible text, and document metadata carries nothing
  beyond title and author. This rules out documents that present different content to a human reader
  than to an automated one.

## Explicit non-goals

- No job-board search, scraping for open positions, or credential storage.
- No browser automation or automated submission to any platform.
- No attempt to evade or test against AI-generated-text detectors.
- No claim of compatibility with any named applicant-tracking system or vendor.
- No at-rest encryption of locally stored data.

## Known constraints

- PDF parsing depends on `pdfplumber` and `pypdf`; documents produced by paths those libraries
  cannot parse reliably will fail the parseability gate rather than pass silently.
- The grounding check operates on the facts and derivations it is given; it cannot detect a fact
  that is internally consistent but factually false.
