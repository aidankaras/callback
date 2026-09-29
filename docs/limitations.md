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
  to the posting saved in `inbox/<job>.md`. `callback postings take` writes the text a board's API
  returned, but the file can be edited afterwards, and a posting pasted in by hand is trusted as it
  is: neither can be told from a genuine one.
- **Every way to describe the candidate indirectly.** An employer sentence may not use first-person
  words, the candidate's name, or a fixed list of referents and relationship verbs ("applicant",
  "she", "interviewed", "hired"); a relationship with the reader ("met", "referred", "suggested")
  must be stated in a cited fact. Both are word lists, so a paraphrase outside them is not seen.

## Guards that are defence in depth, not containment

- **The write guard** refuses an assistant's writes to the fact store, the application record,
  sealed documents and itself. For Bash, it resolves the paths a command names, after `cd`,
  `pushd` and `-C`, and with `~`, `$HOME` and `$CALLBACK_HOME` expanded. It does not catch a path a
  command builds at run time: from another variable, `find`, an encoded string, or a script it runs.
  The same holds for the commands only the candidate may run: the guard reads the words
  `facts confirm`, `facts retire` and `notes activate` in a command's text, whatever the space
  between them, and cannot read them in a command that assembles them, as in `V=retire` followed
  by `callback facts $V`, or that splits one with quotes.
  It guards against accidental writes, not a determined process.
- **The careers-page reader** refuses a host that resolves to a private, loopback or link-local
  address, but checks the address before connecting and the connection resolves the name again, so
  a host whose DNS answer changes in between (DNS rebinding) is not caught. It reads only the
  page's JSON-LD, which the employer can make say anything; `validThrough` catches a posting the
  employer has closed, not one it has simply left up.
- **Confirmation needs a terminal, and a terminal can be faked.** `callback facts confirm` reads
  from `/dev/tty`, so a process without one cannot confirm a fact; but `script` or `unbuffer` gives
  a child process a pseudo-terminal, under which it can. The write guard refuses any Bash command
  that names `facts confirm` other than a search or `git` (so `script -qc`, `bash -c`, `watch`,
  `find -exec` and the rest are all refused, at the cost of a harmless `timeout grep`). A phrase
  split across words, or a script written to a file and then run, is not caught. That stops an
  assistant confirming by accident or on a posting's instruction. It does not stop a program
  written to get round it, and anyone at the candidate's own terminal can confirm.
- **The hidden-content screen** reads colour, size, position, rendering mode and Unicode from the PDF
  and follows form XObjects. It does not measure pixels. The module docstring of
  `core/checks/hidden.py` lists the remaining channels (zero opacity, clipping, a colour-matched box,
  CMYK conversion bias). Anything it cannot judge, such as a pattern fill or an unreadable
  background, fails rather than passes.

## Not built yet

- **Watching boards other than Greenhouse, Lever and Ashby.** A careers page can be resolved one
  URL at a time from its `schema.org/JobPosting` data, but not watched for new postings: listing
  pages rarely carry that data, which search engines read from each posting's own page. Workday
  forbids automated access. Checked 2026-09-22 and not read: SmartRecruiters' and Recruitee's
  posting APIs, whose `robots.txt` disallows every agent (SmartRecruiters allows only LinkedIn's
  crawler), and Workable's job-widget endpoint, which did not return JSON to a plain client.
  Aggregator APIs are not read.
- **Research about an employer.** A cover letter may say about the company only what the posting
  says.

## Explicit non-goals

- No automated submission to any platform, and no browser automation that clicks submit. Every major
  application platform either requires the employer's own credentials to post an application or
  forbids automation. `callback apply` builds a packet and the candidate submits it.
- No scraping of job boards whose terms forbid it (LinkedIn, Indeed, Handshake, Workday,
  Glassdoor), no evasion of anti-bot measures, and no credential storage.
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
