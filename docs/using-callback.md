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

Everything lives under `$CALLBACK_HOME` (default `~/.callback`). Directories there
are `0700` and files `0600`.

## Facts

A fact is one thing that is true about you, stated once:

```yaml
- id: role
  kind: experience
  text: Data Engineer at Northwind Analytics
  entities: [Northwind Analytics]
  dates: {start: "2024-06", end: "2025-08"}
  provenance: {source: "import:old-resume.pdf", trust: import_unconfirmed}
- id: queue
  kind: experience_detail
  text: Cut the review queue to 300 items using Python
  numbers: {queue: {value: 300}}
  entities: [Python]
  provenance: {source: "import:old-resume.pdf", trust: import_unconfirmed}
```

- `kind` is one of `experience`, `experience_detail`, `education`, `project`,
  `skill`, `links`.
- `entities` holds the names a resume may print: the employer exactly as it
  should appear, and tools. Tool names are matched through an alias table, so
  `k8s` and `Kubernetes` are one skill.
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
- Every number, date and recognised tool in a bullet appears in the confirmed
  facts it cites.
- Every employer, title, location and date in an entry header matches a
  confirmed fact.
- Every listed skill has confirmed backing.
- A naive text extractor reads the critical fields of the rendered PDF back in
  order.
- The PDF carries no text a human cannot see.

**They do not check:**
- The *wording* of a bullet beyond those tokens. "Led" and "helped with" are
  not distinguished, so read every bullet before you send it.
- The identity block (name, contact details, links), which comes from your own
  input.
- Whether a posting's requirements are covered. That is advice the skill gives,
  never a gate.

**Not built yet:**
- Sealing a finished document into `final/`.
- The hash-chained ledger.
- Layout lints.
- The MCP server.
- A write guard that stops an assistant from editing the fact store directly.

`build` only ever writes a draft.
