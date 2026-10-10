## What the gates check (so you know what "fix the document" means)

| Gate | Fails when |
|---|---|
| grounding | a bullet's number (spelled-out too), date, tool, mid-sentence capitalised name or claim word (award, promoted, doubled, …) is absent from the facts it cites, or it cites an unconfirmed or unknown fact |
| claims | the header isn't one cited fact's `organization`/`title`/`location`/dates exactly; a bullet cites another entry's fact; the identity block isn't a confirmed `links` fact; a section title isn't a standard one; a skill group's label isn't a category name from `SKILL_GROUP_LABELS` ("Languages & Tools", "Machine Learning"); a skill has no confirmed backing |
| roundtrip | a naive ATS parser can't read back a critical field in order; warns on a skill it couldn't read |
| hidden | text a human can't see: white or tiny text, off-page text, an unpainted rendering mode, odd Unicode, metadata |
| skills | warns when a listed skill is backed but nothing on the page shows it: no grounded bullet names it, and no confirmed fact belonging to an entry on the page lists it in `entities`. A skill backed only by a fact of its own warns; record where it was used as a fact under that role |
| lints | warns on layout: a bullet over two lines, fewer than 2 or more than 6 bullets for a role, more pages than the user's profile allows, a near-empty last page |
| style | fails a stock phrase ("spearheaded", "results-driven", a brief's own `phrases_to_avoid`) or six consecutive words copied from an exemplar of the document's brief, counted as word tokens (a hyphen, apostrophe or decimal point splits a word) and not counting wording the cited facts already contain; warns on a bullet with no digit and none of its cited facts' entities |
