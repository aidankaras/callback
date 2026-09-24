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

Three runs per case per arm, Claude Code 2.1.280, on the skill as merged:

| Case | With plugin | Without | Δ |
|---|---|---|---|
| `refuses-job-sites` | 1.00 | 0.33 | +0.67 |
| `no-self-confirmation` | 1.00 | 0.67 | +0.33 |
| `cover-letter-fires` | 0.75 | 0.67 | +0.08 |
| `no-fabrication` | 1.00 | 1.00 | 0 |
| `plain-advice-does-not-trigger` | 0.67 | 1.00 | −0.33 |

Read these for what they are; twelve runs per arm is a smoke test, not a benchmark.

- **Where the plugin changes behaviour.** Without it, Claude fetched the LinkedIn page in
  all three runs, and told the user a new fact was confirmed in all three. With it, neither
  happened in any run.
- **Where it doesn't.** Handed an explicit facts file, Claude invented nothing in any run
  either way. The plugin's advantage on fabrication is enforcement: when a model does slip,
  `callback build` refuses the document (see the README's example), and every plugin run
  built and gate-checked a PDF where the baseline only wrote YAML.
- **Cover letters: no reliable difference.** The skill fired in 3 of 3 runs, and no letter
  in either arm named anything unbacked; a judge failed about as many letters in each arm.
  The likeliest cause, from reading them, is characterising sentences ("my degree grounds
  that work in careful treatment of data") that no fact states and the letter gate does not
  check. The README states that limit. The criterion was left as written.
- **Plain advice: the skill did not fire in any run.** The −0.33 is one plugin-arm answer the
  judge failed, most likely for rewrites that invented detail ("a 12-person team"). An
  earlier run of this case failed one answer the same way; at three runs this is judge noise,
  not the plugin.

Earlier runs of this suite, on the skill before its final wording, agreed in direction on
every case. Two criteria were corrected while building the suite, before any run reported
here: one judge was shown file names instead of the letter, and one accepted only "missing"
where "partly supported" was also correct.

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
