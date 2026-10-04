import json
import subprocess
from pathlib import Path

import pytest
import yaml

from qqroll import dependabot
from qqroll.cli import main
from qqroll.config import ConfigError, load_rollers

COMMIT = "0" * 40
SLUGS = {"xo-space": "quirq-ai/xo-space", "innernet": "quirq-ai/innernet"}

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
    files = dependabot.generate(ROLLERS, COMMIT, slugs=SLUGS)
    assert sorted(files) == [
        "github/innernet/.github/dependabot.yml",
        "github/innernet/.github/workflows/qq-roll-land.yml",
        "github/xo-space/.github/dependabot.yml",
        "github/xo-space/.github/workflows/qq-roll-land.yml",
    ]
    for text in files.values():
        assert COMMIT in text.splitlines()[1]  # header records the infra-config commit


def test_land_workflow_is_valid_and_scoped():
    text = dependabot.land_workflow(ROLLERS, "xo-space", COMMIT, "quirq-ai/xo-space")
    wf = yaml.safe_load(text)
    job = wf["jobs"]["land"]
    assert "dependabot[bot]" in job["if"] and "'quirq-ai/xo-space'" in job["if"]
    assert wf["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert job["steps"][0]["uses"] == "dependabot/fetch-metadata@25dd0e34f4fe68f24cc83900b1fe3fe149efef98"
    check = job["steps"][1]
    assert json.loads(check["env"]["QQ_ALLOWED"]) == [["requirements*.txt", "pip-requirements"]]
    assert check["env"]["SENDER"] == "${{ github.event.sender.login }}"
    # The embedded check is land_check.py, byte for byte.
    embedded = check["run"].split("<<'QQ_LAND_CHECK'\n", 1)[1].rsplit("QQ_LAND_CHECK", 1)[0]
    assert embedded == dependabot.LAND_CHECK.read_text()
    land = job["steps"][2]
    assert land["if"] == "steps.clean.outputs.clean == 'true'"
    assert land["run"].startswith('gh pr merge --auto --squash --match-head-commit "$HEAD_SHA" "$PR_URL"')
    # Any failure before landing, or a skipped land, turns auto-merge off.
    assert job["steps"][3]["if"] == "always() && steps.land.outcome != 'success'"
    assert job["steps"][3]["run"].startswith('gh pr merge --disable-auto "$PR_URL"')


def test_land_workflow_uses_the_repos_slug():
    text = dependabot.land_workflow(ROLLERS, "xo-space", COMMIT, "someone/xo-space")
    assert "'someone/xo-space'" in yaml.safe_load(text)["jobs"]["land"]["if"]


def test_generated_step_runs_the_check(tmp_path):
    """Run the generated step's shell with `gh` faked: a clean roll, then a forged one."""
    import json as _json
    import sys
    from tests_support import CLEAN_COMMIT, HEAD_RULES, LOCK_BASE, LOCK_HEAD, LOCK_PATCH, REGISTRY, RULES, npm_file, registry
    text = dependabot.land_workflow(ROLLERS, "innernet", COMMIT, "quirq-ai/innernet")
    step = yaml.safe_load(text)["jobs"]["land"]["steps"][1]
    bindir = tmp_path / "bin"
    bindir.mkdir()

    (tmp_path / "base.lock").write_text(LOCK_BASE)
    # python3 runs the embedded check with registry.npmjs.org answered from the fake registry.
    (tmp_path / "registry.json").write_text(_json.dumps(
        {f"https://registry.npmjs.org/{n}/{v}": registry(n, v) for n, v in REGISTRY}))
    (bindir / "python3").write_text(
        f"#!{sys.executable}\n"
        "import io, json, sys, urllib.request\n"
        f"answers = json.load(open({str(tmp_path / 'registry.json')!r}))\n"
        "class Answer(io.BytesIO):\n"
        "    def __init__(self, url):\n"
        "        super().__init__(json.dumps(answers[url]).encode())\n"
        "        self.url = url\n"
        "    def geturl(self):\n"
        "        return self.url\n"
        "class Opener:\n"
        "    def open(self, req, timeout=None):\n"
        "        return Answer(req.full_url.replace('%40', '@'))\n"
        "urllib.request.build_opener = lambda *handlers: Opener()\n"
        "exec(compile(sys.stdin.read(), 'land_check', 'exec'), {'__name__': '__main__'})\n")
    (bindir / "python3").chmod(0o755)

    def run(commits, head_rules=HEAD_RULES, head_lock=LOCK_HEAD):
        (tmp_path / "head_rules").write_text(_json.dumps([head_rules]))
        (tmp_path / "head.lock").write_text(head_lock)
        (tmp_path / "files").write_text(_json.dumps([[npm_file(), npm_file("pnpm-lock.yaml", LOCK_PATCH)]]))
        (tmp_path / "commits").write_text(_json.dumps([commits]))
        (tmp_path / "rules").write_text(_json.dumps([RULES]))
        (bindir / "gh").write_text(
            "#!/bin/sh\n"
            f'case "$*" in *pulls/1/files*) cat {tmp_path}/files;; *pulls/1/commits*) cat {tmp_path}/commits;; '
            f'*compare/{"a" * 40}...{"c" * 40}*) echo {"b" * 40};; '
            f'*contents/pnpm-lock.yaml?ref={"b" * 40}) cat {tmp_path}/base.lock;; '
            f'*contents/pnpm-lock.yaml?ref={"c" * 40}) cat {tmp_path}/head.lock;; '
            f'*rules/branches/main) cat {tmp_path}/rules;; '
            f'*rules/branches/dependabot%2Fnpm_and_yarn%2Fnext-16.3.8) cat {tmp_path}/head_rules;; *) exit 1;; esac\n')
        (bindir / "gh").chmod(0o755)
        out = tmp_path / "out"
        out.write_text("")
        env = {"PATH": f"{bindir}:/usr/bin:/bin", "GITHUB_OUTPUT": str(out), "GITHUB_REPOSITORY": "quirq-ai/innernet",
               "PR": "1", "BASE": "main", "BASE_SHA": "a" * 40, "HEAD_SHA": "c" * 40, "HEAD_REF": "dependabot/npm_and_yarn/next-16.3.8", "SENDER": "dependabot[bot]", "TRIGGER": "dependabot[bot]", "CHANGED": "2",
               "UPDATE_TYPE": "version-update:semver-patch", "PREV_VERSION": "16.3.7", "NEW_VERSION": "16.3.8", "DEP_NAMES": "next",
               "QQ_ALLOWED": step["env"]["QQ_ALLOWED"]}
        subprocess.run(["bash", "-e", "-c", step["run"]], check=True, env=env)
        return out.read_text().strip()

    assert run([CLEAN_COMMIT]) == "clean=true"
    assert run([dict(CLEAN_COMMIT, committer={"login": "mallory"})]) == "clean=false"
    assert run([CLEAN_COMMIT], head_rules=[]) == "clean=false"
    assert run([CLEAN_COMMIT], head_lock=LOCK_HEAD.replace("autoInstallPeers: true", "autoInstallPeers: false")) \
        == "clean=false"


def test_nested_directory_prefixes_patterns():
    rollers = [dict(ROLLERS[1], directory="/web")]
    assert dependabot._allowed(rollers, "innernet") == [["web/package.json", "npm-manifest"],
                                                        ["web/pnpm-lock.yaml", "npm-lock"]]


def test_repo_without_a_slug_is_an_error():
    with pytest.raises(dependabot.GenerateError, match="innernet: not a github.com repo"):
        dependabot.generate(ROLLERS, COMMIT, slugs={"xo-space": "quirq-ai/xo-space"})


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
    files = dependabot.generate(ROLLERS, COMMIT, slugs=SLUGS)
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
    (root / "config" / "repos.toml").write_text(
        '[[repo]]\nname = "xo-space"\nsource = "github.com/quirq-ai/xo-space"\n')
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


def test_land_workflow_passes_the_qq_drift_check():
    """The same check infra-config's generated presubmit runs on every qq-*.yml."""
    import hashlib
    text = dependabot.land_workflow(ROLLERS, "innernet", COMMIT, "quirq-ai/innernet")
    lines = text.splitlines(keepends=True)
    want = [l[len("# qq-digest: sha256:"):].strip() for l in lines if l.startswith("# qq-digest: sha256:")]
    body = "".join(l for l in lines if not l.startswith("# qq-digest: "))
    assert want == [hashlib.sha256(body.encode()).hexdigest()]
    assert yaml.safe_load(text)["name"] == "qq-roll-land"
    edited = text.replace("timeout-minutes: 5", "timeout-minutes: 6")
    body = "".join(l for l in edited.splitlines(keepends=True) if not l.startswith("# qq-digest: "))
    assert want != [hashlib.sha256(body.encode()).hexdigest()]
