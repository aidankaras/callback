# Contributing

`CLAUDE.md` is the contributor guide for people and agents alike: the
non-negotiables, the ways a check here has passed without running, and the
defects this codebase has actually shipped. Read it before changing a gate.

Before opening a pull request, all four gates pass, each run on its own:

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

A fix comes with a test that fails when the fix is reverted, and a change to a
gate's behaviour is shown failing and passing on a fixture built for it. Test
data is synthetic; a real resume never produces a committed file.

Changes to the skill (`skills/tailor/`) are measured with the eval suite in
`evals/`, which costs model usage to run; see `evals/README.md`.
