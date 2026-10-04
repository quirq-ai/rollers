"""Read quirq-ai/toolchains' promoted.toml: the digests the toolchain roller moves repos to.

promoted.toml is written by `qqtc promote` in a reviewed PR in quirq-ai/toolchains (V0-TCH-03). It
is that repo's data file, not a repo manifest, so it is read here directly.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass

SCHEMA = "quirq-toolchains-promoted/1"
REF = re.compile(r"^(?P<source>oci://[^@\s]+)@(?P<digest>sha256:[0-9a-f]{64})$")


class PromotedError(Exception):
    pass


@dataclass(frozen=True)
class Promoted:
    name: str
    version: str
    revision: int
    platform: str
    source: str        # oci://ghcr.io/quirq-ai/toolchains/<name>
    digest: str        # sha256:<64 hex>, the image manifest digest
    build_run: str = ""

    @property
    def label(self) -> str:
        return f"{self.name} {self.version}-r{self.revision} ({self.platform})"


def parse(text: str, source: str = "promoted.toml") -> list[Promoted]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise PromotedError(f"{source}: not valid TOML: {e}") from None
    if data.get("schema") != SCHEMA:
        raise PromotedError(f"{source}: schema is {data.get('schema')!r}, expected {SCHEMA!r}")
    out, seen = [], set()
    for i, t in enumerate(data.get("toolchain", [])):
        where = f"{source}: toolchain[{i}]"
        try:
            name, version, revision, platform, ref = (t["name"], t["version"], t["revision"],
                                                      t["platform"], t["ref"])
        except KeyError as e:
            raise PromotedError(f"{where}: missing {e.args[0]!r}") from None
        m = REF.match(ref)
        if not m:
            raise PromotedError(f"{where}: ref {ref!r} is not oci://<image>@sha256:<64 hex>")
        if (name, platform) in seen:
            raise PromotedError(f"{where}: {name} on {platform} is promoted twice")
        seen.add((name, platform))
        out.append(Promoted(name, str(version), int(revision), platform, m["source"], m["digest"],
                            t.get("build_run", "")))
    return out
