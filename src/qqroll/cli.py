"""The `qqroll` command line."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qqroll import __version__


def _commit(args) -> str:
    if args.commit:
        return args.commit
    pin = Path("infra-config.commit")
    if pin.is_file():
        return pin.read_text().strip()
    raise SystemExit("qqroll: pass --commit (the infra-config commit the config came from)")


def cmd_dependabot(args) -> int:
    from qqroll import dependabot
    from qqroll.config import ConfigError, load_rollers

    try:
        files = dependabot.generate(load_rollers(args.infra_config), _commit(args))
    except (ConfigError, dependabot.GenerateError) as e:
        print(f"qqroll: {e}", file=sys.stderr)
        return 1
    out = Path(args.out)
    if args.write:
        dependabot.write(out, files)
        print(f"wrote {len(files)} files under {out}")
        return 0
    problems = dependabot.check(out, files)
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        return 1
    print(f"PASS: {len(files)} generated files match rollers.toml")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qqroll", description="Rollers for quirq infra (qq).")
    parser.add_argument("--version", action="version", version=f"qqroll {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("dependabot", help="generate or check Dependabot config from rollers.toml (V0-ROL-02)")
    p.add_argument("--infra-config", required=True, help="path to a quirq-ai/infra-config checkout")
    p.add_argument("--commit", help="infra-config commit, recorded in the generated files "
                                    "(default: ./infra-config.commit)")
    p.add_argument("--out", default="generated", help="output directory (default: generated)")
    p.add_argument("--write", action="store_true", help="write the files; default is to check them")
    p.set_defaults(func=cmd_dependabot)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
