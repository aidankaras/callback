## A cover letter, when asked for one

The saved posting's first line must read `# <role> - <company>`, exactly as
the posting names them. Write `$CALLBACK_HOME/outputs/<job-id>/draft/letter.yaml`
(format in `docs/using-callback.md`) and run `callback letter <that yaml>`:
exit 0 passes, 2 fails (fix the letter and rerun), 3 could not run.
Name the field's brief with `brief: <field>` in a letter or answers document as
in a resume; see "Using one" in `references/briefs.md`.

Every sentence declares its `kind`:

- **`candidate`**: about the candidate. It cites their facts, never `posting`. Don't
  name the company in it; say "your team" or "the role" instead.
- **`employer`**: about the job. It cites `[posting]` only, begins with the
  company's name or "The role", "The position", "The internship" or "The
  team", never mentions the candidate, and says only what the posting says.
  Nothing about funding, news or leadership: that needs research this tool
  doesn't do yet.
- **`courtesy`**: only the fixed sentences in the docs.

Keep to one sentence per entry and 300 words in all. Don't paste resume
bullets; say why the experience fits. Never write about salary,
availability, relocation or sponsorship: a signed letter must not concede
terms, and the check fails on it.

## Form answers and outreach

- **Form answers.** When the application form asks free-text questions,
  write `outputs/<job-id>/draft/answers.yaml` (format in
  `docs/using-callback.md`). Include each question's character or word
  limit, and call the `answers` tool with its path.
  The sentences follow the cover-letter kinds. `apply` puts passing answers
  in the packet.
- **Outreach.** Offer to draft a short note to a recruiter, an engineer on
  the team or an alum of the user's school: `callback outreach <yaml>` with `channel`
  (`linkedin-note`, 300 characters; `linkedin-message`; `email`, 150 words)
  and the recipient's name. The name goes in the generated greeting only.
  Never invent a connection ("we met at…") that isn't a confirmed fact.
