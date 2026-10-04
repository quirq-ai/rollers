import os
from pathlib import Path

import pytest

from qqroll import promoted, roll
from qqroll.cli import main
from promoted_support import PROMOTED, TOOLCHAINS, entry, needs_toolchains

FIX = Path(__file__).parent / "fixtures"
STALE = (FIX / "stale.repo.toml").read_text()
ROLLED = (FIX / "rolled.repo.toml").read_text()
NEW_PY = "sha256:230c6677ccbaba9043c810b9c4a6096ed354c8a981bb62ea90bd059b12d1b03c"   # the layer
NEW_PY_IMAGE = "oci://ghcr.io/quirq-ai/toolchains/python@sha256:32c0b762db5ec5453e63cf057ab4fa072751a19f1af70f08c07ac4f5fb70a716"
SYNC = Path(os.environ.get("QQ_SYNC", "/nonexistent")) / "tests" / "fixtures" / "xo-space.repo.toml"


def test_from_entry_is_sync_pin_form():
    py = PROMOTED[1]
    assert (py.name, py.version, py.revision, py.platform) == ("python", "3.14.8", 1, "linux-x86_64")
    assert py.source == NEW_PY_IMAGE and py.digest == NEW_PY
    assert (py.registry, py.repository) == ("ghcr.io", "quirq-ai/toolchains/python")


@pytest.mark.parametrize("changes, match", [
    ({"ref": "oci://ghcr.io/quirq-ai/toolchains/python:latest"}, "is not oci://"),
    ({"layer_sha256": "sha256:" + "a" * 64}, "layer_sha256"),
    ({"layer_sha256": "A" * 64}, "layer_sha256"),
    ({"built_from": "main"}, "built_from"),
    ({"build_run": "https://github.com/someone/toolchains/actions/runs/1"}, "build_run"),
    ({"build_run": ""}, "build_run"),
])
def test_from_entry_rejects(changes, match):
    with pytest.raises(promoted.PromotedError, match=match):
        promoted.from_entry(entry(**changes))


def test_load_needs_a_toolchains_checkout(tmp_path):
    with pytest.raises(promoted.PromotedError, match="not a quirq-ai/toolchains checkout"):
        promoted.load(tmp_path)


@needs_toolchains
def test_load_reads_through_qqtc():
    got = promoted.load(TOOLCHAINS)
    assert {p.name for p in got} >= {"python", "node"}
    assert all(p.source.startswith("oci://") and p.digest.startswith("sha256:") for p in got)


def test_roll_changes_only_the_stale_pin_lines():
    r = roll.plan(STALE, PROMOTED)
    assert r.new_text == ROLLED  # comments, order and the other pins are byte-identical
    assert [(c.toolchain, c.platform, c.new.digest) for c in r.changes] == [("python", "linux-x86_64", NEW_PY)]
    assert r.skipped == []


def test_roll_of_a_current_manifest_is_a_no_op():
    r = roll.plan(ROLLED, PROMOTED)
    assert not r.changed and r.changes == [] and r.new_text == ROLLED


def test_roll_keeps_crlf():
    r = roll.plan(STALE.replace("\n", "\r\n"), PROMOTED)
    assert r.new_text == ROLLED.replace("\n", "\r\n")


def test_roll_skips_pins_from_another_source():
    text = STALE.replace('source = "oci://ghcr.io/quirq-ai/toolchains/python@',
                         'source = "oci://ghcr.io/someone/python@')
    r = roll.plan(text, PROMOTED)
    assert not r.changed
    assert "not a quirq-ai/toolchains pin" in r.skipped[0]


def test_roll_matches_the_image_not_the_full_source():
    # Any earlier manifest of the same image is rolled, and so is a pin of the image with no manifest
    # (the form before sync c51e402): both end at the manifest and layer toolchains promoted.
    old = STALE.replace("@sha256:" + "1" * 64, "@sha256:" + "9" * 64)
    assert roll.plan(old, PROMOTED).new_text == ROLLED
    bare = STALE.replace("/python@sha256:" + "1" * 64, "/python")
    assert roll.plan(bare, PROMOTED).new_text == ROLLED


def test_roll_does_not_downgrade():
    newer = STALE.replace('version = "3.14.7"', 'version = "3.14.9"')
    r = roll.plan(newer, PROMOTED)
    assert not r.changed and "does not downgrade" in r.skipped[0]


def test_roll_moves_a_layer_only_change():
    # Same manifest digest in the source but a different layer: still not current.
    text = ROLLED.replace(NEW_PY, "sha256:" + "4" * 64)
    assert roll.plan(text, PROMOTED).new_text == ROLLED


@pytest.mark.skipif(not SYNC.is_file(), reason="set QQ_SYNC to a quirq-ai/sync checkout")
def test_sync_fixture_is_current():
    # sync's own xo-space fixture already pins the promoted python; the roller agrees it is current.
    r = roll.plan(SYNC.read_text(), PROMOTED)
    assert not r.changed and r.skipped == []


def test_roll_stays_inside_the_kinds_pin():
    assert roll.plan(STALE, PROMOTED, kinds_pins={"python": "3.14", "node": "24"}).changed
    r = roll.plan(STALE, PROMOTED, kinds_pins={"python": "3.13"})
    assert not r.changed
    assert "policy change for suraj" in r.skipped[0]
    # "3.1" must not admit 3.14
    assert not roll.plan(STALE, PROMOTED, kinds_pins={"python": "3.1"}).changed


def test_roll_skips_a_pin_for_every_platform():
    text = (
        'schema = "quirq-repo/1"\n[toolchains.python]\nversion = "3.14.7"\n'
        'source = "oci://ghcr.io/quirq-ai/toolchains/python@sha256:' + "1" * 64 + '"\n'
        'digest = "sha256:' + "2" * 64 + '"\n[[targets]]\nname = "a"\nkind = "k"\n')
    r = roll.plan(text, PROMOTED)
    assert not r.changed
    assert "pin it per platform" in r.skipped[0]


TWO_PLATFORMS = (
    'schema = "quirq-repo/1"\n[toolchains.python]\nversion = "3.14.7"\n'
    'platforms."linux-x86_64" = { source = "oci://ghcr.io/quirq-ai/toolchains/python@sha256:' + "1" * 64
    + '", digest = "sha256:' + "1" * 64 + '" }\n'
    'platforms."macos-arm64" = { source = "oci://ghcr.io/quirq-ai/toolchains/python@sha256:' + "2" * 64
    + '", digest = "sha256:' + "2" * 64 + '" }\n[[targets]]\nname = "a"\nkind = "k"\n')


def test_version_label_stays_when_platforms_would_disagree():
    r = roll.plan(TWO_PLATFORMS, PROMOTED)  # only linux-x86_64 is promoted
    assert [c.platform for c in r.changes] == ["linux-x86_64"]
    assert 'version = "3.14.7"' in r.new_text and NEW_PY in r.new_text
    assert "version label left at '3.14.7'" in r.skipped[0]


def test_version_label_moves_when_every_platform_reaches_it():
    mac = promoted.from_entry(entry(platform="macos-arm64", layer_sha256="3" * 64))
    r = roll.plan(TWO_PLATFORMS, PROMOTED + [mac])
    assert [c.platform for c in r.changes] == ["linux-x86_64", "macos-arm64"]
    assert 'version = "3.14.8"' in r.new_text and r.skipped == []


def test_roll_does_not_add_toolchains_the_manifest_lacks():
    text = STALE.split("[toolchains.node]")[0] + '[[targets]]\nname = "a"\nkind = "k"\n'
    r = roll.plan(text, PROMOTED)
    assert "toolchains.node" not in r.new_text


def test_roll_refuses_an_invalid_manifest():
    with pytest.raises(roll.ManifestError):
        roll.plan('schema = "quirq-repo/1"\n', PROMOTED)


@needs_toolchains
def test_cli_roll_edits_in_place(tmp_path, capsys):
    m = tmp_path / "repo.toml"
    m.write_bytes(STALE.encode())
    args = ["roll", "--toolchains", TOOLCHAINS, "--manifest", str(m)]
    assert main(args + ["--dry-run"]) == 0
    assert m.read_text() == STALE
    assert main(args) == 0
    assert m.read_text() == ROLLED
    assert main(args) == 0
    assert "toolchain pins are current" in capsys.readouterr().out


@needs_toolchains
def test_cli_roll_fails_on_a_skipped_pin(tmp_path, capsys):
    m = tmp_path / "repo.toml"
    m.write_bytes(STALE.replace("toolchains/python@", "someone/python@").encode())
    assert main(["roll", "--toolchains", TOOLCHAINS, "--manifest", str(m)]) == 1
    assert "not rolled (1 skipped)" in capsys.readouterr().out
