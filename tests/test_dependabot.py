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
        assert COMMIT in text.splitlines()[1]  # header records the infra-config commit


def test_land_workflow_is_valid_and_scoped():
    text = dependabot.land_workflow(ROLLERS, "xo-space", COMMIT)
    wf = yaml.safe_load(text)
    job = wf["jobs"]["land"]
    assert "dependabot[bot]" in job["if"] and "'quirq-ai/xo-space'" in job["if"]
    assert wf["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert job["steps"][0]["uses"] == "dependabot/fetch-metadata@25dd0e34f4fe68f24cc83900b1fe3fe149efef98"
    run = job["steps"][1]["run"]
    assert """allowed=('"pyproject.toml"' '"requirements*.txt"')""" in run
    land = job["steps"][2]
    assert "semver-major" in land["if"] and land["run"].startswith('gh pr merge --auto --squash "$PR_URL"')
    # Any failure before landing, or a skipped land, turns auto-merge off.
    assert job["steps"][3]["if"] == "always() && steps.land.outcome != 'success'"
    assert job["steps"][3]["run"].startswith('gh pr merge --disable-auto "$PR_URL"')


def test_land_workflow_uses_the_repos_slug():
    text = dependabot.land_workflow(ROLLERS, "xo-space", COMMIT, "someone/xo-space")
    assert "'someone/xo-space'" in yaml.safe_load(text)["jobs"]["land"]["if"]


def _clean_check(tmp_path, repo):
    """The generated shell check, runnable against fake `gh api` output."""
    text = dependabot.land_workflow(ROLLERS, repo, COMMIT)
    script = yaml.safe_load(text)["jobs"]["land"]["steps"][1]["run"]
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)

    def clean(files, bad_commits=(), gh_fails=False):
        import json as _json
        enc = "\n".join(_json.dumps(f) for f in files)
        (tmp_path / "files").write_text(enc + ("\n" if enc else ""))
        (tmp_path / "commits").write_text("".join(c + "\n" for c in bad_commits))
        (bindir / "gh").write_text(
            "#!/bin/sh\n" + ("exit 1\n" if gh_fails else "") +
            f'case "$*" in *files*) cat {tmp_path}/files;; *commits*) cat {tmp_path}/commits;; esac\n')
        (bindir / "gh").chmod(0o755)
        out = tmp_path / "out"
        out.write_text("")
        proc = subprocess.run(["bash", "-e", "-c", script],
                              env={"PATH": f"{bindir}:/usr/bin:/bin", "GITHUB_OUTPUT": str(out),
                                   "GITHUB_REPOSITORY": f"quirq-ai/{repo}", "PR": "1"})
        return out.read_text().strip() if proc.returncode == 0 else f"failed {proc.returncode}"
    return clean


def test_clean_check_accepts_only_dependency_files(tmp_path):
    clean = _clean_check(tmp_path, "innernet")
    assert clean(["pnpm-lock.yaml", "package.json"]) == "clean=true"
    assert clean(["pnpm-lock.yaml", "scripts/postinstall.js"]) == "clean=false"
    assert clean(["app/package.json"]) == "clean=false"
    assert clean(["package.json\npnpm-lock.yaml"]) == "clean=false"  # one name with a newline
    assert clean(['package.json"']) == "clean=false"


def test_clean_check_globs_stay_in_their_directory(tmp_path):
    clean = _clean_check(tmp_path, "xo-space")
    assert clean(["requirements.txt", "requirements-dev.txt"]) == "clean=true"
    assert clean(["requirements/x.txt"]) == "clean=false"
    assert clean(["requirementsfoo/.github/workflows/a.txt"]) == "clean=false"


def test_clean_check_needs_only_dependabot_commits(tmp_path):
    clean = _clean_check(tmp_path, "innernet")
    assert clean(["pnpm-lock.yaml"], bad_commits=["abc123"]) == "clean=false"


def test_clean_check_fails_closed(tmp_path):
    clean = _clean_check(tmp_path, "innernet")
    assert clean([]) == "clean=false"                       # nothing listed
    assert clean(["pnpm-lock.yaml"], gh_fails=True).startswith("failed")  # API error fails the step


def test_nested_directory_prefixes_patterns():
    rollers = [dict(ROLLERS[1], directory="/web")]
    assert dependabot._patterns(rollers, "innernet") == [
        "web/package.json", "web/pnpm-lock.yaml", "web/pnpm-workspace.yaml"]


def test_unknown_backend_is_an_error():
    with pytest.raises(dependabot.GenerateError, match="launchpad"):
        dependabot.generate(ROLLERS, COMMIT, backend="launchpad")


def test_github_slugs_come_from_repos_toml():
    cfg = {"repos": {"repo": [{"name": "xo-space", "source": "github.com/quirq-ai/xo-space"},
                              {"name": "elsewhere", "source": "example.org/x"}]}}
    assert dependabot.github_slugs(cfg) == {"xo-space": "quirq-ai/xo-space"}


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
    dependabot.write(tmp_path, files)  # rewrites the stale file and removes the extra one
    assert dependabot.check(tmp_path, files) == []


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
