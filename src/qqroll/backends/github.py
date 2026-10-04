"""The github backend: read a file from a repo and open or update a roll PR, over the REST API.

Roll PRs must be opened with the quirq infra bot's token: PRs opened with a workflow's default
GITHUB_TOKEN trigger no workflows, so they would never be gated.
"""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"


from qqroll.backends import BackendError


def _urllib_request(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _run(argv: list[str]) -> tuple[int, bytes, str]:
    if shutil.which(argv[0]) is None:
        return 127, b"", f"{argv[0]} is not installed"
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, b"", str(e)
    return proc.returncode, proc.stdout, proc.stderr.decode(errors="replace").strip()


TOOLCHAIN_ARTIFACT = "application/vnd.quirq.toolchain.v1"
TOOLCHAIN_LAYER = "application/vnd.quirq.toolchain.layer.v1.tar+gzip"


class Backend:
    def __init__(self, token: str | None = None, slugs: dict[str, str] | None = None,
                 org: str = "quirq-ai", request=_urllib_request, run=_run):
        self._run = run
        self.token = token
        self.slugs = slugs or {}   # repo name -> owner/name, from infra-config repos.toml
        self.org = org             # owner for repos repos.toml does not list
        self._request = request

    # --- transport -------------------------------------------------------------------------------

    def _call(self, method: str, path: str, payload: dict | None = None, ok=(200, 201)) -> tuple[int, dict]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "quirq-rollers"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        try:
            status, raw = self._request(method, API + path, headers, body)
            data = json.loads(raw) if raw else {}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            raise BackendError(f"{method} {path}: {e}") from None
        if status not in ok:
            message = data.get("message", "") if isinstance(data, dict) else ""
            raise BackendError(f"{method} {path}: HTTP {status} {message}".rstrip())
        return status, data

    def _slug(self, repo: str) -> str:
        return self.slugs.get(repo, f"{self.org}/{repo}")

    def _repo(self, repo: str) -> str:
        owner, name = self._slug(repo).split("/", 1)
        return f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}"

    # --- reading ---------------------------------------------------------------------------------

    def default_branch(self, repo: str) -> str:
        return self._call("GET", self._repo(repo))[1]["default_branch"]

    def head(self, repo: str, branch: str) -> str:
        """The commit `branch` points at."""
        q = urllib.parse.quote(branch)
        return self._call("GET", f"{self._repo(repo)}/git/ref/heads/{q}")[1]["object"]["sha"]

    def read_file(self, repo: str, path: str, ref: str) -> str | None:
        """The file's text at `ref` (a branch or commit), or None if the repo has no such file."""
        q = urllib.parse.quote(path)
        status, data = self._call("GET", f"{self._repo(repo)}/contents/{q}?ref={urllib.parse.quote(ref)}",
                                  ok=(200, 404))
        if status == 404:
            return None
        if data.get("type") != "file" or data.get("encoding") != "base64":
            raise BackendError(f"{repo}:{path} is not a regular file")
        return base64.b64decode(data["content"]).decode("utf-8")

    # --- verifying a promotion before rolling it ------------------------------------------------

    def verify_promotion(self, p, toolchains: str = "quirq-ai/toolchains") -> str | None:
        """Why `p` must not be rolled, or None when it checks out. Fails closed: a check that cannot
        run is a reason. The same checks as toolchains' promotion gate, run again here:

        1. the image manifest hashes to its digest and is exactly one toolchain layer, the pinned one;
        2. its build provenance verifies against toolchains' build.yml on main, at `built_from`.
        """
        ref = f"{p.registry}/{p.repository}@{p.manifest}"
        code, raw, err = self._run(["oras", "manifest", "fetch", ref])
        if code != 0:
            return f"cannot fetch the manifest of {ref}: {err or f'exit {code}'}"
        if "sha256:" + hashlib.sha256(raw).hexdigest() != p.manifest:
            return f"the manifest fetched for {ref} does not hash to {p.manifest}"
        try:
            doc = json.loads(raw)
            layers = doc["layers"]
            shape = (doc.get("artifactType"), len(layers), layers[0].get("digest"), layers[0].get("mediaType"))
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            return f"{ref} is not a toolchain image manifest"
        if shape != (TOOLCHAIN_ARTIFACT, 1, p.layer, TOOLCHAIN_LAYER):
            return f"{ref} is not exactly one toolchain layer {p.layer}"
        code, _, err = self._run([
            "gh", "attestation", "verify", f"oci://{ref}", "--repo", toolchains,
            "--signer-workflow", f"{toolchains}/.github/workflows/build.yml",
            "--source-ref", "refs/heads/main", "--source-digest", p.built_from])
        if code != 0:
            return f"no build provenance for {ref} from {toolchains} build.yml on main at {p.built_from}: " \
                   f"{err or f'exit {code}'}"
        return None

    # --- writing ---------------------------------------------------------------------------------

    def open_roll(self, repo: str, *, base: str, base_sha: str, branch: str, path: str, text: str,
                  title: str, body: str, auto_merge: bool = False) -> tuple[str, list[str]]:
        """Commit `text` to `path` on `branch`, on top of `base_sha` (the commit `text` was rolled
        from), and open or update its PR. Returns the PR URL and any warnings.

        The branch belongs to the roller and is force-moved, so a stale roll is replaced rather than
        stacked on. When the branch already holds exactly this roll, nothing is pushed, so the gate is
        not re-run and approvals stay.
        """
        r = self._repo(repo)
        q = urllib.parse.quote(branch)
        warnings: list[str] = []
        base_tree = self._call("GET", f"{r}/git/commits/{base_sha}")[1]["tree"]["sha"]
        tree = self._call("POST", f"{r}/git/trees", {
            "base_tree": base_tree,
            "tree": [{"path": path, "mode": "100644", "type": "blob", "content": text}]})[1]["sha"]
        status, ref = self._call("GET", f"{r}/git/ref/heads/{q}", ok=(200, 404))
        current = ref["object"]["sha"] if status == 200 else None
        unchanged = False
        if current:
            commit = self._call("GET", f"{r}/git/commits/{current}")[1]
            unchanged = (commit["tree"]["sha"] == tree
                         and [p["sha"] for p in commit.get("parents", [])] == [base_sha])
        if unchanged:
            head_sha = current
        else:
            head_sha = self._call("POST", f"{r}/git/commits", {"message": f"{title}\n\n{body}", "tree": tree,
                                                                "parents": [base_sha]})[1]["sha"]
            if current:
                self._call("PATCH", f"{r}/git/refs/heads/{q}", {"sha": head_sha, "force": True})
            else:
                self._call("POST", f"{r}/git/refs", {"ref": f"refs/heads/{branch}", "sha": head_sha})
        owner = self._slug(repo).split("/")[0]
        prs = self._call("GET", f"{r}/pulls?state=open&head={urllib.parse.quote(f'{owner}:{branch}')}")[1]
        had_auto_merge = bool(prs and prs[0].get("auto_merge"))
        if prs:
            pr = prs[0]
            if not unchanged:
                pr = self._call("PATCH", f"{r}/pulls/{pr['number']}", {"title": title, "body": body})[1]
        else:
            pr = self._call("POST", f"{r}/pulls", {"title": title, "body": body, "head": branch, "base": base})[1]
        if auto_merge:
            problem = self._graphql(
                "mutation($id: ID!, $oid: GitObjectID!) { enablePullRequestAutoMerge(input: "
                "{pullRequestId: $id, expectedHeadOid: $oid, mergeMethod: SQUASH}) { clientMutationId } }",
                {"id": pr["node_id"], "oid": head_sha})  # TODO(suraj): squash or merge (plan §10.3)
            if problem:
                warnings.append(f"{pr['html_url']}: auto-merge not enabled ({problem}); the roll waits for a human")
        elif had_auto_merge:
            # Auto-merge was not asked for: don't let new roll content land on an older setting.
            problem = self._graphql("mutation($id: ID!) { disablePullRequestAutoMerge(input: "
                                    "{pullRequestId: $id}) { clientMutationId } }", {"id": pr["node_id"]})
            if problem:
                warnings.append(f"{pr['html_url']}: could not turn auto-merge off ({problem})")
        return pr["html_url"], warnings

    def _graphql(self, query: str, variables: dict) -> str | None:
        """Run a mutation. A reason on failure, else None."""
        try:
            _, data = self._call("POST", "/graphql", {"query": query, "variables": variables})
        except BackendError as e:
            return str(e)
        errors = data.get("errors")
        return "; ".join(e.get("message", "") for e in errors) if errors else None
