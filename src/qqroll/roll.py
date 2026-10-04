"""V0-ROL-01: move a repo's toolchain pins to the digests quirq-ai/toolchains promoted.

The manifest is read and edited only through `qqsync` (V0-SYN-02), so a roll changes only the pin
lines it must and leaves every other byte alone. This is the core both the GitHub rotation and, in v1,
`qq roll` use, so the two produce the same diffs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from qqsync.errors import ManifestError
from qqsync.manifest import Manifest
from qqsync.pins import PinError, oci_parts

from qqroll.promoted import Promoted


@dataclass(frozen=True)
class Change:
    toolchain: str
    platform: str
    old_source: str
    old_digest: str
    new: Promoted

    def __str__(self) -> str:
        return (f"{self.toolchain} ({self.platform}): {self.old_source} {self.old_digest} -> "
                f"{self.new.source} {self.new.digest} ({self.new.version}-r{self.new.revision})")


@dataclass
class Roll:
    old_text: str
    new_text: str
    changes: list[Change] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # why a pin was left alone; a person can act on it
    notes: list[str] = field(default_factory=list)     # worth knowing, but every pin was rolled

    @property
    def changed(self) -> bool:
        return self.new_text != self.old_text


def _track_allows(toolchain: str, version: str, kinds_pins: dict[str, str] | None) -> bool:
    """kinds.toml pins each toolchain org-wide (python "3.14", node "24"); a roll stays inside it."""
    if kinds_pins is None or toolchain not in kinds_pins:
        return True
    pin = kinds_pins[toolchain]
    return version == pin or version.startswith(pin + ".")


def _image(source) -> tuple[str, str] | None:
    """(registry, repository) of an oci:// source, or None for any other source."""
    try:
        return oci_parts(source)[:2] if isinstance(source, str) else None
    except PinError:
        return None


def _numeric(version) -> tuple[int, ...] | None:
    """A dotted numeric version as a tuple, or None when it is missing or not numeric."""
    try:
        return tuple(map(int, version.split("."))) if isinstance(version, str) and version else None
    except ValueError:
        return None


def plan(text: str, promoted: list[Promoted], *, source: str = "infra/repo.toml",
         kinds_pins: dict[str, str] | None = None) -> Roll:
    """The roll for one manifest. Raises ManifestError if the manifest is invalid.

    Only toolchains the manifest already pins per platform, from the image the promotion names
    (registry and repository), are moved, to sync's oci:// form: source = the manifest by digest,
    digest = the toolchain layer. A promotion older than the manifest's version label is not rolled.
    A promotion outside the org-wide pin in infra-config kinds.toml (a new minor or major) is not rolled:
    moving that pin is a policy change for suraj.
    """
    manifest = Manifest(text, source)
    roll = Roll(old_text=text, new_text=text)
    pins = manifest.data.get("toolchains", {})
    by_key = {(p.name, p.platform): p for p in promoted}
    for name in sorted(pins):
        pin = pins[name]
        if "platforms" not in pin:
            if any(p.name == name for p in promoted):
                roll.skipped.append(f"{name}: pinned once for every platform, but toolchains promotes one "
                                    "image per platform; pin it per platform to have it rolled")
            continue
        platforms = pin["platforms"]
        rolls = []                   # (platform, current pin, promotion)
        version_at = {}              # platform -> promoted version its digest will be at, or None
        for platform in sorted(platforms):
            current = platforms[platform]
            new = by_key.get((name, platform))
            version_at[platform] = None
            if new is None:
                continue  # nothing promoted for this toolchain on this platform
            if _image(current.get("source")) != (new.registry, new.repository):
                roll.skipped.append(f"{name} ({platform}): source {current.get('source')!r} is not the image "
                                    f"oci://{new.registry}/{new.repository}; not a quirq-ai/toolchains pin")
                continue
            if current.get("source") == new.source and current["digest"] == new.digest:
                version_at[platform] = new.version
                continue
            # The pin's version label is the only record of what it is at, so without a numeric one a
            # downgrade cannot be ruled out. Revisions are not recorded in the manifest, so a revert to an
            # earlier revision of the same version still rolls; that is fine only while a person lands
            # every roll. TODO(expert): compare revisions before --auto-merge is ever turned on.
            have, want = _numeric(pin.get("version")), _numeric(new.version)
            if have is None or want is None:
                roll.skipped.append(f"{name} ({platform}): version label {pin.get('version')!r} or promoted "
                                    f"{new.version!r} is not a numeric version; cannot rule out a downgrade")
                continue
            if want < have:
                roll.skipped.append(f"{name} ({platform}): promoted {new.version} is older than the pinned "
                                    f"{pin.get('version')}; the roller does not downgrade")
                continue
            if not _track_allows(name, new.version, kinds_pins):
                roll.skipped.append(f"{name} ({platform}): promoted {new.version} is outside the kinds.toml "
                                    f"pin {kinds_pins[name]!r}; moving that pin is a policy change for suraj")
                continue
            rolls.append((platform, current, new))
            version_at[platform] = new.version
        if not rolls:
            continue
        # `version` labels the whole toolchain, so it moves only when every platform ends up at one version.
        versions = set(version_at.values())
        version = versions.pop() if len(versions) == 1 and None not in versions else None
        if version is None:
            roll.notes.append(f"{name}: version label left at {pin.get('version')!r}; its platforms are "
                              "not all at one promoted version")
        for platform, current, new in rolls:
            manifest.set_pin("toolchains", name, source=new.source, digest=new.digest, version=version,
                             platform=platform)
            roll.changes.append(Change(name, platform, current["source"], current["digest"], new))
    roll.new_text = manifest.dumps()
    return roll


__all__ = ["Change", "Roll", "plan", "ManifestError"]
