"""The toolchain roller rotation: for every repo the `quirq-rollers` toolchains roller covers, roll
its manifest to the promoted digests and open (or refresh) one roll PR."""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from qqsync.manifest import DEFAULT_PATH

from qqroll import roll
from qqroll.promoted import Promoted

BRANCH = "qq-roll/toolchains"
ROLLER = "toolchains"


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    prs: list[str] = field(default_factory=list)

    def say(self, line: str) -> None:
        self.lines.append(line)


def title(changes: list[roll.Change]) -> str:
    names = sorted({f"{c.new.name} {c.new.version}-r{c.new.revision}" for c in changes})
    return "roll: toolchains " + ", ".join(names)


def body(changes: list[roll.Change], promoted_from: str, rollers_commit: str, auto_merge: bool) -> str:
    landing = ("An agent may land it alone once the gate passes (D4); auto-merge is on." if auto_merge else
               "Auto-merge is off for now, so it waits at an open PR for someone to land it.")
    rows = "\n".join(f"| {c.toolchain} | {c.platform or 'all'} | `{c.old_digest}` | `{c.new.digest}` | "
                     f"{c.new.version}-r{c.new.revision} | {c.new.build_run or ''} |" for c in changes)
    return (f"Moves toolchain pins in `{DEFAULT_PATH}` to the digests quirq-ai/toolchains promoted "
            f"(`promoted.toml` at {promoted_from}).\n\n"
            "| Toolchain | Platform | From | To | Version | Build |\n|---|---|---|---|---|---|\n"
            f"{rows}\n\n"
            f"Change class: `dependency-roll` (infra-config gate.toml). {landing}\n\n"
            f"Written through qqsync by quirq-ai/rollers at {rollers_commit} (V0-ROL-01).\n")


def run(backend, rollers: list[dict], promoted: list[Promoted], *, kinds_pins: dict[str, str] | None,
        promoted_from: str, rollers_commit: str, apply: bool, auto_merge: bool = False) -> Report:
    report = Report()
    config = [r for r in rollers if r["tool"] == "quirq-rollers" and r["name"] == ROLLER]
    if not config:
        report.warnings.append(f"rollers.toml has no quirq-rollers roller named {ROLLER!r}; nothing to roll")
        return report
    repos = sorted({repo for r in config for repo in r["repos"]})
    for repo in repos:
        base = backend.default_branch(repo)
        text = backend.read_file(repo, str(DEFAULT_PATH), base)
        if text is None:
            report.say(f"{repo}: no {DEFAULT_PATH} on {base} yet (onboarding, V0-ONB-01); skipped")
            continue
        try:
            r = roll.plan(text, promoted, source=f"{repo}:{DEFAULT_PATH}", kinds_pins=kinds_pins)
        except roll.ManifestError as e:
            report.warnings.append(f"{repo}: manifest is invalid, not rolled:\n{e}")
            continue
        report.warnings.extend(f"{repo}: {s}" for s in r.skipped)
        if not r.changed:
            report.say(f"{repo}: toolchain pins are current")
            continue
        for c in r.changes:
            report.say(f"{repo}: {c}")
        if not apply:
            report.say("".join(difflib.unified_diff(
                r.old_text.splitlines(keepends=True), r.new_text.splitlines(keepends=True),
                f"a/{DEFAULT_PATH}", f"b/{DEFAULT_PATH}")).rstrip("\n"))
            report.say(f"{repo}: dry run; pass --apply to open the roll PR")
            continue
        url, warnings = backend.open_roll(repo, base=base, branch=BRANCH, path=str(DEFAULT_PATH),
                                          text=r.new_text, title=title(r.changes),
                                          body=body(r.changes, promoted_from, rollers_commit, auto_merge),
                                          auto_merge=auto_merge)
        report.prs.append(url)
        report.warnings.extend(warnings)
        report.say(f"{repo}: roll PR {url}")
    return report


__all__ = ["run", "BRANCH"]
