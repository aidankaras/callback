"""Command-line entry point for callback."""

from __future__ import annotations

import argparse

from core import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="callback")
    parser.add_argument("--version", action="version", version=f"callback {__version__}")
    parser.set_defaults(handler=None)

    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init", help="create the private data directory")
    init.set_defaults(handler=_cmd_init)

    return parser


def _cmd_init(_args: argparse.Namespace) -> int:
    from core.paths import ensure_home

    home = ensure_home()
    print(f"initialised {home}")
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
