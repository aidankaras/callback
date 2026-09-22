# Limitations

What `callback`'s checks do and do not guarantee, what is not built yet, and what is out of scope
by design rather than merely unimplemented.

## What the checks cannot know

- **Whether a confirmed fact is true.** Every guarantee is about traceability: a document says
  nothing its cited, candidate-confirmed facts do not. A fact the candidate confirmed but that is
  false passes everything.
- **Ordinary wording.** Bullets and cover-letter sentences are checked for numbers, dates,
  technologies, mid-sentence capitalised names, a fixed list of claim words, and spelled-out
  numbers. Ordinary verbs and nouns are not compared: "led the migration" and "helped with the
  migration" ground identically.
- **Which occurrence of a value a claim means.** Numbers match by value against the cited facts,
  not by position. A quantity recorded with a percent unit cannot back a plain count, but a quantity
  recorded with no unit can back either.
- **Whether a saved posting is the real one.** A cover letter's claims about the employer are held
  to the posting saved in `inbox/<job>.md`. `callback` does not fetch postings, so it cannot tell a
  genuine posting from an edited one.

## Guards that are defence in depth, not containment

- **The write guard** refuses an assistant's writes to the fact store, the application record,
  sealed documents and itself. For Bash, it resolves the paths a command names, after `cd`,
  `pushd` and `-C`, and with `~`, `$HOME` and `$CALLBACK_HOME` expanded. It does not catch a path a
  command builds at run time: from another variable, `find`, an encoded string, or a script it runs.
  It guards against accidental writes, not a determined process.
- **Confirmation needs a terminal.** `callback facts confirm` reads from `/dev/tty`, so a process
  without one cannot confirm a fact. Anyone at the candidate's own terminal can.
- **The hidden-content screen** reads colour, size, position, rendering mode and Unicode from the PDF
  and follows form XObjects. It does not measure pixels. The module docstring of
  `core/checks/hidden.py` lists the remaining channels (zero opacity, clipping, a colour-matched box,
  CMYK conversion bias). Anything it cannot judge, such as a pattern fill or an unreadable
  background, fails rather than passes.

## Not built yet

- **Sealing a finished document** into `final/` with a keyed hash, and a hash-chained ledger of what
  was generated from which facts. Today `build` writes a draft, and `apply` refuses a draft whose
  PDF changed after it was gated.
- **Research about an employer.** A cover letter may say about the company only what the posting
  says.
- **An MCP server.** The skill drives the command line.

## Explicit non-goals

- No automated submission to any platform, and no browser automation that clicks submit. Every major
  application platform either requires the employer's own credentials to post an application or
  forbids automation. `callback apply` builds a packet and the candidate submits it.
- No scraping of job boards whose terms forbid it, and no credential storage.
- No attempt to evade or test against AI-generated-text detectors.
- No claim of compatibility with any named applicant-tracking system. The parseability check
  proves a document is structurally unambiguous to a naive extractor, nothing more.
- No at-rest encryption of locally stored data. Files are `0600` and directories `0700` under
  `$CALLBACK_HOME`.

## Known constraints

- The sensitive-attribute screen on fact storage is a heuristic refusal of self-attributed protected
  attributes (for example "I am married", "Date of birth: ..."), not a general PII classifier. It may
  refuse legitimate text that happens to match; rephrase the fact rather than bypass the check.
- Section titles come from a fixed list, and a cover letter's courtesy sentences from a short fixed
  set. An unusual but legitimate title or sentence is refused rather than guessed about.
