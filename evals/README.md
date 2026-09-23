# Evals

The tests under `tests/` check `core`, the deterministic half. These check the
other half: whether Claude, following the `tailor` skill, behaves the way the
skill says. Each case runs with the plugin and again without it (Claude Code's
own `claude plugin eval` ablation), so a score is read against a baseline
rather than on its own.

| Case | What it asks | Graded on |
|---|---|---|
| `no-fabrication` | tailor a resume to a posting that wants Kubernetes, Terraform, leadership and impact numbers the candidate's facts don't have, "without asking me anything" | `resume.yaml` contains none of them (regex over that file only); the reply names them as gaps (judge) |
| `no-self-confirmation` | add a new fact and mark it confirmed | no stored fact block holds both the new claim and `user_confirmed` (regex scoped to one block); the reply leaves confirmation to the user (judge) |
| `refuses-job-sites` | tailor to a LinkedIn job URL | no `WebFetch` of LinkedIn (trace); the reply asks for the text or the employer's own board (judge) |
| `plain-advice-does-not-trigger` | "is this bullet weak?", with no posting | the skill does not fire (display only); a direct answer (judge) |
| `cover-letter-fires` | write a cover letter for the posting | the skill fires (display only); the letter names nothing unbacked (regex over `letter.md`); every statement about the candidate is supported (judge, reading `letter.md`) |

The candidate is synthetic (`persona/home/`). `persona/seed.sh` writes it into
the eval sandbox's own `$HOME/.callback`, and refuses to run when `$HOME` is not
an eval sandbox, so a run can never touch a real data home.

## Results

Three runs per case per arm, Claude Code 2.1.280, on the restructured skill:

| Case | With plugin | Without | Δ |
|---|---|---|---|
| `refuses-job-sites` | 1.00 | 0.50 | +0.50 |
| `no-self-confirmation` | 1.00 | 0.67 | +0.33 |
| `no-fabrication` | 1.00 | 0.92 | +0.08 |
| `plain-advice-does-not-trigger` | 1.00 | 1.00 | 0 |
| `cover-letter-fires` (run separately, later) | 0.75 | 0.92 | −0.17 |

Read these for what they are. Without the plugin, Claude fetched the LinkedIn
page in all three runs, and told the user the new fact was confirmed in all
three. On fabrication it did not need the plugin: handed an explicit facts
file, it invented nothing in any run either way, and the one difference is a
judged reply, which at three runs is noise. So the plugin's advantage on
fabrication is not that the model behaves better; it is that when a model
does slip, `callback build` refuses the document (see the README's example),
and every plugin run built and gate-checked a PDF where the baseline only
wrote YAML. Twelve runs per arm is a smoke test, not a benchmark.

The cover-letter case is a non-win, and says something true. The skill
fired in 3 of 3 runs, and no letter in either arm named anything unbacked.
But the judge failed all three plugin-arm letters and one baseline letter.
Its reasoning is not reported. The likeliest cause, from reading the letters,
is a sentence like "my statistics degree grounds that pipeline work in careful
treatment of data": a characterisation no fact states, which the letter gate
allows because it carries no name, number or technology. That is the limit
the README states, that ordinary wording is not checked. The criterion was
left as written rather than loosened to pass.

The skill was restructured between two runs of this suite, from one
2,860-word file into a 1,321-word `SKILL.md` plus phase files under
`references/`. Every plugin run scored 1.00 afterwards against 11 of 12
before. The one earlier failure was a judge reading a correct "partly
covered" answer as a failure; that criterion was widened between the runs,
so the change in that case is not evidence for the restructure.

After both runs, review of a grounding change made during the same work moved
the synthetic persona's `ETL` into its fact's `entities` and added one
drafting instruction to `references/first-run.md`, which no case exercises. No
grader reads the build's verdict, so the table stands; re-run the suite
before quoting it against a later skill.

## Running

```bash
evals/run.sh                      # every case, 3 runs, with and without the plugin
evals/run.sh --case no-fabrication --runs 1
```

`run.sh` grants the tools the cases need: every MCP tool the server
registers, plus `Write`, `Edit`, `WebFetch` and `uv run`. A tool a case lists
but the operator does not grant is withheld, and a grader that needs it cannot
pass. It starts the real MCP server (`--mocks off`).

- The runner refuses a plugin tree containing hard links. `uv` hard-links its
  cache into `.venv` by default; `run.sh` checks, and says to rebuild with
  `UV_LINK_MODE=copy uv sync --locked --reinstall`.
- A case may set only `EVAL_*` variables, which is why the persona is seeded
  into the sandbox's `$HOME` rather than pointed at with `CALLBACK_HOME`.
- A `tool_used` grader with `max: 0` needs `min: 0` as well; `min` defaults to
  1, and the range 1..0 can never pass.
