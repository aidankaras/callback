"""Hand-built PDFs from raw content streams, for pages no Typst source produces.

Typst always emits at least one page and never a text run made only of
spaces, so the inputs that matter most to an *import* -- a PDF with no
pages, a page whose text layer is only whitespace -- have to be written
directly. Helvetica is referenced as a standard 14 font, so nothing is
embedded and the output is a few hundred bytes.
"""

from __future__ import annotations

from pathlib import Path


def make(path: Path, contents: bytes, *, npages: int = 1, form: bytes | None = None) -> None:
    """Write a US-letter PDF of `npages` pages, each drawing `contents`.

    With `form`, every page's resources also name a form XObject `/X1` whose
    content stream is `form`; `contents` draws it with `/X1 Do`.
    """
    form_ref = f"{4 + 2 * npages} 0 R".encode()
    xobject = b" /XObject << /X1 " + form_ref + b" >>" if form is not None else b""
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + " ".join(f"{4 + i} 0 R" for i in range(npages)).encode()
        + b"] /Count "
        + str(npages).encode()
        + b" >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    objs += [
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 3 0 R >>"
        + xobject
        + b" >> /Contents "
        + str(4 + npages + i).encode()
        + b" 0 R >>"
        for i in range(npages)
    ]
    objs += [
        b"<< /Length " + str(len(contents)).encode() + b" >>\nstream\n" + contents + b"\nendstream"
        for _ in range(npages)
    ]
    if form is not None:
        objs.append(
            b"<< /Type /XObject /Subtype /Form /BBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Length "
            + str(len(form)).encode()
            + b" >>\nstream\n"
            + form
            + b"\nendstream"
        )
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objs):
        offsets.append(len(out))
        out += f"{i + 1} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
