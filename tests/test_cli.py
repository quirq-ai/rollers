import pytest

from qqroll import __version__
from qqroll.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"qqroll {__version__}"


def test_no_command_prints_help(capsys):
    assert main([]) == 2
    assert "usage: qqroll" in capsys.readouterr().out


def test_every_action_is_pinned_by_sha():
    """Audit S-5: workflows run only actions pinned to a full commit."""
    import re
    from pathlib import Path

    root = Path(__file__).parent.parent
    files = list((root / ".github" / "workflows").glob("*.yml")) + list((root / "generated").rglob("*.yml"))
    uses = [(f.name, m[1]) for f in files for m in re.finditer(r"^\s*(?:-\s*)?uses:\s*(\S+)", f.read_text(), re.M)]
    assert uses
    assert [u for u in uses if not re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", u[1])] == []


def test_repos_lists_the_toolchains_roller(monkeypatch, capsys):
    from qqroll import cli
    monkeypatch.setattr("qqroll.config.load", lambda path: {})
    monkeypatch.setattr("qqroll.config.rollers", lambda cfg: [
        {"name": "toolchains", "tool": "quirq-rollers", "repos": ["xo-space", "innernet"]},
        {"name": "lockfiles", "tool": "dependabot", "repos": ["other"]}])
    assert cli.main(["repos", "--infra-config", "x"]) == 0
    assert capsys.readouterr().out.strip() == "innernet,xo-space"


def test_qqsync_pin_matches_pyproject():
    import tomllib
    from pathlib import Path

    root = Path(__file__).parent.parent
    with open(root / "pyproject.toml", "rb") as f:
        deps = tomllib.load(f)["project"]["dependencies"]
    lines = [ln for ln in (root / "requirements" / "qqsync.txt").read_text().splitlines()
             if ln and not ln.startswith("#")]
    assert lines == deps
