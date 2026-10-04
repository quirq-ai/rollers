"""V0-ROL-01: move a repo's toolchain pins to the digests quirq-ai/toolchains promoted.

The manifest is read and edited only through `qqsync` (V0-SYN-02), so a roll changes only the pin
lines it must and leaves every other byte alone. This is the core both the GitHub rotation and, in v1,
`qq roll` use, so the two produce the same diffs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from qqsync.errors import ManifestError
from qqsync.manifest import Manifest

from qqroll.promoted import Promoted


@dataclass(frozen=True)
class Change:
    toolchain: str
    platform: str | None   # None for a pin that covers every platform
    old_digest: str
    new: Promoted

    def __str__(self) -> str:
        where = f"{self.toolchain} ({self.platform})" if self.platform else self.toolchain
        return f"{where}: {self.old_digest} -> {self.new.digest} ({self.new.version}-r{self.new.revision})"


@dataclass
class Roll:
    old_text: str
    new_text: str
    changes: list[Change] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # why a pin was left alone; a person can act on it

    @property
    def changed(self) -> bool:
        return self.new_text != self.old_text


def _track_allows(toolchain: str, version: str, kinds_pins: dict[str, str] | None) -> bool:
    """kinds.toml pins each toolchain org-wide (python "3.14", node "24"); a roll stays inside it."""
    if kinds_pins is None or toolchain not in kinds_pins:
        return True
    pin = kinds_pins[toolchain]
    return version == pin or version.startswith(pin + ".")


def plan(text: str, promoted: list[Promoted], *, source: str = "infra/repo.toml",
         kinds_pins: dict[str, str] | None = None) -> Roll:
    """The roll for one manifest. Raises ManifestError if the manifest is invalid.

    Only toolchains the manifest already pins, from the same source the promotion names, are moved.
    A promotion outside the org-wide pin in infra-config kinds.toml (a new minor or major) is not rolled:
    moving that pin is a policy change for suraj.
    """
    manifest = Manifest(text, source)
    roll = Roll(old_text=text, new_text=text)
    pins = manifest.data.get("toolchains", {})
    by_key = {(p.name, p.platform): p for p in promoted}
    for name in sorted(pins):
        pin = pins[name]
        if "platforms" in pin:
            targets = [(platform, pin["platforms"][platform]) for platform in sorted(pin["platforms"])]
        else:
            targets = [(None, pin)]
        for platform, current in targets:
            new = by_key.get((name, platform)) if platform else _single(promoted, name)
            if new is None:
                continue  # nothing promoted for this toolchain on this platform
            if current.get("source") != new.source:
                roll.skipped.append(f"{name}: source {current.get('source')!r} is not {new.source!r}; "
                                    "not a quirq-ai/toolchains pin")
                continue
            if current["digest"] == new.digest:
                continue
            if not _track_allows(name, new.version, kinds_pins):
                roll.skipped.append(f"{name}: promoted {new.version} is outside the kinds.toml pin "
                                    f"{kinds_pins[name]!r}; moving that pin is a policy change for suraj")
                continue
            manifest.set_pin("toolchains", name, digest=new.digest, version=new.version, platform=platform)
            roll.changes.append(Change(name, platform, current["digest"], new))
    roll.new_text = manifest.dumps()
    return roll


def _single(promoted: list[Promoted], name: str) -> Promoted | None:
    """For a pin that covers every platform: the promotion, if there is exactly one for `name`."""
    matches = [p for p in promoted if p.name == name]
    return matches[0] if len(matches) == 1 else None


__all__ = ["Change", "Roll", "plan", "ManifestError"]
