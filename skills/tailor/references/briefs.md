# Field briefs

A brief is what screeners in one field look for, with short exemplars of how
the field writes, each cited to where it was published. It is the writer's
model of the field: you read it before phrasing a draft, and the `style` gate
holds the draft to it. One brief per field, at
`$CALLBACK_HOME/briefs/<field>.yaml`, where `<field>` is lowercase letters,
digits and hyphens.

## Building one

Building a brief is research and judgement, so it happens here, not in
`core`. Dispatch one research subagent (`model: sonnet`) per source kind:

- **`employer`**: material employers publish about how they hire: how-we-hire
  pages, published rubrics, intern-programme pages, from the companies and
  programmes the user targets (`callback targets list`).
- **`career-centre`**: sample resumes and cover letters that university career
  offices publish for this field.

Rules every subagent is given:

1. **No material about identifiable individuals**: no personal portfolios, no
   profiles, no one's own resume.
2. **Never fetch LinkedIn, Indeed, Handshake, Workday or Glassdoor**, or a
   copy of their pages. `callback brief check` fails a brief citing a URL on
   `linkedin.com`, `indeed.com`, `joinhandshake.com`, `myworkdayjobs.com` or
   `glassdoor.com`.
3. **Cite and date every claim.** A claim no listed source states is dropped.
4. **Exemplars are quotations of at most 60 words**, never a reproduced
   document.
5. **Contested advice is recorded as contested** ("one-page resumes: required
   by s1, not by s4"), never averaged into a rule.

Write the YAML with the Write tool:

```yaml
field: ai-ml-engineering
built_at: 2026-10-10T14:00:00-04:00        # with its UTC offset
sources:
  - id: s1
    kind: employer                         # employer | career-centre
    publisher: "Example Labs"
    title: "How we hire interns"
    url: https://example.com/careers/interns
    fetched_at: 2026-10-10T13:20:00-04:00
claims:
  - text: "Interns are screened on shipped projects with measurable outcomes."
    source: s1
exemplars:
  - kind: bullet                           # bullet | letter-paragraph
    text: "..."
    source: s1
    why: "Names the system, the scale and the result in one line."
phrases_to_avoid: ["rockstar"]             # added to the built-in list
```

Then run `callback brief check <field>`. A brief it cannot read is refused
before any check runs, with exit code 3: YAML that does not parse, a missing
or unexpected key, a timestamp without a UTC offset, a source URL that is not
`https://` or has no plain host name, a `field` other than the name the file
is stored under, or a file reached through a symlink leading out of
`$CALLBACK_HOME`. A brief it can read fails (exit 2) on a source cited but not
listed, a source id listed twice, a source on a forbidden platform, or an
exemplar over 60 words; it warns (exit 1) on a source fetched more than 90
days ago or fewer than 5 exemplars. Fix every failure. **Then show the user
what you wrote and wait for their review** before any draft uses it: it is about
three minutes of reading, and a brief nobody read is advice nobody checked.

## Using one

Before phrasing a draft, choose the field the posting belongs to and run
`callback brief show <field>`; it prints nothing of a brief that fails its
check. Write with its claims as the screening
criteria and its exemplars as a model of **structure** (system, scale,
result), never of words. Put `brief: <field>` at the top level of the resume,
letter or answers document; the build then fails any six consecutive
words copied from an exemplar and any phrase that brief lists to avoid. Words
are counted as tokens, so a hyphen, apostrophe or decimal point splits one
("state-of-the-art" is four). A run of six words the sentence's own cited
facts also contain is the candidate's wording, not copying, and a courtesy
sentence from the fixed list is checked for neither.

A document that names a brief which cannot be loaded, or whose
`callback brief check` fails, does not build (exit 3). A brief that only warns
is used.

## The critic

After a build passes or warns, dispatch one subagent on the session's own
model. Its persona is a hiring manager in that field, holding its claims. Give it the built document and the facts its bullets cite.
Ask for at most three bullets or sentences, weakest first, each with the
reason and a sharper alternative that uses only the facts already cited.

Show the user what it returns. It is advice, never a gate: a rewrite it
suggests is built and gated like any other change.
