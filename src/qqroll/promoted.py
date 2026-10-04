"""The toolchains quirq-ai/toolchains promoted: the pins the toolchain roller moves repos to.

promoted.toml is written by `qqtc promote` in a reviewed PR in quirq-ai/toolchains (V0-TCH-03). It
is read only through that repo's own parser: `qqtc promoted-changed` (with no base) prints every
entry as JSON, so this repo never has a second reading of the format. qqtc always runs as its own
process, isolated and with no credentials in its environment; in CI it runs in a separate job from
the one that holds the bot token. Its JSON is data, checked again here entry by entry.

A promotion becomes a pin in sync's `oci://` form (sync c51e402): the source names the image
manifest by digest and the digest pins the one toolchain layer.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

TOOLCHAINS_SLUG = "quirq-ai/toolchains"
BUILD_RUN = re.compile(r"^https://github\.com/" + re.escape(TOOLCHAINS_SLUG) + r"/actions/runs/[0-9]+$")
_REF = re.compile(r"^oci://(?P<registry>[^/@\s]+)/(?P<repository>[^@\s]+)@(?P<manifest>sha256:[0-9a-f]{64})$")
_LAYER = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_VERSION = re.compile(r"^[0-9][0-9A-Za-z.+-]*$")


class PromotedError(Exception):
    pass


@dataclass(frozen=True)
class Promoted:
    name: str
    version: str
    revision: int
    platform: str
    registry: str
    repository: str
    manifest: str      # sha256:<64 hex>, the image manifest digest
    layer: str         # sha256:<64 hex>, the one toolchain layer
    built_from: str    # the toolchains commit build.yml ran on
    build_run: str = ""

    @property
    def source(self) -> str:
        """The pin's source in sync's oci:// form: the manifest, by digest."""
        return f"oci://{self.registry}/{self.repository}@{self.manifest}"

    @property
    def digest(self) -> str:
        """The pin's digest: the layer, which is what gets fetched and unpacked."""
        return self.layer

    @property
    def label(self) -> str:
        return f"{self.name} {self.version}-r{self.revision} ({self.platform})"


def from_entry(entry: dict, where: str = "promoted.toml") -> Promoted:
    """One promoted.toml entry, as qqtc.load_promoted returns it, checked again here."""
    for key, pattern in (("name", _NAME), ("version", _VERSION), ("platform", _NAME)):
        if not isinstance(entry.get(key), str) or not pattern.match(entry[key]):
            raise PromotedError(f"{where}: {entry.get('name')!r}: {key} {entry.get(key)!r} is not valid")
    revision = entry.get("revision")
    if type(revision) is not int or revision < 1:
        raise PromotedError(f"{where}: {entry['name']}: revision {revision!r} is not a positive integer")
    for key in ("ref", "layer_sha256", "built_from", "build_run"):
        if not isinstance(entry.get(key), str):
            raise PromotedError(f"{where}: {entry['name']}: {key} is not a string")
    m = _REF.match(entry["ref"])
    if not m:
        raise PromotedError(f"{where}: {entry.get('name')}: ref {entry.get('ref')!r} is not oci://<image>@sha256:<64 hex>")
    if not _LAYER.match(str(entry.get("layer_sha256", ""))):
        raise PromotedError(f"{where}: {entry.get('name')}: layer_sha256 is not 64 lowercase hex characters")
    if not _COMMIT.match(str(entry.get("built_from", ""))):
        raise PromotedError(f"{where}: {entry.get('name')}: built_from is not a full commit")
    run = str(entry.get("build_run", ""))
    if not BUILD_RUN.match(run):
        raise PromotedError(f"{where}: {entry.get('name')}: build_run {run!r} is not a {TOOLCHAINS_SLUG} Actions run")
    return Promoted(entry["name"], entry["version"], revision, entry["platform"],
                    m["registry"], m["repository"], m["manifest"], "sha256:" + entry["layer_sha256"],
                    entry["built_from"], run)


def parse_json(text: str, where: str = "qqtc promoted-changed") -> list[Promoted]:
    """Promotions from the JSON `qqtc promoted-changed` prints: a list of promoted.toml entries."""
    try:
        entries = json.loads(text)
    except ValueError as e:
        raise PromotedError(f"{where}: not JSON: {e}") from None
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise PromotedError(f"{where}: not a list of promoted.toml entries")
    try:
        out = [from_entry(e, where) for e in entries]
    except (KeyError, TypeError, ValueError) as e:
        raise PromotedError(f"{where}: malformed entry: {e}") from None
    keys = [(p.name, p.platform) for p in out]
    if len(set(keys)) != len(keys):
        raise PromotedError(f"{where}: a toolchain is promoted twice for one platform")
    return out


def load(toolchains: str | Path) -> list[Promoted]:
    """Every promotion in a quirq-ai/toolchains checkout, read by its own qqtc in its own process.

    qqtc gets no credentials: only PATH reaches its environment, and Python runs isolated (-I).
    """
    root = Path(toolchains).resolve()
    tool = root / "tools" / "qqtc.py"
    if not tool.is_file():
        raise PromotedError(f"{root}: not a quirq-ai/toolchains checkout (no tools/qqtc.py)")
    try:
        proc = subprocess.run([sys.executable, "-I", str(tool), "promoted-changed"], cwd=root,
                              capture_output=True, text=True, timeout=120,
                              env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PromotedError(f"{tool}: {e}") from None
    if proc.returncode != 0:
        raise PromotedError(f"{tool} promoted-changed failed: {proc.stderr.strip() or f'exit {proc.returncode}'}")
    return parse_json(proc.stdout, f"{tool} promoted-changed")
