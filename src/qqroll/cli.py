"""The `qqroll` command line."""
from __future__ import annotations

import argparse
import sys

from qqroll import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qqroll", description=__doc__)
    parser.add_argument("--version", action="version", version=f"qqroll {__version__}")
    parser.add_subparsers(dest="command")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
