# callback

Tailoring a resume or cover letter for a specific role is high-effort, low-feedback work, and
generic language-model assistance tends to make it worse: it invents accomplishments and produces
documents that read as machine-written.

`callback` addresses this with a deterministic verification layer around document generation. A
candidate's facts are held in a structured, provenance-tracked store; every generated document is
checked mechanically before it is delivered:

- **Grounded** — every claim in the output is traceable to a fact the candidate supplied or a value
  derived from one; nothing is fabricated.
- **Machine-parseable** — documents are rendered from a structured source, then re-extracted with a
  pessimistic parser and diffed against the source to catch ambiguous reading order or broken field
  segmentation before the candidate ever sees a review comment about it.
- **Free of hidden channels** — extracted text must match visible text, and document metadata
  carries nothing beyond title and author.

See `docs/limitations.md` for what the verification gates do and do not guarantee.

## Status

This repository currently provides the project scaffolding: a pinned Python toolchain, CI, and a
minimal CLI entry point. The verification and generation pipeline is under active development.

## Development

Requires Python 3.12 and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

The `callback` console script is installed into the project's virtual environment by `uv sync`:

```bash
uv run callback --version
```

## License

MIT — see `LICENSE`.
