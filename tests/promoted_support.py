"""Promotions for tests, built from the fixture with `promoted.from_entry`.

The roller itself reads promoted.toml only through quirq-ai/toolchains' own parser (`promoted.load`);
tests that need that run against a toolchains checkout named by QQ_TOOLCHAINS.
"""
import hashlib
import json
import os
import tomllib
from pathlib import Path

import pytest

from qqroll import promoted

FIX = Path(__file__).parent / "fixtures"
with open(FIX / "promoted.toml", "rb") as f:
    ENTRIES = tomllib.load(f)["toolchain"]
PROMOTED = [promoted.from_entry(e) for e in ENTRIES]

TOOLCHAINS = os.environ.get("QQ_TOOLCHAINS")
needs_toolchains = pytest.mark.skipif(not TOOLCHAINS, reason="set QQ_TOOLCHAINS to a quirq-ai/toolchains checkout")


def entry(**changes) -> dict:
    """The python fixture entry with some fields changed."""
    return dict(ENTRIES[1], **changes)


def image(layer: str, artifact="application/vnd.quirq.toolchain.v1",
          media="application/vnd.quirq.toolchain.layer.v1.tar+gzip", layers=1) -> bytes:
    """The raw bytes of a toolchain image manifest."""
    return json.dumps({"schemaVersion": 2, "artifactType": artifact,
                       "layers": [{"digest": layer, "mediaType": media}] * layers}).encode()


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()
