"""The toolchain roller rotation: for every repo the `quirq-rollers` toolchains roller covers, roll
its manifest to the promoted digests and open (or refresh) one roll PR."""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from qqsync.manifest import DEFAULT_PATH

from qqroll import roll
from qqroll.backends import BackendError
from qqroll.promoted import Promoted

BRANCH = "qq-roll/toolchains"
ROLLER = "toolchains"


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    prs: list[str] = field(default_factory=list)
    failed: bool = False   # a repo could not be rolled; the others still were

    def say(self, line: str) -> None:
        self.lines.append(line)


def _cell(value: str) -> str:
    """A Markdown table cell: no pipes or line breaks from data."""
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def title(changes: list[roll.Change]) -> str:
    names = sorted({f"{c.new.name} {c.new.version}-r{c.new.revision}" for c in changes})
    return "roll: toolchains " + ", ".join(names)


def body(changes: list[roll.Change], promoted_from: str, rollers_commit: str, auto_merge: bool,
         path: str = str(DEFAULT_PATH)) -> str:
    landing = ("An agent may land it alone once the gate passes (D4); auto-merge is on." if auto_merge else
               "Auto-merge is off for now, so it waits at an open PR for someone to land it.")
    rows = "\n".join("| " + " | ".join(_cell(v) for v in (
        c.toolchain, c.platform, f"`{c.old_digest}`", f"`{c.new.manifest}` / `{c.new.digest}`",
        f"{c.new.version}-r{c.new.revision}", c.new.build_run)) + " |" for c in changes)
    return (f"Moves toolchain pins in `{path}` to the digests quirq-ai/toolchains promoted "
            f"(`promoted.toml` read with its qqtc at {promoted_from}).\n\n"
            "| Toolchain | Platform | From (layer) | To (manifest / layer) | Version | Build |\n"
            "|---|---|---|---|---|---|\n"
            f"{rows}\n\n"
            "Each new pin was checked before this PR was written: its manifest is exactly the one pinned "
            "layer, and its build provenance verifies against toolchains' build.yml on main.\n\n"
            f"Change class: `dependency-roll` (infra-config gate.toml). {landing}\n\n"
            f"Written through qqsync by quirq-ai/rollers at {rollers_commit} (V0-ROL-01).\n")


def run(backend, rollers: list[dict], promoted: list[Promoted], *, kinds_pins: dict[str, str] | None,
        promoted_from: str, rollers_commit: str, apply: bool, auto_merge: bool = False,
        manifests: dict[str, str] | None = None) -> Report:
    """`manifests` maps repo names to their manifest path (infra-config repos.toml), default infra/repo.toml."""
    report = Report()
    verified: dict[Promoted, str | None] = {}
    config = [r for r in rollers if r["tool"] == "quirq-rollers" and r["name"] == ROLLER]
    if not config:
        report.warnings.append(f"rollers.toml has no quirq-rollers roller named {ROLLER!r}; nothing to roll")
        return report
    for repo in sorted({repo for r in config for repo in r["repos"]}):
        path = (manifests or {}).get(repo, str(DEFAULT_PATH))
        try:
            _roll_repo(backend, repo, path, promoted, report, verified, kinds_pins=kinds_pins,
                       promoted_from=promoted_from, rollers_commit=rollers_commit, apply=apply,
                       auto_merge=auto_merge)
        except BackendError as e:
            report.failed = True
            report.warnings.append(f"{repo}: not rolled: {e}")
    return report


def _roll_repo(backend, repo, path, promoted, report, verified, *, kinds_pins, promoted_from, rollers_commit, apply,
               auto_merge) -> None:
    base = backend.default_branch(repo)
    base_sha = backend.head(repo, base)  # read and commit against the same commit, so nothing is reverted
    text = backend.read_file(repo, path, base_sha)
    if text is None:
        report.say(f"{repo}: no {path} on {base} yet (onboarding, V0-ONB-01); skipped")
        return
    try:
        r = roll.plan(text, promoted, source=f"{repo}:{path}", kinds_pins=kinds_pins)
    except roll.ManifestError as e:
        report.failed = True
        report.warnings.append(f"{repo}: manifest is invalid, not rolled:\n{e}")
        return
    report.warnings.extend(f"{repo}: {s}" for s in r.skipped + r.notes)
    if r.skipped:
        report.failed = True  # a skipped pin needs a person; never report it as current
    if not r.changed:
        # TODO(expert): close a leftover qq-roll/toolchains PR once main is current by other means.
        report.say(f"{repo}: not rolled ({len(r.skipped)} skipped)" if r.skipped
                   else f"{repo}: toolchain pins are current")
        return
    for p in {c.new for c in r.changes}:
        if p not in verified:
            verified[p] = backend.verify_promotion(p)
    bad = sorted({f"{p.label}: {verified[p]}" for p in {c.new for c in r.changes} if verified[p]})
    if bad:
        report.failed = True
        report.warnings.extend(f"{repo}: not rolled, a promotion did not verify: {b}" for b in bad)
        report.say(f"{repo}: not rolled (a promotion did not verify)")
        return
    for c in r.changes:
        report.say(f"{repo}: {c}")
    if not apply:
        report.say("".join(difflib.unified_diff(
            r.old_text.splitlines(keepends=True), r.new_text.splitlines(keepends=True),
            f"a/{path}", f"b/{path}")).rstrip("\n"))
        report.say(f"{repo}: dry run; pass --apply to open the roll PR")
        return
    url, warnings = backend.open_roll(repo, base=base, base_sha=base_sha, branch=BRANCH, path=path,
                                      text=r.new_text, title=title(r.changes),
                                      body=body(r.changes, promoted_from, rollers_commit, auto_merge, path),
                                      auto_merge=auto_merge)
    report.prs.append(url)
    report.warnings.extend(warnings)
    report.say(f"{repo}: roll PR {url}")


__all__ = ["run", "BRANCH"]
