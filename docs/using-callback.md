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
below for you, except `facts confirm`, which only you can run.

## The commands

| Command | What it does | Exit codes |
|---|---|---|
| `callback import <resume.pdf>` | Extracts the text of an existing PDF into `inbox/`, after screening it for hidden text | 0 clean, 3 unreadable, 4 hidden content found or a page could not be screened |
| `callback facts add <facts.yaml>` | Adds facts, all unconfirmed. Each fact is accepted or refused on its own | 0, 1 if any fact was refused, 3 unreadable file |
| `callback facts list [--unconfirmed]` | Lists stored facts | 0 |
| `callback facts confirm [ids...]` | Asks you, fact by fact, whether each is true | 0, 3 if not run from a terminal |
| `callback build <resume.yaml> --job <id>` | Renders the PDF and runs every gate, writing to `outputs/<id>/draft/` | 0 pass, 1 warn, 2 fail, 3 could not build |
| `callback export <resume.yaml>` | Prints the resume as plain text for forms that want it pasted, and checks every critical field survived | 0, 2 if a field was lost |

Everything lives under `$CALLBACK_HOME` (default `~/.callback`). Directories there
are `0700` and files `0600`.

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
  answers from the terminal itself (`/dev/tty`), so no script or assistant can
  confirm on your behalf, even by piping in "y". It guards against accidental
  confirmation. It is not a lock against a process that edits the store file
  directly.

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

## What the gates guarantee, and what they do not

**They guarantee:**
- Every number (spelled-out ones included), date and recognised tool in a
  bullet appears in the confirmed facts it cites.
- Every capitalised name that doesn't start a sentence (a client, an award, a
  title) also appears there.
- So does every claim word: award, patent, promoted, founded, valedictorian,
  doubled, hundreds, "led a team" and the rest of a fixed list.
- A bullet cites only its own entry's facts.
- Every entry header equals one confirmed fact field for field: organization,
  title, location and both dates.
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

**Not built yet:**
- Sealing a finished document into `final/`.
- The hash-chained ledger.
- The MCP server.

`build` only ever writes a draft.
