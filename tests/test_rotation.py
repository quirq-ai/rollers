import base64
import json
from pathlib import Path

import pytest

from qqroll import backends, promoted, rotation
from qqroll.backends.github import Backend, BackendError
from qqroll.cli import main

FIX = Path(__file__).parent / "fixtures"
PROMOTED = promoted.parse((FIX / "promoted.toml").read_text())
STALE = (FIX / "stale.repo.toml").read_text()
ROLLED = (FIX / "rolled.repo.toml").read_text()
ROLLERS = [{"name": "toolchains", "repos": ["xo-space", "innernet"], "moves": "toolchain pins",
            "tool": "quirq-rollers", "cadence": "weekly"}]


class FakeGitHub:
    """Just enough of the GitHub REST and GraphQL APIs, recording every call."""

    def __init__(self, files: dict[str, str], branch_tree=None, open_pr=None, auto_merge_error=None):
        self.files = files
        self.branch_tree = branch_tree   # tree sha of the roll branch's commit, or None if no branch
        self.open_pr = open_pr           # the open roll PR, or None
        self.auto_merge_error = auto_merge_error
        self.calls = []

    def __call__(self, method, url, headers, body):
        path = url.removeprefix("https://api.github.com")
        payload = json.loads(body) if body else None
        self.calls.append((method, path, payload, headers.get("Authorization")))
        def ok(data, status=200):
            return status, json.dumps(data).encode()
        if method == "GET" and path.count("/") == 3:
            return ok({"default_branch": "main"})
        if "/contents/" in path:
            assert path.endswith("?ref=basesha"), "manifest must be read at the base commit"
            repo = path.split("/")[3]
            if repo not in self.files:
                return ok({"message": "Not Found"}, 404)
            return ok({"type": "file", "encoding": "base64",
                       "content": base64.b64encode(self.files[repo].encode()).decode()})
        if path.endswith("/git/ref/heads/main"):
            return ok({"object": {"sha": "basesha"}})
        if path.endswith("/git/ref/heads/qq-roll/toolchains"):
            return ok({"object": {"sha": "branchsha"}}) if self.branch_tree else ok({"message": "Not Found"}, 404)
        if path.endswith("/git/commits/basesha"):
            return ok({"tree": {"sha": "basetree"}})
        if path.endswith("/git/commits/branchsha"):
            return ok({"tree": {"sha": self.branch_tree}, "parents": [{"sha": "basesha"}]})
        if path.endswith("/git/trees"):
            return ok({"sha": "newtree"}, 201)
        if path.endswith("/git/commits") and method == "POST":
            return ok({"sha": "newcommit"}, 201)
        if "/git/refs/heads/" in path and method == "PATCH":
            return ok({})
        if path.endswith("/git/refs"):
            return ok({}, 201)
        if "/pulls?" in path:
            return ok([self.open_pr] if self.open_pr else [])
        if "/pulls" in path:
            return ok({"number": 7, "node_id": "PR_7", "html_url": "https://github.com/quirq-ai/x/pull/7"},
                      200 if method == "PATCH" else 201)
        if path == "/graphql":
            if self.auto_merge_error:
                return ok({"errors": [{"message": self.auto_merge_error}]})
            return ok({"data": {}})
        raise AssertionError(f"unexpected call {method} {path}")


OPEN_PR = {"number": 7, "node_id": "PR_7", "html_url": "https://github.com/quirq-ai/x/pull/7", "auto_merge": None}


def writes(fake):
    return [(m, path) for m, path, *_ in fake.calls if m in ("POST", "PATCH")]


def run(fake, apply=True, kinds_pins=None, auto_merge=True):
    backend = Backend(token="t", request=fake)
    return rotation.run(backend, ROLLERS, PROMOTED, kinds_pins=kinds_pins, promoted_from="toolchains@d020ec6",
                        rollers_commit="abc", apply=apply, auto_merge=auto_merge)


def test_repo_without_a_manifest_is_skipped():
    fake = FakeGitHub({})
    report = run(fake)
    assert report.prs == []
    assert all("no infra/repo.toml on main yet" in line for line in report.lines)
    assert writes(fake) == []


def test_current_manifest_opens_nothing():
    fake = FakeGitHub({"xo-space": ROLLED, "innernet": ROLLED})
    report = run(fake)
    assert report.prs == [] and report.lines == ["innernet: toolchain pins are current",
                                                 "xo-space: toolchain pins are current"]


def test_stale_manifest_gets_one_roll_pr_with_exactly_the_rolled_text():
    fake = FakeGitHub({"xo-space": STALE})
    report = run(fake)
    assert report.prs == ["https://github.com/quirq-ai/x/pull/7"] and report.warnings == []
    tree = next(p for m, path, p, _ in fake.calls if path.endswith("/git/trees"))
    assert tree["tree"] == [{"path": "infra/repo.toml", "mode": "100644", "type": "blob", "content": ROLLED}]
    commit = next(p for m, path, p, _ in fake.calls if path.endswith("/git/commits") and m == "POST")
    assert commit["parents"] == ["basesha"] and commit["message"].startswith("roll: toolchains python 3.14.8-r1")
    created = next(p for m, path, p, _ in fake.calls if path.endswith("/git/refs"))
    assert created == {"ref": "refs/heads/qq-roll/toolchains", "sha": "newcommit"}
    pr = next(p for m, path, p, _ in fake.calls if m == "POST" and path.endswith("/pulls"))
    assert pr["head"] == "qq-roll/toolchains" and pr["base"] == "main"
    assert "dependency-roll" in pr["body"] and "toolchains@d020ec6" in pr["body"]
    mutation = next(p for _, path, p, _ in fake.calls if path == "/graphql")
    assert "enablePullRequestAutoMerge" in mutation["query"] and mutation["variables"]["oid"] == "newcommit"
    assert all(auth == "Bearer t" for *_, auth in fake.calls)


def test_auto_merge_is_off_by_default():
    fake = FakeGitHub({"xo-space": STALE})
    report = run(fake, auto_merge=False)
    assert report.prs and not any(path == "/graphql" for _, path, _, _ in fake.calls)
    pr = next(p for m, path, p, _ in fake.calls if m == "POST" and path.endswith("/pulls"))
    assert "Auto-merge is off" in pr["body"]
    assert rotation.run.__kwdefaults__["auto_merge"] is False


def test_existing_roll_is_refreshed_not_duplicated():
    fake = FakeGitHub({"xo-space": STALE}, branch_tree="oldtree", open_pr=OPEN_PR)
    run(fake)
    assert ("PATCH", "/repos/quirq-ai/xo-space/git/refs/heads/qq-roll/toolchains") in writes(fake)
    assert ("PATCH", "/repos/quirq-ai/xo-space/pulls/7") in writes(fake)
    assert not any(m == "POST" and path.endswith(("/pulls", "/git/refs")) for m, path in writes(fake))


def test_identical_roll_pushes_nothing():
    fake = FakeGitHub({"xo-space": STALE}, branch_tree="newtree", open_pr=OPEN_PR)
    report = run(fake, auto_merge=False)
    assert report.prs == ["https://github.com/quirq-ai/x/pull/7"]
    assert [w for w in writes(fake) if w[1] != "/repos/quirq-ai/xo-space/git/trees"] == []


def test_earlier_auto_merge_is_turned_off_when_not_asked_for():
    fake = FakeGitHub({"xo-space": STALE}, branch_tree="oldtree", open_pr=dict(OPEN_PR, auto_merge={"x": 1}))
    run(fake, auto_merge=False)
    mutation = next(p for _, path, p, _ in fake.calls if path == "/graphql")
    assert "disablePullRequestAutoMerge" in mutation["query"]


def test_one_failing_repo_does_not_stop_the_others():
    fake = FakeGitHub({"xo-space": STALE, "innernet": STALE})
    def flaky(method, url, headers, body):
        if "/innernet" in url:
            return 502, b'{"message": "Bad gateway"}'
        return fake(method, url, headers, body)
    report = rotation.run(Backend(token="t", request=flaky), ROLLERS, PROMOTED, kinds_pins=None,
                          promoted_from="p", rollers_commit="r", apply=True)
    assert report.failed and "innernet: not rolled" in report.warnings[0]
    assert report.prs == ["https://github.com/quirq-ai/x/pull/7"]


def test_manifest_path_comes_from_config():
    fake = FakeGitHub({"xo-space": STALE})
    rotation.run(Backend(token="t", request=fake), ROLLERS, PROMOTED, kinds_pins=None, promoted_from="p",
                 rollers_commit="r", apply=False, manifests={"xo-space": "build/repo.toml"})
    assert any("/contents/build/repo.toml" in path for _, path, *_ in fake.calls)


def test_auto_merge_refusal_is_a_warning():
    fake = FakeGitHub({"xo-space": STALE}, auto_merge_error="Auto merge is not allowed for this repository")
    report = run(fake)
    assert report.prs and "waits for a human" in report.warnings[0]


def test_dry_run_writes_nothing_and_shows_the_diff():
    fake = FakeGitHub({"xo-space": STALE})
    report = run(fake, apply=False)
    assert writes(fake) == []
    text = "\n".join(report.lines)
    assert "+version = \"3.14.8\"" in text and "dry run" in text


def test_invalid_manifest_is_a_warning_not_a_crash():
    fake = FakeGitHub({"xo-space": 'schema = "quirq-repo/1"\n'})
    report = run(fake)
    assert report.prs == [] and "manifest is invalid" in report.warnings[0]


def test_http_errors_are_actionable():
    def broken(method, url, headers, body):
        return 401, b'{"message": "Bad credentials"}'
    with pytest.raises(BackendError, match="HTTP 401 Bad credentials"):
        Backend(token="t", request=broken).default_branch("xo-space")


def test_backend_uses_configured_slugs():
    fake = FakeGitHub({})
    Backend(slugs={"xo-space": "someone/xo-space"}, request=fake).default_branch("xo-space")
    assert fake.calls[0][1] == "/repos/someone/xo-space"


def test_backend_loader():
    assert isinstance(backends.load("github", token=None), Backend)
    with pytest.raises(ValueError, match="unknown backend 'launchpad'"):
        backends.load("launchpad")


def test_cli_apply_needs_the_bot_token(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("QQ_ROLLER_TOKEN", raising=False)
    args = ["rotation", "--infra-config", str(tmp_path), "--promoted", str(FIX / "promoted.toml"), "--apply"]
    assert main(args) == 1
    assert "GITHUB_TOKEN trigger no workflows" in capsys.readouterr().err
