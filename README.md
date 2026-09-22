# callback

Tailoring a resume or cover letter for a specific role is high-effort, low-feedback work, and
generic language-model assistance tends to make it worse: it invents accomplishments, inflates
titles, and produces documents nobody has checked an applicant-tracking system can read.

`callback` is a Claude Code plugin built the other way round. The candidate's facts live in a local,
provenance-tracked store; Claude arranges them for a posting; and a deterministic Python package
checks every document mechanically against that store before it is delivered. A quality claim is
something you can re-run, not something the model reports about itself. There is no LLM call in
`core/`.

## What the checks guarantee

| Property | Mechanism |
|---|---|
| Only facts the candidate confirmed can back a document | `callback facts confirm` reads its answers from the controlling terminal, which an assistant's shell does not have; a write-guard hook refuses an assistant's edits to the fact store |
| Bullets make no claim their facts do not state | every number (including spelled-out ones), date, technology, mid-sentence name and claim word (award, promoted, doubled, …) must appear in the cited facts; a count cannot rest on a percentage |
| Entry headers are not inflated | every header field an entry prints (employer, title, location, dates) must equal the same cited fact's; a bullet may cite only its own entry's facts |
| Nothing is added around the content | the identity block must match a confirmed contact fact; section titles come from a fixed list; every listed skill needs confirmed backing |
| The PDF is machine-readable | the rendered PDF is re-extracted with a deliberately naive parser, and every critical field must be recovered in order |
| Nothing hidden | no invisible, tiny, off-page or unpainted text, no suspicious Unicode, no unexpected metadata; anything the screen cannot judge fails rather than passes |
| Cover letters stay honest | each sentence declares what it is about: claims about the candidate rest on their facts and never on the posting, names, numbers and claim words about the employer only on the posting; commitment language (salary, availability, relocation, sponsorship) fails |
| Postings come from the source | discovery reads employers' documented public posting APIs over HTTPS, politely, and refuses LinkedIn, Indeed, Handshake, Workday and Glassdoor by name |
| Applications stay reasonable | at most 10 in 24 hours, no re-apply to the same posting within 90 days, and a packet is only built from a draft that passed, or whose warnings the candidate accepted |

It does **not** check ordinary wording: "led the migration" and "helped with the migration" are
indistinguishable to it. And it cannot know whether a fact the candidate confirmed is true. See
`docs/limitations.md` for the full list.

## Using it

```bash
uv sync
claude --plugin-dir .      # loads the `tailor` skill and the write guard
```

Then ask Claude to tailor your resume to a posting. The underlying commands:

| Command | Does |
|---|---|
| `callback targets add`, `criteria`, `watch`, `postings` | find postings on employers' own Greenhouse, Lever and Ashby boards, never by scraping job sites |
| `callback profile`, `eligibility`, `triage` | flag knockout requirements (sponsorship, citizenship, graduation window) quoted from the posting, and rank postings by evidence and freshness |
| `callback import resume.pdf` | extract an existing resume's text, screening it for hidden content |
| `callback facts add / list / confirm` | store facts (unconfirmed), list them, confirm them yourself |
| `callback build resume.yaml --job <id>` | render the PDF and run every gate; exit 0 pass, 1 warn, 2 fail |
| `callback letter letter.yaml` | render and check a cover letter |
| `callback export resume.yaml` | the resume as plain text, for forms that want it pasted |
| `callback apply <id> …` | build the application packet and record it; **callback never submits** |
| `callback outcome <id> <kind>`, `callback applications` | track what happened |

`docs/using-callback.md` has the fact format, the document format and every rule.

## Development

Requires Python 3.12 and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

`CLAUDE.md` holds the repository's non-negotiables and the defects it has actually shipped, and
`docs/lessons/` records design decisions with their evidence.

## License

MIT — see `LICENSE`.
