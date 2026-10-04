import subprocess
from pathlib import Path

import pytest
import yaml

from qqroll import dependabot
from qqroll.cli import main
from qqroll.config import ConfigError, load_rollers

COMMIT = "0" * 40

ROLLERS = [
    {"name": "python-deps", "repos": ["xo-space"], "moves": "Python requirements", "tool": "dependabot",
     "ecosystem": "pip", "directory": "/", "open_limit": 5, "cadence": "weekly"},
    {"name": "node-deps", "repos": ["innernet"], "moves": "pnpm-lock.yaml", "tool": "dependabot",
     "ecosystem": "npm", "directory": "/", "open_limit": 3, "cadence": "daily"},
    {"name": "toolchains", "repos": ["xo-space", "innernet"], "moves": "toolchain pins",
     "tool": "quirq-rollers", "cadence": "weekly"},
]


def test_dependabot_yml_follows_rollers_toml():
    doc = yaml.safe_load(dependabot.dependabot_yml(ROLLERS, "innernet", COMMIT))
    assert doc == {"version": 2, "updates": [{
        "package-ecosystem": "npm", "directory": "/", "schedule": {"interval": "daily"},
        "open-pull-requests-limit": 3, "commit-message": {"prefix": "roll"}}]}


def test_one_file_pair_per_dependabot_repo():
    files = dependabot.generate(ROLLERS, COMMIT)
    assert sorted(files) == [
        "github/innernet/.github/dependabot.yml",
        "github/innernet/.github/workflows/qq-roll-land.yml",
        "github/xo-space/.github/dependabot.yml",
        "github/xo-space/.github/workflows/qq-roll-land.yml",
    ]
    for text in files.values():
        assert COMMIT in text.splitlines()[2]  # header records the infra-config commit


def test_land_workflow_is_valid_and_scoped():
    text = dependabot.land_workflow(ROLLERS, "xo-space", COMMIT)
    wf = yaml.safe_load(text)
    job = wf["jobs"]["land"]
    assert "dependabot[bot]" in job["if"] and "'quirq-ai/xo-space'" in job["if"]
    assert wf["permissions"] == {"contents": "write", "pull-requests": "write"}
    run = job["steps"][1]["run"]
    assert "allowed=('pyproject.toml' 'requirements*.txt')" in run
    land = job["steps"][2]
    assert "semver-major" in land["if"] and land["run"].startswith('gh pr merge --auto --squash "$PR_URL"')
    assert job["steps"][3]["run"].startswith('gh pr merge --disable-auto "$PR_URL"')


def test_land_workflow_clean_check_accepts_only_dependency_files(tmp_path):
    """Run the generated shell check against file lists, with `gh` faked."""
    text = dependabot.land_workflow(ROLLERS, "innernet", COMMIT)
    script = yaml.safe_load(text)["jobs"]["land"]["steps"][1]["run"]
    bindir = tmp_path / "bin"
    bindir.mkdir()

    def clean(files, actor="dependabot[bot]"):
        (bindir / "gh").write_text("#!/bin/sh\nprintf '%s\\n' " + " ".join(f"'{f}'" for f in files) + "\n")
        (bindir / "gh").chmod(0o755)
        out = tmp_path / "out"
        out.write_text("")
        subprocess.run(["bash", "-c", script], check=True,
                       env={"PATH": f"{bindir}:/usr/bin:/bin", "GITHUB_OUTPUT": str(out),
                            "GITHUB_REPOSITORY": "quirq-ai/innernet", "PR": "1", "ACTOR": actor})
        return out.read_text().strip()

    assert clean(["pnpm-lock.yaml", "package.json"]) == "clean=true"
    assert clean(["pnpm-lock.yaml", "scripts/postinstall.js"]) == "clean=false"
    assert clean(["app/package.json"]) == "clean=false"
    assert clean(["pnpm-lock.yaml"], actor="someone") == "clean=false"


def test_nested_directory_prefixes_patterns():
    rollers = [dict(ROLLERS[1], directory="/web")]
    assert dependabot._patterns(rollers, "innernet") == ["web/package.json", "web/pnpm-lock.yaml"]


def test_unsupported_cadence_is_an_error():
    with pytest.raises(dependabot.GenerateError, match="biweekly"):
        dependabot.generate([dict(ROLLERS[0], cadence="biweekly")], COMMIT)


def test_repo_without_dependabot_roller_is_an_error():
    with pytest.raises(dependabot.GenerateError, match="wiki"):
        dependabot.dependabot_yml(ROLLERS, "wiki", COMMIT)


def test_check_reports_missing_stale_and_extra(tmp_path):
    files = dependabot.generate(ROLLERS, COMMIT)
    assert len(dependabot.check(tmp_path, files)) == len(files)
    dependabot.write(tmp_path, files)
    assert dependabot.check(tmp_path, files) == []
    stale = tmp_path / "github/xo-space/.github/dependabot.yml"
    stale.write_text(stale.read_text() + "# hand edit\n")
    (tmp_path / "github/extra.yml").write_text("x")
    problems = dependabot.check(tmp_path, files)
    assert [p.split(": ")[1].split(";")[0] for p in problems] == ["stale", "not generated from rollers.toml"]


def fake_infra_config(root: Path, rollers_toml: str) -> Path:
    (root / "tools").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "tools" / "qqcfg.py").write_text(
        "import tomllib\nclass ConfigError(Exception): pass\n"
        "def load(root):\n"
        "    return {p.stem: tomllib.loads(p.read_text()) for p in (root / 'config').glob('*.toml')}\n")
    (root / "config" / "rollers.toml").write_text(rollers_toml)
    return root


ROLLERS_TOML = """
[[roller]]
name = "python-deps"
repos = ["xo-space"]
moves = "Python requirements"
tool = "dependabot"
ecosystem = "pip"
directory = "/"
open_limit = 5
cadence = "weekly"
"""


def test_load_rollers_uses_infra_configs_loader(tmp_path):
    rollers = load_rollers(fake_infra_config(tmp_path, ROLLERS_TOML))
    assert [r["name"] for r in rollers] == ["python-deps"]


def test_load_rollers_rejects_non_checkout(tmp_path):
    with pytest.raises(ConfigError, match="not an infra-config checkout"):
        load_rollers(tmp_path)


def test_cli_write_then_check(tmp_path, capsys):
    ic = fake_infra_config(tmp_path / "ic", ROLLERS_TOML)
    out = tmp_path / "gen"
    args = ["dependabot", "--infra-config", str(ic), "--commit", COMMIT, "--out", str(out)]
    assert main(args) == 1  # nothing written yet
    assert main(args + ["--write"]) == 0
    assert main(args) == 0
    assert "PASS" in capsys.readouterr().out
