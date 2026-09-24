"""What a job's draft directory holds, and which of it records a judgement.

A draft holds the documents the candidate is working on and, beside each, the
record of the gates that judged it: a rendered PDF, a verdict, the hashes of
the bytes that were judged. Those records are about *those* bytes. Left beside
a different document they read as its own, and `core.apply` believes them.

The lists live here rather than in the modules that write them so that a
reader replacing a document -- `core.templates` -- clears every record rather
than the one list it happened to import, and so the next gate's artifacts are
cleared by adding them in one place.
"""

from __future__ import annotations

# `callback build`
RESUME_ARTIFACTS = ("resume.pdf", "report.md", "report.json")
# `callback letter`
LETTER_ARTIFACTS = ("cover-letter.pdf", "letter-report.md", "letter-report.json")
# `callback answers`
ANSWERS_ARTIFACTS = ("answers.md", "answers-report.json")

# Which template `callback template use` seeded the draft from, if any. Not a
# judgement of the document, so not among the records below: editing the
# draft is the point of a template, and the origin survives it.
ORIGIN = "origin.json"

# Everything in a draft that records a judgement of a particular document.
GATE_RECORDS = (*RESUME_ARTIFACTS, *LETTER_ARTIFACTS, *ANSWERS_ARTIFACTS)
