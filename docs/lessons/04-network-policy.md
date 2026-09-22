# 04 — What callback is allowed to fetch, and why so little

**Decision.** `core/discovery/` is the only package that opens a socket. It
reads four documented public posting APIs by allowlist, and one careers page at
a time from the structured data that page publishes for search engines.
Everything else — the job sites a candidate actually browses, aggregators, and
several other applicant-tracking systems — is refused in code, by name where a
name exists.

## The state a reader will find

`core/discovery/fetch.py` carries `ALLOWED_HOSTS` (four hosts) and
`DENIED_DOMAINS` (`linkedin.com`, `indeed.com`, `joinhandshake.com`,
`myworkdayjobs.com`, `glassdoor.com`). A denied domain is refused before the
allowlist is consulted, so it cannot be added by editing one line. Redirects are
re-checked against both. `core/discovery/page.py` adds a second fetcher for a
single careers page, under stricter rules again: HTTPS on the default port, no
user info, no host resolving to a non-public address, `robots.txt` honoured, a
redirect only to the same host and an allowed path.

This looks like an arbitrary shortlist, and the obvious "fix" — adding the site
the candidate is actually looking at, or routing through a scraping service —
is what the rest of this note is against.

## Why

**The job sites forbid it, and a tool that ignores that is not usable on a real
search.** LinkedIn, Indeed, Handshake and Workday all prohibit automated access
in their terms. The candidate's own account is the one at risk, and the
documents this produces go to employers under their own name. A refusal by name
also produces a better message than a failure: the skill is told to ask for the
company and find the same role on the employer's own board, which is usually
where the listing came from in the first place.

**Routing through a scraper does not change the answer.** It changes who makes
the request, not whether the site permitted it. Nor does "only where robots.txt
allows" plus a browser User-Agent: pretending to be a browser is the part that
makes it circumvention.

**A documented public API is the permission.** Greenhouse, Lever and Ashby
publish unauthenticated posting endpoints for exactly this purpose, and Algolia
publishes Hacker News's. Ashby's `robots.txt` returns 401 while its posting API
is documented public; the documented API is the permission, and the 401 is about
the crawler, not the API.

**Where robots.txt says no to everyone, that is the answer.** Checked
2026-09-22 and deliberately not added: SmartRecruiters (`Disallow: /` for every
agent except `LinkedInBot`) and Recruitee (`Disallow: /`), both of which
otherwise publish usable posting APIs. Workable's job-widget endpoint returned
something other than JSON to a plain client, most likely a bot check, and
pushing past one is the circumvention this policy exists to avoid.

**A careers page is read only from what it publishes for machines.** An
employer that is not on a known applicant-tracking system usually still embeds
`schema.org/JobPosting` JSON-LD, because that is what search engines' job
listings consume. Reading that block is reading what the employer published for
programs to read; scraping the page's layout is not, and would also break on
every redesign. That is why `page.py` parses the JSON-LD and nothing else on
the page, and why a page without it is refused with "paste the posting's text
instead" rather than fallen back to a text scrape.

## What a reviewer will ask

**"Why not watch careers pages for new postings too?"** Because the data is
rarely there: search engines read `JobPosting` from each posting's own page, not
from a listing page, so a watch over listing pages would poll many sites to find
nothing. Watching stays with the APIs that publish a full list in one request.

**"Why refuse a host that resolves to a private address? This runs on the
candidate's own machine."** Because a URL can arrive from a posting, a search
result or a page the assistant read, and none of those is the candidate. The
check costs one DNS lookup and removes a class of request the tool has no reason
to make. It does not survive DNS rebinding, which `docs/limitations.md` records
rather than claims to solve.

**"The allowlist is four hosts; isn't this too strict to be useful?"** The
alternative that was measured is worse. Most postings a candidate finds on a job
site are reposts of an employer's own board, so the board is reachable; and when
it is not, pasting the text costs one step and keeps the gates' guarantees
intact, because every check downstream treats the posting as untrusted input
either way.
