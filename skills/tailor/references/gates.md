## What the gates check (so you know what "fix the document" means)

| Gate | Fails when |
|---|---|
| grounding | a bullet's number (spelled-out too), date, tool, mid-sentence capitalised name or claim word (award, promoted, doubled, …) is absent from the facts it cites, or it cites an unconfirmed or unknown fact |
| claims | the header isn't one cited fact's `organization`/`title`/`location`/dates exactly; a bullet cites another entry's fact; the identity block isn't a confirmed `links` fact; a section title isn't a standard one; a skill has no confirmed backing |
| roundtrip | a naive ATS parser can't read back a critical field in order; warns on a skill it couldn't read |
| hidden | text a human can't see: white or tiny text, off-page text, an unpainted rendering mode, odd Unicode, metadata |
| skills | warns when a listed skill is backed but no grounded bullet shows it in use |
| lints | warns on layout: a bullet over two lines, fewer than 3 or more than 6 bullets for a role, a second page, a near-empty last page |
