from pathlib import Path

import pytest

from qqroll import promoted, roll
from qqroll.cli import main

FIX = Path(__file__).parent / "fixtures"
PROMOTED = promoted.parse((FIX / "promoted.toml").read_text())
STALE = (FIX / "stale.repo.toml").read_text()
ROLLED = (FIX / "rolled.repo.toml").read_text()
NEW_PY = "sha256:32c0b762db5ec5453e63cf057ab4fa072751a19f1af70f08c07ac4f5fb70a716"


def test_parse_promoted():
    assert [(p.name, p.version, p.revision, p.platform, p.source) for p in PROMOTED] == [
        ("node", "24.21.0", 1, "linux-x86_64", "oci://ghcr.io/quirq-ai/toolchains/node"),
        ("python", "3.14.8", 1, "linux-x86_64", "oci://ghcr.io/quirq-ai/toolchains/python"),
    ]
    assert PROMOTED[1].digest == NEW_PY


@pytest.mark.parametrize("text, match", [
    ('schema = "other/1"', "schema"),
    ('schema = "quirq-toolchains-promoted/1"\n[[toolchain]]\nname = "x"', "missing 'version'"),
    ('schema = "quirq-toolchains-promoted/1"\n[[toolchain]]\nname = "x"\nversion = "1"\nrevision = 1\n'
     'platform = "p"\nref = "oci://x:latest"', "is not oci://"),
    ("[[[", "not valid TOML"),
])
def test_parse_rejects(text, match):
    with pytest.raises(promoted.PromotedError, match=match):
        promoted.parse(text)


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
    text = STALE.replace('source = "oci://ghcr.io/quirq-ai/toolchains/python"',
                         'source = "https://example.org/python.tar"')
    r = roll.plan(text, PROMOTED)
    assert not r.changed
    assert "not a quirq-ai/toolchains pin" in r.skipped[0]


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
        'source = "oci://ghcr.io/quirq-ai/toolchains/python"\n'
        'digest = "sha256:' + "1" * 64 + '"\n[[targets]]\nname = "a"\nkind = "k"\n')
    r = roll.plan(text, PROMOTED)
    assert not r.changed
    assert "pin it per platform" in r.skipped[0]


TWO_PLATFORMS = (
    'schema = "quirq-repo/1"\n[toolchains.python]\nversion = "3.14.7"\n'
    'platforms."linux-x86_64" = { source = "oci://ghcr.io/quirq-ai/toolchains/python", digest = "sha256:'
    + "1" * 64 + '" }\n'
    'platforms."macos-arm64" = { source = "oci://ghcr.io/quirq-ai/toolchains/python", digest = "sha256:'
    + "2" * 64 + '" }\n[[targets]]\nname = "a"\nkind = "k"\n')


def test_version_label_stays_when_platforms_would_disagree():
    r = roll.plan(TWO_PLATFORMS, PROMOTED)  # only linux-x86_64 is promoted
    assert [c.platform for c in r.changes] == ["linux-x86_64"]
    assert 'version = "3.14.7"' in r.new_text and NEW_PY in r.new_text
    assert "version label left at '3.14.7'" in r.skipped[0]


def test_version_label_moves_when_every_platform_reaches_it():
    mac = promoted.Promoted("python", "3.14.8", 1, "macos-arm64", "oci://ghcr.io/quirq-ai/toolchains/python",
                            "sha256:" + "3" * 64)
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


def test_cli_roll_edits_in_place(tmp_path, capsys):
    m = tmp_path / "repo.toml"
    m.write_bytes(STALE.encode())
    args = ["roll", "--promoted", str(FIX / "promoted.toml"), "--manifest", str(m)]
    assert main(args + ["--dry-run"]) == 0
    assert m.read_text() == STALE
    assert main(args) == 0
    assert m.read_text() == ROLLED
    assert main(args) == 0
    assert "toolchain pins are current" in capsys.readouterr().out
