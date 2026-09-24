## Finding postings

Postings come from the employer's own board, never from a scraper.

- **Onboarding.** Ask which companies the user wants to watch, then find
  each one's applicant-tracking board with a web search, e.g.
  `"<company>" site:boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com`.
  Add each with `callback targets add "<company>" --ats <greenhouse|lever|ashby> --board <name>`.
  The board name is the path segment after the host in the posting URLs.
- **Criteria.** Set title terms once:
  `callback criteria --include "intern,intern*,data scien*,machine learning,data analyst" --exclude "senior,staff,principal,director"`.
  Terms are whole words; a trailing `*` makes a prefix.
- **Profile.** Set it once, so eligibility can be judged:
  `callback profile --work-authorization "<…>" --graduation "<Month Year>" --degree <bachelor|master|phd>`.
- **Watching.** `callback watch` stores new matches. `callback triage`
  ranks them: knockouts last, then by what the user can evidence and by
  freshness. Applying in a posting's first days matters, so suggest the
  freshest eligible ones first.
- **Web search** (Firecrawl or other search) for roles at companies not
  watched, on the ATS domains above or the employer's own careers site.
  Then `callback postings resolve <url> --company "<company>"` fetches the
  canonical posting: from the ATS API, or from the careers page's own
  `schema.org/JobPosting` data. A page without that data, or whose
  `robots.txt` disallows it, is refused; then ask the user to paste the
  text. Never fetch the posting page yourself to work around a refusal.
- **LinkedIn, Indeed, Handshake or Workday.** Never fetch or scrape them;
  they forbid automated access, and callback refuses them. If the user
  shares one, ask for the company, find the same role on its own board,
  and resolve that. For a Handshake-only listing, ask the user to paste the
  text into `inbox/<job-id>.md`, first line `# <role> - <company>`.
- **Starting a job.** `callback postings take <posting-id> --job <job-id>`
  saves the employer's own text as the job's posting. Then run
  `callback fit <job-id>`, which reports the same knockouts as
  `callback eligibility` and, for each technology the posting names, which
  confirmed facts back it. On a knockout, stop and tell the user why,
  quoting the posting. On a question, ask it. Its evidenced list is where
  selection starts; its missing list is what "Each posting", step 3, asks about.

## What to learn next

- **What to learn next.** `callback gaps` ranks the technologies eligible
  postings ask for that no confirmed fact backs. Offer it when the user
  asks what to work on.
