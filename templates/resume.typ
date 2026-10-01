// The resume layout: one column, serif, black on white, in the dense
// shape recruiters in data science and engineering expect -- bold employer
// left, dates right, italic title beneath, tight bullets, ruled section
// headings. Density comes from the gaps *between* blocks, not from the line
// pitch inside one.
//
// `hyphenate: false` and `justify: false` are load-bearing, not style
// choices: a justified, hyphenated line can break a keyword across two
// lines ("infra-" / "structure"), which silently zeroes a grounding match
// on the whole word. The bullet marker below is emitted as a literal glyph
// (`•`) in its own 10pt grid cell rather than a list marker, so a later
// geometry pass can find a bullet's start by looking for that character
// directly instead of interpreting Typst's own list layout.
//
// `leading` (0.63em) is looser than the first version's 0.55em, never
// tighter: the naive extractor clusters rows with a 3.0pt tolerance measured
// against the line pitch, and a larger pitch only widens that margin. Block
// spacing (the gap between two bullets, or between an entry and the next)
// equals the leading, so the gap between two bullets is never smaller than
// the gap between two lines of one bullet.
// One accent colour for the name, section headings and their rules: dark
// enough to print as near-black, and invisible to a text extractor, which
// reads the same characters whatever their colour.
#let accent = rgb("#1f3a5f")

#let resume(identity: (:), sections: (), font: "Libertinus Serif", leading: 0.63em) = {
  set document(title: identity.name)
  set page(paper: "us-letter", margin: (x: 0.55in, y: 0.45in))
  set text(font: font, size: 10.5pt, hyphenate: false)
  set par(justify: false, leading: leading, spacing: leading)
  set block(spacing: leading)

  align(center)[
    #text(size: 20pt, weight: "bold", fill: accent)[#identity.name]
    #v(-0.35em)
    #text(size: 10pt)[#identity.contact_line]
  ]

  for section in sections {
    block(above: 1.3em, below: 0.6em, width: 100%)[
      #text(size: 11pt, weight: "bold", fill: accent)[#upper(section.title)]
      // A smaller pull than the block gap, so the rule lands below the
      // heading's descenders (the J in PROJECTS) rather than through them;
      // measured by extracting both positions, not by eye.
      #v(-0.12em)
      #line(length: 100%, stroke: 0.7pt + accent)
    ]
    if section.skills.len() > 0 {
      // Body size and weight, deliberately: a skills run rendered at
      // heading weight reads as an employer name to a parser that anchors
      // on bold text, and this project's own round-trip gate anchors on
      // entry headings.
      par(justify: false)[#section.skills.join(", ")]
    }
    // A grouped row is `Label: a, b, c` with the label in bold, the
    // convention reviewers expect. No trailing period: the round-trip gate
    // compares the last item exactly. The colon is the separator the gate
    // strips (`core.checks.roundtrip._row_items`), which is why neither a
    // label nor a skill may contain one.
    for group in section.skill_groups {
      // A hanging indent, so a row that wraps reads as the same row.
      par(justify: false, hanging-indent: 1.2em)[#text(weight: "bold")[#group.label:] #group.skills.join(", ")]
    }
    for (i, entry) in section.entries.enumerate() {
      if i > 0 { v(0.45em) }
      // Two rows when the entry has a title, a place and dates: employer and
      // dates in bold on the first, so each listing opens with one bold band a
      // skimming reader can find; title and place in italic on the second.
      // A right-hand cell on the heading row is set at the heading's own size:
      // at a smaller size, poppler's reading order (what many screeners use)
      // put the date before the employer, measured on a real render.
      // Otherwise the heading row carries whatever right-hand text exists.
      if entry.subheading != none and entry.location != none and entry.dates != "" {
        grid(
          columns: (1fr, auto),
          align: (left, right),
          row-gutter: 0.45em,
          text(weight: "bold")[#entry.heading],
          text(weight: "bold")[#entry.dates],
          text(style: "italic", size: 10pt)[#entry.subheading],
          text(style: "italic", size: 10pt)[#entry.location],
        )
      } else {
        grid(
          columns: (1fr, auto),
          text(weight: "bold")[#entry.heading],
          text(weight: "bold")[#entry.right_line],
        )
        if entry.subheading != none {
          v(-0.2em)
          text(style: "italic", size: 10pt)[#entry.subheading]
        }
      }
      for bullet in entry.bullets {
        pad(left: 4pt, grid(columns: (10pt, 1fr), [•], [#bullet]))
      }
    }
  }
}
