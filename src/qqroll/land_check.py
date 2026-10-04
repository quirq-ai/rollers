"""The check qq-roll-land runs on a Dependabot PR before it may land with no human (V0-ROL-02, D4).

This file's source is embedded as is in the generated workflow and run with the runner's python3,
so it uses only the standard library and reads everything from the environment:

    QQ_DIR        directory holding files.json, commits.json and rules.json (`gh api --paginate --slurp`)
    QQ_ALLOWED    JSON list of [glob, kind]: the files a roll may modify and how their lines are checked
    SENDER, TRIGGER, CHANGED, UPDATE_TYPE, PREV_VERSION, DEP_NAMES, GITHUB_OUTPUT

A PR is clean only when every rule holds; anything else goes to a human. It fails closed: missing
or unexpected data is "not clean", never "clean".
"""
import fnmatch
import json
import os
import re
import sys

DEPENDABOT = "dependabot[bot]"
MAX_FILES = 20

# Changed lines a clean roll may contain, per kind of file. Anything else (scripts, index options,
# URL requirements, non-registry resolutions) is not a pin or lockfile update (gate.toml dependency-roll).
_VERSION = r"[0-9][0-9A-Za-z.+-]*"
_OP = r"(==|>=|<=|~=|!=|<|>)"
LINE_RULES = {
    # "name": "^1.2.3", in package.json dependency maps; removed and added lines alike
    "npm-manifest": re.compile(r'^\s*"(@[a-z0-9._-]+/)?[a-z0-9._-]+":\s*"[\^~]?' + _VERSION + r'",?\s*$'),
    # name==1.2.3 or name>=1.2,<2 with an optional trailing comment; no options, URLs or markers
    "pip-requirements": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,-]+\])?\s*" + _OP + r"\s*"
                                   + _VERSION + r"(\s*,\s*" + _OP + r"\s*" + _VERSION + r")*\s*(#.*)?$"),
}
# Added lockfile lines may not point anywhere but the registry.
LOCK_FORBIDDEN = re.compile(r"tarball:|://|git\+|\bfile:|\blink:|\bgithub:")


def pages(path):
    """`gh api --paginate --slurp` writes a list of pages; each page is a list of items."""
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list) or not all(isinstance(p, list) for p in data):
        raise ValueError(f"{path}: not a list of pages")
    return [item for page in data for item in page]


def matches(name, glob):
    """fnmatch, but `*` never crosses a directory: the slash counts must agree."""
    return fnmatch.fnmatchcase(name, glob) and name.count("/") == glob.count("/")


def changed_lines(patch):
    for line in patch.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line[:1] in ("+", "-"):
            yield line[0], line[1:]


def problems(env, files, commits, rules, allowed):
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
    for f in files:
        name = f.get("filename", "")
        shown = json.dumps(name)
        if f.get("status") != "modified" or f.get("previous_filename"):
            out.append(f"{shown}: {f.get('status')} (only modified files, no renames)")
            continue
        kinds = [kind for glob, kind in allowed if matches(name, glob)]
        if not kinds:
            out.append(f"{shown}: not a dependency file")
            continue
        patch = f.get("patch")
        if not patch:
            out.append(f"{shown}: no diff to check (too large or binary)")
            continue
        for sign, line in changed_lines(patch):
            if kinds[0] == "npm-lock":
                if sign == "+" and LOCK_FORBIDDEN.search(line):
                    out.append(f"{shown}: non-registry resolution: {json.dumps(line.strip())[:100]}")
            elif not LINE_RULES[kinds[0]].match(line):
                out.append(f"{shown}: not a version line: {json.dumps(line.strip())[:100]}")

    # Only patch and minor bumps of one dependency; a 0.x minor bump can break, so it counts as major.
    update = env.get("UPDATE_TYPE", "")
    if update not in ("version-update:semver-patch", "version-update:semver-minor"):
        out.append(f"update type {update!r}; only patch and minor bumps land alone")
    if "," in env.get("DEP_NAMES", ""):
        out.append("several dependencies in one PR")
    if update == "version-update:semver-minor" and env.get("PREV_VERSION", "0").startswith("0."):
        out.append(f"minor bump from {env.get('PREV_VERSION')!r}; 0.x minors can break, so a human lands it")

    # Auto-merge waits for required checks only if there are some, bound to the app that runs them.
    bound = [check for r in rules if r.get("type") == "required_status_checks"
             for check in (r.get("parameters") or {}).get("required_status_checks", [])
             if check.get("integration_id")]
    if not bound:
        out.append("no required status check bound to its app on the base branch; nothing would gate the roll")
    return out


def main():
    env = os.environ
    d = env["QQ_DIR"]
    found = problems(env, pages(f"{d}/files.json"), pages(f"{d}/commits.json"), pages(f"{d}/rules.json"),
                     json.loads(env["QQ_ALLOWED"]))
    for p in found:
        print(f"not clean: {p}")
    if not found:
        print("clean: lands once the required checks pass")
    with open(env["GITHUB_OUTPUT"], "a") as out:
        out.write(f"clean={'false' if found else 'true'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
