# Vendored fonts

`templates/fonts/` contains the font files the renderer passes to Typst via
`font_paths`, with `ignore_system_fonts=True`. Rendering is only
byte-reproducible if the exact font bytes are fixed and shipped with the
repository rather than resolved from whatever happens to be installed on the
machine doing the rendering.

## Libertinus Serif

- **Family**: Libertinus Serif (a metrics-compatible, actively maintained
  successor to Linux Libertine).
- **Version**: 7.051.
- **Source**: <https://github.com/alerque/libertinus/releases/tag/v7.051>,
  `static/OTF/LibertinusSerif-{Regular,Bold,Italic}.otf` from
  `Libertinus-7.051.zip`.
- **Files vendored**: `LibertinusSerif-Regular.otf`, `LibertinusSerif-Bold.otf`,
  `LibertinusSerif-Italic.otf`.
- **License**: SIL Open Font License, Version 1.1 (`templates/fonts/OFL.txt`).
  The OFL permits embedding, bundling, and redistribution as part of this
  software, including in generated PDFs, without royalty.
- **Reserved names**: the licence reserves "Linux Libertine", "Biolinum", and
  "STIX Fonts" — a derivative of these fonts may not be distributed under
  those names. "Libertinus" itself is not a reserved name and is used here
  unmodified, so this restriction does not currently apply, but it would bind
  any future derivative work built from these files.
