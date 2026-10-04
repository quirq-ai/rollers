"""The toolchains quirq-ai/toolchains promoted: the pins the toolchain roller moves repos to.

promoted.toml is written by `qqtc promote` in a reviewed PR in quirq-ai/toolchains (V0-TCH-03). It
is read only through that repo's own parser, `qqtc.load_promoted`, from a checkout of toolchains
`main`, so this repo never has a second reading of the format.

A promotion becomes a pin in sync's `oci://` form (sync c51e402): the source names the image
manifest by digest and the digest pins the one toolchain layer.
"""
from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path

TOOLCHAINS_SLUG = "quirq-ai/toolchains"
BUILD_RUN = re.compile(r"^https://github\.com/" + re.escape(TOOLCHAINS_SLUG) + r"/actions/runs/[0-9]+$")
_REF = re.compile(r"^oci://(?P<registry>[^/@\s]+)/(?P<repository>[^@\s]+)@(?P<manifest>sha256:[0-9a-f]{64})$")
_LAYER = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


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
    m = _REF.match(str(entry.get("ref", "")))
    if not m:
        raise PromotedError(f"{where}: {entry.get('name')}: ref {entry.get('ref')!r} is not oci://<image>@sha256:<64 hex>")
    if not _LAYER.match(str(entry.get("layer_sha256", ""))):
        raise PromotedError(f"{where}: {entry.get('name')}: layer_sha256 is not 64 lowercase hex characters")
    if not _COMMIT.match(str(entry.get("built_from", ""))):
        raise PromotedError(f"{where}: {entry.get('name')}: built_from is not a full commit")
    run = str(entry.get("build_run", ""))
    if not BUILD_RUN.match(run):
        raise PromotedError(f"{where}: {entry.get('name')}: build_run {run!r} is not a {TOOLCHAINS_SLUG} Actions run")
    return Promoted(entry["name"], str(entry["version"]), int(entry["revision"]), entry["platform"],
                    m["registry"], m["repository"], m["manifest"], "sha256:" + entry["layer_sha256"],
                    entry["built_from"], run)


def load(toolchains: str | Path) -> list[Promoted]:
    """Every promotion in a quirq-ai/toolchains checkout, read with its own `qqtc.load_promoted`."""
    root = Path(toolchains)
    tool = root / "tools" / "qqtc.py"
    if not tool.is_file():
        raise PromotedError(f"{root}: not a quirq-ai/toolchains checkout (no tools/qqtc.py)")
    spec = importlib.util.spec_from_file_location("qqtc", tool)
    qqtc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qqtc)
    try:
        cfg = qqtc.load_repo_config(root / "toolchains.toml")
        entries = qqtc.load_promoted(root / "promoted.toml", cfg, root / "toolchains")
    except qqtc.SpecError as e:
        raise PromotedError(str(e)) from None
    return [from_entry(e) for _, e in sorted(entries.items())]
