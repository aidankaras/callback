# Using callback

callback turns confirmed facts about you into a resume for one job posting,
and checks the result with deterministic gates. The gates are what make its
claims checkable: a report is produced by re-running code, not by a model
grading its own work.

## Install and load

```bash
git clone <this repository> && cd callback
uv sync
claude --plugin-dir .          # loads the `tailor` skill into a Claude Code session
```

Then ask Claude to tailor your resume to a posting. The skill runs the commands
below for you, except `facts confirm` and `notes activate`, which only you can
run.

## The commands

| Command | What it does | Exit codes |
|---|---|---|
| `callback init` | Creates the data home (`$CALLBACK_HOME`, default `~/.callback`) with private permissions; every other command does this for itself | 0 |
| `callback delete-all` | Removes every file under the data home: facts, postings, drafts, packets, sealed documents, the ledger and the signing key. There is no undo, and no tool can run it -- it is not an MCP tool, and the write guard cannot stop it | 0 |
| `callback import <resume.pdf>` | Extracts the text of an existing PDF into `inbox/`, after screening it for hidden text | 0 clean, 3 unreadable, 4 hidden content found or a page could not be screened |
| `callback facts add <facts.yaml>` | Adds facts, all unconfirmed. Each fact is accepted or refused on its own, and one that arrives already confirmed is refused | 0, 1 if any fact was refused, 3 unreadable file |
| `callback facts list [--unconfirmed]` | Lists stored facts | 0 |
| `callback facts github <user>` | Drafts an unconfirmed `project` fact (description as text; name and primary language as entities) for each of your own public, described, non-fork repositories, to `inbox/github-<user>.yaml`. Nothing reaches the store until you `facts add` the file | 0, 1 nothing drafted, 3 |
| `callback facts confirm [ids...]` | Asks you, fact by fact, whether each is true | 0, 3 if not run from a terminal or an id is unknown or web-sourced |
| `callback notes list [--all]` | Lists active notes; `--all` adds proposed and retired ones | 0, 3 unreadable notebook |
| `callback notes activate <id>` | Shows you a proposed note in full, with where it came from, and activates it on your yes at the terminal | 0, 3 if not run from a terminal or the id is unknown |
| `callback notes retire <id>` | Sets a note aside; it stays in the file and can be activated again | 0, 3 unknown id |
| `callback notes families [--set a,b]` | Shows, or replaces, the role families applications can be sorted into | 0, 3 invalid name |
| `callback build <resume.yaml> --job <id>` | Renders the PDF and runs every gate, writing to `outputs/<id>/draft/` | 0 pass, 1 warn, 2 fail, 3 could not build |
| `callback export <resume.yaml>` | Prints the resume as plain text for forms that want it pasted, and checks every critical field survived | 0, 2 if a field was lost, 3 unreadable document |
| `callback letter <letter.yaml>` | Renders a cover letter and checks it against your facts and the saved posting | 0 pass, 2 fail, 3 could not run |
| `callback apply <id> --company … --title … --source …` | Builds the application packet from a gated draft and records it, with the template it started from, the facts it cites and an optional `--role-family`; `--refresh` rebuilds it after the draft improves, keeping every earlier send and what happened to it. **Never submits** | 0, 3 refused |
| `callback outcome <id> <kind>` / `callback applications` | Records what happened; lists applications | 0, 3 unreadable record |
| `callback funnel` | Per source: applied, replied (callback, interview, offer or rejection; not "viewed"), positive, interviews, offers, median days to first reply, each as counts, with a source of fewer than 5 applications marked too few to compare; then applications silent for 21+ days, as a suggestion to record them as ghosted | 0, 3 unreadable record |
| `callback insights` | Grounding failures that recur in the latest build of two or more jobs, traced to the facts the failing bullets cite, and the jobs whose grounding failed without naming a fact, whose failures it cannot trace; then, for each packet sent (a refresh is a new one), what happened, grouped by the template the draft began as, the role family and each cited fact. Counts only; a group of fewer than 5 is marked too few to compare, and nothing is ranked | 0, 3 unreadable record |
| `callback finalize <id>` / `callback verify [<id>]` | Seals a gated draft read-only into `final/` with a signed manifest and a hash-chained ledger entry; `verify` reports anything changed since, and any break in the ledger | 0, 2 changed, 3 |
| `callback interview <id>` | An interview prep pack: the posting, exactly what was sent (verified against the application record) and the facts behind each claim | 0, 3 |

Everything lives under `$CALLBACK_HOME` (default `~/.callback`). Directories there
are `0700` and files `0600`.

## Finding postings

callback reads postings from employers' own applicant-tracking boards
(Greenhouse, Lever and Ashby, through their documented public APIs) and from
Hacker News's monthly "Who is hiring?" thread, where companies post their own
openings: `callback targets add "HN" --ats hn --board whoishiring`. It never
reads job boards. Most LinkedIn and Indeed listings are reposts of these
postings anyway.

| Command | What it does | Exit codes |
|---|---|---|
| `callback targets add "<company>" --ats <ats> --board <name>` / `targets list` | Choose the companies to watch | 0, 3 |
| `callback criteria --include "…" --exclude "…"` | Title terms a watch keeps: whole words, with a trailing `*` for a prefix | 0, 3 |
| `callback watch` | Polls every target and stores new matching postings with when they were first seen | 0, 1 if a board could not be read, 3 |
| `callback postings list [--days N]` | Stored postings, newest first | 0, 3 |
| `callback postings resolve <url> --company "<company>"` | The canonical posting behind a Greenhouse, Lever or Ashby URL, or an employer careers page's `schema.org/JobPosting` data | 0, 3 |
| `callback postings take <id> --job <job-id>` | Saves the posting's own text to `inbox/<job-id>.md` | 0, 3 |
| `callback profile --work-authorization … --graduation … --degree …` | What eligibility is judged against | 0, 3 |
| `callback agenda` | What to do next, most time-sensitive first: interviews recorded with no prep pack, drafts built but never sent, the five freshest eligible postings at companies not yet applied to, and applications silent for 21+ days. Each line names the command that acts; this one never does | 0, 3 |
| `callback template save <job-id> --name <name>` / `list` / `use <name> --job <job-id> [--overwrite]` | Saves a document whose build passed under a name, and copies it into another job's draft as a starting point. The copy is an ordinary draft: every gate runs against it again, and its bullets still cite facts that must still be confirmed | 0, 3 |
| `callback fit <job-id>` | The same knockouts, plus every technology the posting names that the alias table knows: which ones confirmed facts back (with the fact ids to cite) and which are missing | 0 eligible or stretch, 1 a question, 2 knockout, 3 |
| `callback eligibility <job-id>` | Knockout requirements in a saved posting, each quoted | 0 eligible or stretch, 1 a question, 2 knockout, 3 |
| `callback triage [--days N]` | Stored postings ranked: knockouts last, then by evidenced technologies and freshness | 0, 3 |
| `callback gaps [--days N]` | Technologies the eligible stored postings name that no confirmed fact backs, most frequent first, with how many rejected applications' saved postings named each; a rejection with no saved posting is listed, not counted | 0, 3 |

**Network access.** Discovery is the only part of callback that uses the
network:
- It reaches only those APIs (the three applicant-tracking systems and
  Algolia's Hacker News API), over HTTPS, with an honest User-Agent and at
  least a second between requests to a host.
- LinkedIn, Indeed, Handshake, Workday and Glassdoor forbid automated
  access, and callback refuses them by name.
- For a posting found there, find the same role on the employer's own
  board, or paste its text.

**Eligibility** reads the posting's words for a refusal to sponsor, a
citizenship or clearance requirement, a graduation window, and graduate-degree
or years-of-experience requirements. It judges them against your profile and
quotes each one. It is advice and never blocks a document.

## Facts

A fact is one thing that is true about you, stated once:

```yaml
- id: contact
  kind: links
  text: Dana Vex, dana@example.com, github.com/dv
  entities: [Dana Vex, dana@example.com, github.com/dv]
  provenance: {source: "import:old-resume.pdf", trust: import_unconfirmed}
- id: role
  kind: experience
  text: Data Engineer at Northwind Analytics
  organization: Northwind Analytics
  title: Data Engineer
  location: Boston, MA
  entities: [Northwind Analytics]
  dates: {start: "2024-06", end: "2025-08"}
  provenance: {source: "import:old-resume.pdf", trust: import_unconfirmed}
- id: queue
  kind: experience_detail
  parent: role
  text: Cut the review queue to 300 items using Python
  numbers: {queue: {value: 300}}
  entities: [Python]
  provenance: {source: "import:old-resume.pdf", trust: import_unconfirmed}
```

- `kind` is one of `experience`, `experience_detail`, `education`, `project`,
  `skill`, `links`.
- An `experience`, `education` or `project` fact carries the entry header as
  structured fields: `organization`, `title` and `location`, exactly as they
  should print. A resume entry's header must equal them. A shortened title
  doesn't count, since dropping "Intern" is inflation.
- A detail fact names its role in `parent`. A bullet may cite only its own
  entry's facts, or their children.
- `entities` holds names and tools the fact states. Tool names are matched
  through an alias table, so `k8s` and `Kubernetes` are one skill.
- One `links` fact holds your name, email, phone and links as `entities`.
  The resume's identity block must match it exactly.
- Record a percentage with `unit: "%"`. A count can never be backed by a
  percentage.
- `dates` use `YYYY-MM`. Leave out `end` for a role that has not ended; that is
  what lets a resume say "Present".
- A fact is usable only after `callback facts confirm`. That command reads your
  answers from the terminal itself (`/dev/tty`), so piping in "y" does not work,
  and the write guard refuses it when an assistant runs it through a wrapper
  that fakes a terminal. It guards against accidental confirmation, and against
  an assistant being talked into it. It is not a lock against a program built to
  get round it, or one that edits the store file directly.

## Notes

Facts are what a resume may claim. Notes are everything else a session should
know before it starts: a preference ("one page always"), a goal ("targeting ML
infrastructure"), a constraint ("never use 'spearheaded'"), or context ("offer
deadline Nov 14"). They live in `notes.yaml`, at most 200 characters each.

- An assistant can only *propose* a note. A proposed note does nothing until
  you run `callback notes activate <id>` yourself, which shows you the full
  text and where it came from and asks you. Retiring needs no terminal: a
  session can retire a note when you ask it to, the note stays in the file,
  and activating it again asks you.
- A note cannot make a claim pass the gates: grounding reads facts and nothing
  else. It can change everything the gates do not check -- which facts are
  chosen, what is left out, the wording, letters and answers -- which is why
  activating one is yours alone. As with `facts confirm`, the write guard
  refuses `notes activate` when an assistant runs it, however it is wrapped,
  and any tool's edit of `notes.yaml`. It guards against accidental or
  steered activation, not against a program built to get round it.
- Sessions receive notes as quoted data, never as instructions.

## The resume document

`build` takes a YAML resume whose bullets and entry headers cite fact ids:

```yaml
identity: {name: Dana Vex, email: dana@example.com}
sections:
- title: Experience
  entries:
  - heading: Northwind Analytics
    subheading: Data Engineer
    start: Jun 2024
    end: Aug 2025
    sources: [role]
    bullets:
    - {text: Cut the review queue to 300 items using Python, sources: [queue]}
- title: Skills
  skills: [Python]
```

Built as written, this example gives `WARN`: one bullet is under the
three-per-role layout floor. Warnings are advice, and every other gate passes.

## Cover letters

`callback letter <letter.yaml>` renders a letter to
`outputs/<job>/draft/cover-letter.pdf` and checks it against your facts and
the posting saved at `inbox/<job>.md`. The posting's first line must read
`# <role> - <company>`.

```yaml
identity: {name: Dana Vex, email: dana@example.com}
job_id: acme-ml-intern
company: Acme Robotics
role: Machine Learning Intern
greeting: Dear Hiring Team,
paragraphs:
- - {kind: courtesy, text: I am applying for the Machine Learning Intern role at Acme Robotics.}
  - {kind: employer, text: Acme Robotics is building a perception data platform., sources: [posting]}
- - {kind: candidate, text: At Northwind Analytics I brought the review queue down to 300 items with Python., sources: [role, queue]}
  - {kind: courtesy, text: Thank you for your consideration.}
closing: Sincerely,
```

Every sentence declares what it is about:

- **`candidate`**: a claim about you. It cites your facts, never the
  posting, and is checked exactly like a resume bullet. It may not name the
  company.
- **`employer`**: about the job. It cites `posting` and nothing else,
  begins with the company's name or "The role", "The position", "The
  internship" or "The team", and never speaks about you. Any number, tool,
  name or claim word in it must appear in the posting. Its ordinary wording
  is not compared with the posting.
- **`courtesy`**: one of a short fixed list of sentences ("Thank you for your
  consideration.", "I am applying for the {role} role at {company}.").

Also checked:
- Each sentence must be a single sentence.
- The greeting must be a salutation, and the closing one of a fixed set
  ("Sincerely,", "Best regards,", …).
- The company and role must each be a segment of the posting's first line.
- The letter fails if it runs past 300 words, uses commitment or negotiation
  language (salary, compensation, availability, "available to start",
  relocation, visa or sponsorship, start date, notice period, "willing to"),
  or reuses a resume bullet verbatim.
- The PDF is screened for hidden text.

## Form answers and outreach

`callback answers <answers.yaml>` checks free-text application-form answers
and writes them, ready to paste, to `outputs/<job>/draft/answers.md`:

```yaml
job_id: acme-ml-intern
company: Acme Robotics
role: Machine Learning Intern
candidate_name: Dana Vex
answers:
- question: Why are you interested in Acme Robotics?
  max_chars: 400
  sentences:
  - {kind: employer, text: Acme Robotics is building a perception data platform., sources: [posting]}
  - {kind: candidate, text: At Northwind Analytics I brought the review queue down to 300 items with Python., sources: [role, queue]}
```

Each sentence follows the cover-letter kinds. A `question` is copied from the
form: one line, not a heading, and never about the candidate, because it is
printed above its answer. Each answer must fit its
`max_chars` or `max_words`, counted on the exact text to be pasted. `apply`
adds `answers.md` to the packet only when its check passed and the file is
unchanged. Exit codes: 0 pass, 2 fail, 3 could not run.

`callback outreach <note.yaml>` checks a short note to someone at the
company. Its fields are the same as a form answer's, plus `recipient` (a
name) and `channel`: `linkedin-note` (300 characters), `linkedin-message`
(1,900 characters, 200 words) or `email` (150 words). The greeting and
sign-off are generated. `candidate_name` (here and in form answers) must be
the name a confirmed `links` fact states, since it signs the message. The
recipient appears only in the greeting, possessives included, and the
whole message is measured against the channel's limit. It prints the note,
and exits 0 pass, 2 fail, 3 could not run.

## What the gates guarantee, and what they do not

**They guarantee:**
- Every number (spelled-out ones included), date and recognised tool in a
  bullet appears in the confirmed facts it cites.
- Every capitalised name that doesn't start a sentence (a client, an award, a
  title) also appears there.
- So does every claim word: award, patent, promoted, founded, valedictorian,
  doubled, hundreds, "led a team" and the rest of a fixed list.
- A bullet cites only its own entry's facts.
- Every header field an entry prints (organization, title, location, both
  dates) equals the same cited confirmed fact's field, ignoring case and
  spacing. A field may be left out.
- The identity block equals a confirmed `links` fact, and section titles come
  from a fixed list.
- Every listed skill has confirmed backing.
- A naive text extractor reads the critical fields of the rendered PDF back in
  order.
- The PDF carries no text a human cannot see.

**They do not check:**
- Ordinary verbs and nouns. "Led the migration" and "helped with the
  migration" are not distinguished, so read every bullet before you send it.
- Whether a posting's requirements are covered. That is advice the skill gives,
  never a gate.

**The write guard.** Loaded as a plugin, callback installs a `PreToolUse` hook
(`hooks/write_guard.py`) that refuses an assistant's attempt to:
- write the fact store;
- write anything under `final/` or `released/`;
- write the guard itself;
- run `callback facts confirm`.

For Bash it resolves the paths a command names, including after a `cd`. It
guards against accidental writes; it is not a sandbox. A command that builds
its path at run time is not caught.

**Layout lints** (a bullet over two lines, fewer than 3 or more than 6
bullets for a role, a second page, a near-empty last page) can only warn.
They never fail a document.

**Sealing.** `build` only ever writes a draft. `finalize` is the only
writer of `final/`:
- It seals a gated draft read-only, with a manifest of every file's SHA-256
  signed by a key kept at `0600` in the data home.
- Each seal, application and refresh is recorded in a hash-chained
  `ledger.log`.
- `verify` reports any sealed file changed since, and any ledger entry
  altered, removed or reordered.

This is tamper-evidence, not tamper-proofing: anyone who can read the key
can forge a signature.

Each ledger entry also carries an HMAC under the same key, so a ledger
rewritten from scratch fails even when every entry chains correctly, and
`verify` reports a sealed document the ledger does not record.

## The MCP server

The plugin registers `callback-mcp` (see `.mcp.json`), which serves the same
checks as tools: `build`, `letter`, `answers`, `outreach`, `facts_add`,
<<<<<<< ours
`facts_list`, `notes_list`, `notes_propose`, `notes_retire`, `watch`, `triage`, `fit`, `agenda`, `postings_take`, `postings_resolve`,
`applications`, `funnel`,
=======
`facts_list`, `notes_list`, `notes_propose`, `watch`, `triage`, `fit`, `agenda`, `postings_take`, `postings_resolve`,
<<<<<<< ours
`applications`, `funnel`, `insights`,
>>>>>>> theirs
=======
`applications`, `funnel`, `insights`, `gaps`,
>>>>>>> theirs
`eligibility`, `apply`, `interview` and `verify`. Each wraps the function the
command line calls, so a result is identical whichever way it is reached, and
a failure is a tool error, never an empty result. `facts confirm`, activating
a note, and `delete-all` are deliberately not tools: only the candidate, at a
terminal, may make a fact usable, make a note something sessions follow, or
erase the data home.
