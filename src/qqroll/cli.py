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
    from qqroll.config import ConfigError, load, rollers

    try:
        cfg = load(args.infra_config)
        files = dependabot.generate(rollers(cfg, args.infra_config), _commit(args),
                                    backend=cfg.get("org", {}).get("org", {}).get("default_backend", "github"),
                                    slugs=dependabot.github_slugs(cfg))
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


def cmd_roll(args) -> int:
    """Roll one local manifest in place: the same edit the rotation makes, for people and `qq roll`."""
    from qqroll import promoted, roll
    from qqroll.config import ConfigError, load, toolchain_pins

    try:
        kinds_pins = toolchain_pins(load(args.infra_config)) if args.infra_config else None
        new = promoted.parse(Path(args.promoted).read_text(), args.promoted)
        with open(args.manifest, encoding="utf-8", newline="") as f:
            text = f.read()
        r = roll.plan(text, new, source=args.manifest, kinds_pins=kinds_pins)
    except (OSError, ConfigError, promoted.PromotedError, roll.ManifestError) as e:
        print(f"qqroll: {e}", file=sys.stderr)
        return 1
    for s in r.skipped:
        print(f"skipped {s}", file=sys.stderr)
    for c in r.changes:
        print(f"rolled {c}")
    if not r.changed:
        print("toolchain pins are current")
    elif not args.dry_run:
        with open(args.manifest, "w", encoding="utf-8", newline="") as f:
            f.write(r.new_text)
    return 0


def cmd_rotation(args) -> int:
    """The rotation: roll every repo rollers.toml lists and open roll PRs (V0-ROL-01)."""
    import os

    from qqroll import backends, dependabot, promoted, rotation
    from qqroll.backends import BackendError
    from qqroll.config import ConfigError, load, rollers, toolchain_pins

    token = os.environ.get("QQ_ROLLER_TOKEN") or None
    read_token = token or os.environ.get("QQ_READ_TOKEN") or None  # dry runs read with any token
    if args.apply and not token:
        print("qqroll: --apply needs QQ_ROLLER_TOKEN, the quirq infra bot's token. PRs opened with a "
              "workflow's GITHUB_TOKEN trigger no workflows, so they would never be gated.", file=sys.stderr)
        return 1
    try:
        cfg = load(args.infra_config)
        new = promoted.parse(Path(args.promoted).read_text(), args.promoted)
        name = args.backend or cfg.get("org", {}).get("org", {}).get("default_backend", "github")
        backend = backends.load(name, token=token if args.apply else read_token, slugs=dependabot.github_slugs(cfg))
        report = rotation.run(backend, rollers(cfg), new, kinds_pins=toolchain_pins(cfg),
                              promoted_from=args.promoted_from, rollers_commit=args.rollers_commit,
                              apply=args.apply, auto_merge=args.auto_merge,
                              manifests={r["name"]: r["manifest"] for r in cfg.get("repos", {}).get("repo", [])
                                         if "manifest" in r})
    except (OSError, ValueError, ConfigError, promoted.PromotedError, BackendError) as e:
        print(f"qqroll: {e}", file=sys.stderr)
        return 1
    for line in report.lines:
        print(line)
    for w in report.warnings:
        print(f"warning: {w}", file=sys.stderr)
    return 1 if report.failed else 0


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

    p = sub.add_parser("roll", help="roll one manifest's toolchain pins to promoted digests (V0-ROL-01)")
    p.add_argument("--promoted", required=True, help="quirq-ai/toolchains promoted.toml")
    p.add_argument("--manifest", default="infra/repo.toml", help="manifest to edit in place")
    p.add_argument("--infra-config", help="infra-config checkout; keeps rolls inside its kinds.toml pins")
    p.add_argument("--dry-run", action="store_true", help="report the roll without writing")
    p.set_defaults(func=cmd_roll)

    p = sub.add_parser("rotation", help="roll every repo rollers.toml lists and open roll PRs (V0-ROL-01)")
    p.add_argument("--infra-config", required=True, help="path to a quirq-ai/infra-config checkout")
    p.add_argument("--promoted", required=True, help="quirq-ai/toolchains promoted.toml")
    p.add_argument("--promoted-from", default="an unrecorded commit",
                   help="where promoted.toml came from, for the PR body (e.g. quirq-ai/toolchains@<sha>)")
    p.add_argument("--rollers-commit", default="an unrecorded commit", help="this repo's commit, for the PR body")
    p.add_argument("--backend", help="where repos live (default: infra-config org default_backend; "
                                     "github now, launchpad later)")
    p.add_argument("--apply", action="store_true", help="open roll PRs; default is a dry run")
    p.add_argument("--auto-merge", action="store_true",
                   help="turn on auto-merge so a roll lands once the gate passes (default: stop at the PR)")
    p.set_defaults(func=cmd_rotation)
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
