// The cover-letter layout: the same type and page as the resume, one column,
// no decoration. `hyphenate: false` for the same reason as the resume: a word
// broken across lines is a word a text search no longer finds.
#let letter(identity: (:), greeting: "", paragraphs: (), closing: "", font: "Libertinus Serif") = {
  set document(title: identity.name)
  set page(paper: "us-letter", margin: (x: 1in, y: 1in))
  set text(font: font, size: 11pt, hyphenate: false)
  set par(justify: false, leading: 0.65em, spacing: 1.1em)

  text(size: 14pt, weight: "bold")[#identity.name]
  linebreak()
  text(size: 9pt)[#identity.contact_line]
  v(18pt)

  par[#greeting]
  for paragraph in paragraphs {
    par[#paragraph]
  }
  v(6pt)
  par[#closing]
  par[#identity.name]
}
