"""Command-line entry point for callback."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from core import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="callback")
    parser.add_argument("--version", action="version", version=f"callback {__version__}")
    parser.set_defaults(handler=None)

    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init", help="create the private data directory")
    init.set_defaults(handler=_cmd_init)

    delete_all = sub.add_parser(
        "delete-all", help="permanently remove everything under the private data directory"
    )
    delete_all.set_defaults(handler=_cmd_delete_all)

    build = sub.add_parser(
        "build", help="render a resume document and run every gate on it (writes a draft)"
    )
    build.add_argument("doc", type=Path, help="resume document (YAML)")
    build.add_argument("--job", required=True, help="job id: lowercase letters, digits, hyphens")
    build.set_defaults(handler=_cmd_build)

    imp = sub.add_parser("import", help="read an existing resume PDF, screening it for hidden text")
    imp.add_argument("file", type=Path)
    imp.set_defaults(handler=_cmd_import)

    facts = sub.add_parser("facts", help="add, list and confirm the facts a resume may use")
    facts_sub = facts.add_subparsers(dest="facts_command", required=True)
    add = facts_sub.add_parser("add", help="add facts from a YAML list (they start unconfirmed)")
    add.add_argument("file", type=Path)
    add.set_defaults(handler=_cmd_facts_add)
    listing = facts_sub.add_parser("list", help="list stored facts")
    listing.add_argument("--unconfirmed", action="store_true", help="only unconfirmed facts")
    listing.set_defaults(handler=_cmd_facts_list)
    conf = facts_sub.add_parser(
        "confirm", help="confirm facts interactively; must be run by the candidate in a terminal"
    )
    conf.add_argument("ids", nargs="*", help="fact ids (default: every unconfirmed fact)")
    conf.set_defaults(handler=_cmd_facts_confirm)

    return parser


_SAFE_STEM = re.compile(r"[^a-z0-9-]+")


def _cmd_import(args: argparse.Namespace) -> int:
    from core.ingest.extract import ExtractionError, extract_document, gap_audit
    from core.paths import ensure_home, resolve_inside, write_private

    try:
        result = extract_document(args.file)
    except (ExtractionError, OSError) as exc:
        print(f"callback import: {exc}", file=sys.stderr)
        return 3
    ensure_home()
    stem = _SAFE_STEM.sub("-", args.file.stem.casefold()).strip("-") or "resume"
    out = resolve_inside(f"inbox/{stem}.txt")
    write_private(out, result.text)

    unscreened = result.unscreened
    found = [f for f in result.findings if f not in unscreened]
    if found:
        print("HIDDEN CONTENT FOUND -- do not trust the extracted text as written:")
        print("\n".join(f"  {f.kind}: {f.detail}" for f in found))
    if unscreened:
        print("COULD NOT SCREEN -- these parts were not checked for hidden text:")
        print("\n".join(f"  {f.kind}: {f.detail}" for f in unscreened))
    print(f"{result.pages} page(s) read; text written to {out}")
    gaps = gap_audit(result.text)
    if gaps:
        print("Advice (never a verdict):")
        print("\n".join(f"  {g}" for g in gaps))
    return 4 if result.findings else 0


def _cmd_facts_add(args: argparse.Namespace) -> int:
    from core.factscmd import add_from_file
    from core.paths import ensure_home
    from core.profile.store import StoreError

    ensure_home()
    try:
        added, rejected = add_from_file(args.file)
    except (StoreError, OSError) as exc:
        print(f"callback facts add: {exc}", file=sys.stderr)
        return 3
    print(f"added {len(added)} fact(s), all unconfirmed until you run `callback facts confirm`")
    for fid, reason in rejected:
        print(f"  refused {fid}: {reason}", file=sys.stderr)
    return 1 if rejected else 0


def _cmd_facts_list(args: argparse.Namespace) -> int:
    from core.profile.store import load_facts

    for fid, fact in load_facts().items():
        if args.unconfirmed and fact.selectable():
            continue
        mark = "confirmed  " if fact.selectable() else "UNCONFIRMED"
        print(f"{mark} {fid:<24} {fact.kind:<18} {fact.text}")
    return 0


def _cmd_facts_confirm(args: argparse.Namespace) -> int:
    from core.factscmd import ConfirmError, confirm

    try:
        confirmed = confirm(list(args.ids))
    except ConfirmError as exc:
        print(f"callback facts confirm: {exc}", file=sys.stderr)
        return 3
    print(f"confirmed {len(confirmed)} fact(s)")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    from core.buildcmd import BuildError, build
    from core.profile.store import StoreError
    from core.resume.io import DocumentError

    try:
        result = build(args.doc, args.job)
    except (BuildError, DocumentError, StoreError, OSError) as exc:
        print(f"callback build: {exc}", file=sys.stderr)
        return 3
    print(f"{result.report.verdict.upper()}  {result.out_dir / 'resume.pdf'}")
    for outcome in result.report.outcomes:
        print(f"  {outcome.gate:<10} {outcome.verdict}")
    print(f"report: {result.out_dir / 'report.md'}")
    return result.exit_code


def _cmd_init(_args: argparse.Namespace) -> int:
    from core.paths import ensure_home

    home = ensure_home()
    print(f"initialised {home}")
    return 0


def _cmd_delete_all(_args: argparse.Namespace) -> int:
    from core.paths import delete_all

    for removed in delete_all():
        print(removed)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.handler is None:
        parser.print_help()
        return 0
    result: int = args.handler(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
