"""Command-line entry point for callback."""

from __future__ import annotations

import argparse
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

    return parser


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
