// The resume layout. Kept deliberately plain: this is the fast path that
// gets a candidate a usable PDF quickly, not the final visual design.
//
// `hyphenate: false` and `justify: false` are load-bearing, not style
// choices: a justified, hyphenated line can break a keyword across two
// lines ("infra-" / "structure"), which silently zeroes a grounding match
// on the whole word. The bullet marker below is emitted as a literal glyph
// (`•`) in its own grid cell rather than a list marker, so a later geometry
// pass can find a bullet's start by looking for that character directly
// instead of interpreting Typst's own list layout.
#let resume(identity: (:), sections: (), font: "Libertinus Serif") = {
  set document(title: identity.name)
  set page(paper: "us-letter", margin: (x: 0.6in, y: 0.5in))
  set text(font: font, size: 10pt, hyphenate: false)
  set par(justify: false, leading: 0.55em)

  align(center)[
    #text(size: 16pt, weight: "bold")[#identity.name]
    #linebreak()
    #text(size: 9pt)[#identity.contact_line]
  ]
  v(6pt)

  for section in sections {
    text(size: 11pt, weight: "bold")[#upper(section.title)]
    line(length: 100%, stroke: 0.5pt)
    v(2pt)
    for entry in section.entries {
      grid(
        columns: (1fr, auto),
        text(weight: "bold")[#entry.heading],
        text(size: 9pt)[#entry.right_line],
      )
      if entry.subheading != none { text(style: "italic", size: 9.5pt)[#entry.subheading] }
      if entry.bullets.len() > 0 {
        v(1pt)
        for bullet in entry.bullets {
          grid(columns: (10pt, 1fr), [•], [#bullet])
        }
      }
      v(4pt)
    }
    v(2pt)
  }
}
