"""The check qq-roll-land runs on a Dependabot PR before it may land with no human (V0-ROL-02, D4).

This file's source is embedded as is in the generated workflow and run with the runner's python3,
so it uses only the standard library and reads everything from the environment:

    QQ_DIR        directory holding files.json, commits.json, rules.json (base branch) and head_rules.json
                  (head branch), each from `gh api --paginate --slurp`
    QQ_ALLOWED    JSON list of [glob, kind]: the files a roll may modify and how their lines are checked
    BASE_SHA, HEAD_SHA  the PR's base and head commits; changed lockfiles are read whole at the merge base
                  and the head with `gh api` (GH_TOKEN, GITHUB_REPOSITORY)
    SENDER, TRIGGER, CHANGED, UPDATE_TYPE, PREV_VERSION, DEP_NAMES, GITHUB_OUTPUT

A PR is clean only when every rule holds; anything else goes to a human. It fails closed: missing
or unexpected data is "not clean", never "clean".
"""
import fnmatch
import json
import os
import re
import subprocess
import sys
import urllib.parse

DEPENDABOT = "dependabot[bot]"
MAX_FILES = 20
# Check runs made with any workflow's GITHUB_TOKEN belong to the GitHub Actions app, so a required
# check bound to it can be forged by anyone who can run a workflow in the repo.
GITHUB_ACTIONS_APP = 15368

# Changed lines a clean roll may contain, per kind of file. Anything else (scripts, index options,
# URL requirements, non-registry resolutions) is not a pin or lockfile update (gate.toml dependency-roll).
_VERSION = r"[0-9][0-9A-Za-z.+-]*"
_OP = r"(==|>=|<=|~=|!=|<|>)"
LINE_RULES = {
    # "name": "^1.2.3", in package.json dependency maps; removed and added lines alike
    "npm-manifest": re.compile(r'^\s*"(?P<key>(@[a-z0-9._-]+/)?[a-z0-9._-]+)":\s*"[\^~]?' + _VERSION + r'",?\s*$'),
    # name==1.2.3 or name>=1.2,<2 with an optional trailing comment; no options, URLs or markers. A
    # backslash in the comment is refused: pip would join the next line into it and drop that pin.
    "pip-requirements": re.compile(r"^(?P<key>[A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9._,-]+\])?\s*" + _OP + r"\s*"
                                   + _VERSION + r"(\s*,\s*" + _OP + r"\s*" + _VERSION + r")*(\s+#[^\\]*)?\s*$"),
}
# Characters that are not printable on one line: C0 controls but tab, DEL, C1 controls and the Unicode
# line and paragraph separators.
CONTROL = re.compile("[\x00-\x08\x0a-\x1f\x7f-\x9f\u2028\u2029]")
# A version where one starts: right after an operator, a range prefix or the opening quote.
_PLACED_VERSION = re.compile(r'(?<=[=<>~!^"])\s*' + _VERSION)
_BARE_VERSION = re.compile(_VERSION)
# Added lockfile lines may not point anywhere but the registry.
LOCK_FORBIDDEN = re.compile(r"tarball:|://|git\+|git@|\bfile:|\blink:|\bgithub:|\brepo:|\bdirectory:|"
                            r"\btype:\s*(git|directory)\b")


def pages(path):
    """`gh api --paginate --slurp` writes a list of pages; each page is a list of items."""
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list) or not all(isinstance(p, list) for p in data):
        raise ValueError(f"{path}: not a list of pages")
    return [item for page in data for item in page]


def matches(path, glob):
    """fnmatch, but `*` never crosses a directory: the slash counts must agree."""
    return fnmatch.fnmatchcase(path, glob) and path.count("/") == glob.count("/")


def changed_lines(patch):
    """The +/- lines of a patch. File headers can only come before the first hunk: after it, a line
    starting with --- is a removed line that starts with --, such as a pip --index-url option."""
    in_hunk = False
    # Split on \n alone, as GitHub does: str.splitlines also splits on \r, \f, \x85 and more, which
    # would turn the rest of a changed line into an unchecked context line.
    for line in patch.split("\n"):
        line = line.removesuffix("\r")
        if line.startswith("@@"):
            in_hunk = True
        elif not in_hunk and line.startswith(("+++", "---")):
            continue
        elif line[:1] in ("+", "-"):
            yield line[0], line[1:]


def dep_name(kind, key):
    """The name a dependency is known by: PEP 503 normalized for pip, as written for npm."""
    return re.sub(r"[-_.]+", "-", key).lower() if kind == "pip-requirements" else key


def entry(kind, m):
    """A changed line with its version numbers blanked: a bump changes the numbers and nothing else,
    so the removed and added lines of a bump have equal entries (same name, extras, operators, range
    prefix and comment)."""
    rest = m.string[m.end("key"):]
    return dep_name(kind, m["key"]), " ".join(_PLACED_VERSION.sub("V", rest).split())


# pnpm-lock.yaml: the importers section says which version of each direct dependency is installed, and
# settings (and any other top-level section) how. A roll of one dependency may change only that
# dependency's specifier and version there. packages and snapshots hold the resolved tree, which a bump
# legitimately reshapes; their added lines are only checked against LOCK_FORBIDDEN.
# TODO(expert): check snapshots against registry metadata, so a roll cannot add a dependency edge to an
# unrelated package's snapshot.
LOCK_TREE = {"packages", "snapshots"}
LOCK_DEP_TYPES = {"dependencies", "devDependencies", "optionalDependencies"}
_LOCK_VERSION = re.compile(r"[0-9][0-9A-Za-z.+-]*(\(.*\))?")


def lock_leaves(text):
    """pnpm-lock.yaml as {key path: scalar}, for its subset of YAML: block mappings and block lists of
    scalars (a list is one leaf under the key "-"); flow values stay text.

    Anything outside that subset raises ValueError, so an odd lockfile fails closed.
    """
    leaves, stack = {}, []
    for n, line in enumerate(text.split("\n"), 1):
        line = line.removesuffix("\r")
        body = line.strip(" ")
        if not body or body.startswith("#"):
            continue
        if CONTROL.search(line) or "\t" in line:
            raise ValueError(f"line {n}: control character")
        indent = len(line) - len(line.lstrip(" "))
        if body == "-" or body.startswith("- "):  # a block list item: kept, in order, as the list's text
            while stack and stack[-1][0] >= indent:
                stack.pop()
            if not stack:
                raise ValueError(f"line {n}: list item outside a mapping")
            path = tuple(k for _, k in stack) + ("-",)
            leaves[path] = (leaves.get(path) or "") + body[1:].strip() + "\n"
            continue
        if body.startswith(("'", '"')):
            end = body.find(body[0], 1)
            if end < 0 or body[end + 1:end + 2] != ":":
                raise ValueError(f"line {n}: unterminated quoted key")
            key, rest = body[1:end], body[end + 2:]
        elif ": " in body or body.endswith(":"):
            key, _, rest = body.partition(":")
        else:
            raise ValueError(f"line {n}: not a mapping entry")
        if rest and not rest.startswith(" "):
            raise ValueError(f"line {n}: not a mapping entry")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = tuple(k for _, k in stack) + (key,)
        if path in leaves:
            raise ValueError(f"line {n}: duplicate key {'/'.join(path)}")
        leaves[path] = rest.strip() or None
        if not rest.strip():
            stack.append((indent, key))
    return leaves


def lock_problems(shown, base, head, deps):
    """What a roll of `deps` changes in pnpm-lock.yaml beyond the bumped dependency's importer entries."""
    try:
        old, new = lock_leaves(base), lock_leaves(head)
    except ValueError as e:
        return [f"{shown}: not a lockfile this check can read ({e})"]
    out = []
    for path in sorted(set(old) | set(new)):
        if old.get(path, ()) == new.get(path, ()) or path[0] in LOCK_TREE:
            continue
        bumped = (len(path) == 5 and path[0] == "importers" and path[2] in LOCK_DEP_TYPES
                  and path[3] in deps and path[4] in ("specifier", "version")
                  and old.get(path) and new.get(path))
        if bumped and path[4] == "specifier" and _BARE_VERSION.sub("V", old[path]) == _BARE_VERSION.sub("V", new[path]):
            continue
        if bumped and path[4] == "version" and _LOCK_VERSION.fullmatch(new[path]):
            continue
        out.append(f"{shown}: changes {json.dumps('/'.join(path))[:100]}, which a roll of "
                   f"{json.dumps(sorted(deps))} may not")
    return out[:10]


def problems(env, files, commits, rules, allowed, head_rules, locks):
    """Why the roll is not clean; empty when it is. `locks` maps each changed npm-lock file to its
    (merge base, head) contents."""
    out = []
    if env.get("SENDER") != DEPENDABOT or env.get("TRIGGER") != DEPENDABOT:
        out.append(f"triggered by {env.get('SENDER')!r}/{env.get('TRIGGER')!r}, not Dependabot")

    # Exactly one commit, made by Dependabot through GitHub (committer web-flow, signed by GitHub).
    # Author emails can be set by anyone, so the committer and the event sender carry the trust.
    if len(commits) != 1:
        out.append(f"{len(commits)} commits; a Dependabot roll has exactly one")
    for c in commits:
        author = (c.get("author") or {}).get("login")
        committer = (c.get("committer") or {}).get("login")
        verified = ((c.get("commit") or {}).get("verification") or {}).get("verified") is True
        if (author, committer, verified) != (DEPENDABOT, "web-flow", True):
            out.append(f"commit {c.get('sha', '?')[:12]}: author {author!r}, committer {committer!r}, "
                       f"verified {verified}; not a GitHub-made Dependabot commit")

    try:
        changed = int(env.get("CHANGED", ""))
    except ValueError:
        changed = -1
    if changed != len(files) or not 0 < changed <= MAX_FILES:
        out.append(f"the PR changes {changed} files and {len(files)} were listed (at most {MAX_FILES})")
    dep_names = [d.strip() for d in env.get("DEP_NAMES", "").split(",") if d.strip()]
    deps = {kind: {dep_name(kind, d) for d in dep_names} for kind in LINE_RULES}
    for f in files:
        filename = f.get("filename", "")
        shown = json.dumps(filename)
        if f.get("status") != "modified" or f.get("previous_filename"):
            out.append(f"{shown}: {f.get('status')} (only modified files, no renames)")
            continue
        kinds = [kind for glob, kind in allowed if matches(filename, glob)]
        if not kinds:
            out.append(f"{shown}: not a dependency file")
            continue
        patch = f.get("patch")
        if not patch:
            out.append(f"{shown}: no diff to check (too large or binary)")
            continue
        keys = {"+": [], "-": []}
        for sign, line in changed_lines(patch):
            # pip and other readers split lines on these too, so one could hide a second line in this one.
            if CONTROL.search(line):
                out.append(f"{shown}: control or line-separator character in a changed line: "
                           f"{json.dumps(line.strip())[:100]}")
                continue
            if kinds[0] == "npm-lock":
                if sign == "+" and LOCK_FORBIDDEN.search(line):
                    out.append(f"{shown}: non-registry resolution: {json.dumps(line.strip())[:100]}")
                continue
            m = LINE_RULES[kinds[0]].match(line)
            if not m:
                out.append(f"{shown}: not a version line: {json.dumps(line.strip())[:100]}")
            else:
                keys[sign].append(entry(kinds[0], m))
        # A bump rewrites existing entries in place: the same entries go out and come back in, with only
        # their version numbers changed, and only for the dependency the PR says it bumps.
        if sorted(keys["+"]) != sorted(keys["-"]):
            out.append(f"{shown}: adds, removes or reshapes entries, not only versions")
        if kinds[0] == "npm-lock":
            if filename not in locks:
                out.append(f"{shown}: lockfile contents were not read")
            else:
                out += lock_problems(shown, *locks[filename], deps["npm-manifest"])
        others = sorted({n for n, _ in keys["+"] + keys["-"]} - deps.get(kinds[0], set()))
        if others:
            out.append(f"{shown}: changes {json.dumps(others)[:100]}, not the bumped dependency")

    # Only patch and minor bumps of one dependency; a 0.x minor bump can break, so it counts as major.
    update = env.get("UPDATE_TYPE", "")
    if update not in ("version-update:semver-patch", "version-update:semver-minor"):
        out.append(f"update type {update!r}; only patch and minor bumps land alone")
    if "," in env.get("DEP_NAMES", ""):
        out.append("several dependencies in one PR")
    # Semver: below 1.0 a minor bump can break, and below 0.1 even a patch bump can. Unknown counts as 0.x.
    prev = env.get("PREV_VERSION", "").strip().lstrip("vV")
    if update == "version-update:semver-minor" and (not prev or prev.startswith("0.")):
        out.append(f"minor bump from {env.get('PREV_VERSION')!r}; 0.x minors can break, so a human lands it")
    if update == "version-update:semver-patch" and (not prev or prev == "0.0" or prev.startswith("0.0.")):
        out.append(f"patch bump from {env.get('PREV_VERSION')!r}; 0.0.x patches can break, so a human lands it")

    # Auto-merge waits for required checks only if there are some that a workflow cannot fake: a
    # required check bound to an app other than GitHub Actions, or a required workflow (ruleset).
    bound = [check for r in rules if r.get("type") == "required_status_checks"
             for check in (r.get("parameters") or {}).get("required_status_checks", [])
             if type(check.get("integration_id")) is int and check["integration_id"] > 0
             and check["integration_id"] != GITHUB_ACTIONS_APP]
    # A required workflow counts only if every workflow in the rule is pinned to a commit, so nobody with
    # push access can edit what it runs.
    workflows = [r for r in rules if r.get("type") == "workflows"
                 and (r.get("parameters") or {}).get("workflows")
                 and all(isinstance(w, dict) and re.fullmatch(r"[0-9a-f]{40}", str(w.get("sha", "")))
                         for w in r["parameters"]["workflows"])]
    if not bound and not workflows:
        out.append("no required workflow pinned by sha, and no required check bound to an app other than "
                   "GitHub Actions, on the base branch; any workflow could fake the gate, so a human lands it")

    # Auto-merge stays on after this run, so nobody but Dependabot may push to the PR branch afterwards.
    # (Who is exempt from these rules is not readable here; that part is on the admin.)
    head_types = {r.get("type") for r in head_rules}
    if not {"update", "non_fast_forward"} <= head_types:
        out.append(f"the PR branch has no update and non_fast_forward rules (has {sorted(map(str, head_types))}); "
                   "anyone with push access could swap its commit after this check, so a human lands it")
    return out


def gh(*args):
    return subprocess.run(["gh", "api", *args], check=True, capture_output=True, text=True).stdout


def read_locks(env, files, allowed):
    """(merge base, head) contents of each changed npm-lock file, read whole through the contents API."""
    names = [f.get("filename", "") for f in files
             if any(kind == "npm-lock" and matches(f.get("filename", ""), glob) for glob, kind in allowed)]
    if not names:
        return {}
    repo, head = env["GITHUB_REPOSITORY"], env["HEAD_SHA"]
    base = gh(f"repos/{repo}/compare/{env['BASE_SHA']}...{head}", "--jq", ".merge_base_commit.sha").strip()
    for sha in (base, head):
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError(f"not a commit sha: {sha!r}")
    raw = ("-H", "Accept: application/vnd.github.raw+json")
    return {name: tuple(gh(*raw, f"repos/{repo}/contents/{urllib.parse.quote(name)}?ref={sha}")
                        for sha in (base, head)) for name in names}


def main():
    env = os.environ
    d = env["QQ_DIR"]
    files, allowed = pages(f"{d}/files.json"), json.loads(env["QQ_ALLOWED"])
    found = problems(env, files, pages(f"{d}/commits.json"), pages(f"{d}/rules.json"),
                     allowed, pages(f"{d}/head_rules.json"), read_locks(env, files, allowed))
    for p in found:
        print(f"not clean: {p}")
    if not found:
        print("clean: lands once the required checks pass")
    with open(env["GITHUB_OUTPUT"], "a") as out:
        out.write(f"clean={'false' if found else 'true'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
