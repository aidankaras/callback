"""A plain-text resume, for application forms that ask for one pasted in.

Many application systems take a resume as pasted text rather than a file, and
what they keep is whatever survives the paste. The export is therefore laid
out for a text box, not a page: no columns, no tabs, bullets as a leading
"- " with wrapped lines hanging under the text, and each entry's header on one
line.

Wrapping never breaks a word or a hyphenated token, so a field value can
move to the next line but is never split inside itself. `check_text_export`
then applies the same rule the PDF round-trip gate applies to critical
fields: each must be recoverable from the text, ignoring line breaks.
"""

from __future__ import annotations

import textwrap
from typing import Literal

from core.resume.models import Entry, ResumeDoc


def _dates(entry: Entry) -> str:
    if entry.start and entry.end:
        return f"{entry.start} - {entry.end}"
    return entry.start or entry.end or ""


def _wrap(text: str, width: int, *, first: str = "", rest: str = "") -> list[str]:
    return textwrap.wrap(
        text,
        width=width,
        initial_indent=first,
        subsequent_indent=rest,
        break_long_words=False,
        break_on_hyphens=False,
    )


def to_text(doc: ResumeDoc, *, width: int = 78) -> str:
    """`doc` as plain text, every line at most `width` characters where words allow."""
    identity = doc.identity
    contact = " | ".join([identity.email, *filter(None, [identity.phone]), *identity.links])
    lines = [identity.name, *_wrap(contact, width)]
    for section in doc.sections:
        lines += ["", section.title.upper()]
        for entry in section.entries:
            header = " | ".join(
                part
                for part in (
                    entry.heading,
                    entry.subheading,
                    entry.location,
                    _dates(entry),
                )
                if part
            )
            lines += _wrap(header, width)
            for bullet in entry.bullets:
                lines += _wrap(bullet.text, width, first="- ", rest="  ")
        if section.skills:
            lines += _wrap(", ".join(section.skills), width)
    return "\n".join(lines) + "\n"


def _critical(doc: ResumeDoc) -> list[tuple[str, str]]:
    identity = doc.identity
    fields = [("name", identity.name), ("email", identity.email)]
    if identity.phone:
        fields.append(("phone", identity.phone))
    index = 0
    for n, section in enumerate(doc.sections):
        fields.append((f"section_title[{n}]", section.title.upper()))
        for entry in section.entries:
            fields.append((f"employer[{index}]", entry.heading))
            for name, value in (
                ("title", entry.subheading),
                ("location", entry.location),
                ("dates", _dates(entry)),
            ):
                if value:
                    fields.append((f"{name}[{index}]", value))
            index += 1
        fields += [(f"skill[{n}][{i}]", skill) for i, skill in enumerate(section.skills)]
    return fields


def check_text_export(doc: ResumeDoc, text: str) -> tuple[Literal["pass", "fail"], list[str]]:
    """Every critical field of `doc` that cannot be found in `text`, ignoring line breaks."""
    flat = " ".join(text.split())
    missing = [
        f"{name}: {value!r}"
        for name, value in _critical(doc)
        if " ".join(value.split()) not in flat
    ]
    return ("fail" if missing else "pass"), missing
