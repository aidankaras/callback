# callback

Tailoring a resume or cover letter for a specific role is high-effort, low-feedback work, and
generic language-model assistance tends to make it worse: it invents accomplishments and produces
documents that read as machine-written.

`callback` is a deterministic verification layer for that process: a candidate's facts are held in
a structured, provenance-tracked store, and generated text is checked mechanically against that
store rather than trusted on its face.

## What's implemented

- **Provenance-tracked facts** — every fact carries where it came from and how confirmed it is; only
  a fact the candidate explicitly confirmed can back generated text. Facts that would record a
  legally sensitive attribute (date of birth, SSN, marital status, and similar) are refused at write
  time, and technology names are canonicalised against a fixed alias table so the same tool can't
  end up stored under two different spellings (`core/profile`, `core/aliases`).
- **Grounding** — every number, entity, and date in a piece of text must be derivable from the cited
  facts: a derived figure is recomputed from its inputs rather than pattern-matched, and an entity is
  checked against the facts only when it's a known technology or looks like an identifier
  (`core/checks`). This is a traceability guarantee, not an absolute one — an invented product or
  tool name that is neither a known alias nor identifier-shaped currently passes grounding
  unchecked. See `docs/limitations.md` for the full list of what grounding does and does not catch.
- **Deterministic rendering** — a structured document (`core/resume`) is compiled to PDF with Typst
  (`core/render`) under conditions chosen so the same input always produces the same bytes: system
  fonts are ignored in favour of a vendored, licensed font family, any compiler warning (including a
  silently substituted font) is a hard failure rather than advisory, and the document's embedded
  timestamp is fixed rather than left to wall-clock time. This is the rendering step the layout gates
  below will measure; it does not itself check the result's structure or content.
- A minimal CLI (`callback init`, `callback delete-all`) for managing the private data directory.

## Planned

The parseability gate, the hidden-channels gate, additional layout gates, and a ledger of what was
generated and from which facts are not built yet:

- **Machine-parseable** — the rendered PDF will be re-extracted with a pessimistic parser and diffed
  against the structured source to catch ambiguous reading order or broken field segmentation before
  the candidate sees a review comment about it.
- **Free of hidden channels** — extracted text will be checked against visible text, and document
  metadata will be constrained to carry nothing beyond title and author.

`pyproject.toml` already pins the extraction dependency these will use (`pdfplumber`); it is not
called from `core/` yet. `pypdf` is already used, but only by rendering itself, to verify the fonts
it embedded — not yet for the broader text-and-structure extraction these gates will need.

See `docs/limitations.md` for what the verification gates do and do not guarantee.

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
