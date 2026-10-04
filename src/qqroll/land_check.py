"""The check qq-roll-land runs on a Dependabot PR before it may land with no human (V0-ROL-02, D4).

This file's source is embedded as is in the generated workflow and run with the runner's python3,
so it uses only the standard library and reads everything from the environment:

    QQ_DIR        directory holding files.json, commits.json, rules.json (base branch) and head_rules.json
                  (head branch), each from `gh api --paginate --slurp`
    QQ_ALLOWED    JSON list of [glob, kind]: the files a roll may modify and how their lines are checked
    BASE_SHA, HEAD_SHA  the PR's base and head commits; changed lockfiles are read whole at the merge base
                  and the head with `gh api` (GH_TOKEN, GITHUB_REPOSITORY)
    SENDER, TRIGGER, CHANGED, UPDATE_TYPE, PREV_VERSION, NEW_VERSION, DEP_NAMES, GITHUB_OUTPUT

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
LOCK_FORBIDDEN = re.compile(r"tarball|://|git\+|git@|\bfile:|\blink:|\bgithub:|\brepo:|\bdirectory:|"
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
# dependency's specifier and version there, plus the same version where it appears in other entries'
# peer suffixes. packages and snapshots hold the resolved tree, which a bump legitimately reshapes.
# TODO(expert): check snapshots against registry metadata, so a roll cannot add a dependency edge to an
# unrelated package's snapshot.
LOCK_TREE = {"packages", "snapshots"}
LOCK_DEP_TYPES = {"dependencies", "devDependencies", "optionalDependencies"}
# pnpm writes block mappings and lists of plain or single-quoted scalars, one per line, with no comments.
# Everything else YAML allows (comments, double quotes and their escapes, block scalars, anchors,
# aliases, tags, values continued on the next line, quotes in the middle of a word) could make YAML read
# the file differently from this parser, so it is refused.
_LOCK_REFUSED = re.compile(r'["\\`#]')
_LOCK_VALUE_START = re.compile(r"[|>&*!%@`?-]")


def _closed(text, n):
    """`text`, checked to be one-line flow YAML as pnpm writes it: a single quote only opens a token
    (at the start, or after `[`, `{`, `, ` in brackets, or `: `) and closes before `,`, `]`, `}`, `:` or
    the end, with '' escaping one; and brackets balance."""
    def bad(why):
        return ValueError(f"line {n}: {why}")
    depth, token_start, i = 0, True, 0
    while i < len(text):
        c = text[i]
        if c in "&*!" and token_start:
            raise bad("an anchor, alias or tag")
        if c == "'":
            if not token_start:
                raise bad("a quote inside a word")
            j = i + 1
            while True:
                k = text.find("'", j)
                if k < 0:
                    raise bad("a quote left open (multi-line values are refused)")
                if text[k + 1:k + 2] != "'":
                    break
                j = k + 2
            i = k + 1
            after = text[i:].lstrip(" ")
            if after and after[0] not in ",]}:":
                raise bad("text after a quoted scalar")
            token_start = False
            continue
        if c in "{[":
            depth, token_start = depth + 1, True
        elif c in "}]":
            depth, token_start = depth - 1, False
            if depth < 0:
                raise bad("unbalanced brackets")
        elif c == "," and depth:
            token_start = True
        elif c == ":" and text[i + 1:i + 2] in (" ", ""):
            token_start = True
        elif c != " ":
            token_start = False
        i += 1
    if depth:
        raise bad("a bracket left open (multi-line values are refused)")
    return text


def lock_leaves(text):
    """pnpm-lock.yaml as {key path: scalar}, for the subset of YAML pnpm writes: block mappings and block
    lists of one-line scalars (a list is one leaf under the key "-"); flow values stay text. Children of
    a key share one indent, deeper than the key's, and are all keys or all list items.

    Anything outside that subset raises ValueError, so an odd lockfile fails closed.
    """
    leaves = {}
    # Open containers: [indent, key, indent of their children (once seen), "map" or "list"].
    stack = [[-1, None, None, None]]
    for n, line in enumerate(text.split("\n"), 1):
        line = line.removesuffix("\r")
        body = line.strip(" ")
        if not body:
            continue
        if CONTROL.search(line) or "\t" in line or _LOCK_REFUSED.search(line):
            raise ValueError(f"line {n}: a control character, #, double quote, backslash or backtick")
        indent = len(line) - len(line.lstrip(" "))
        is_item = body == "-" or body.startswith("- ")
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1]
        if parent[2] is None:
            parent[2], parent[3] = indent, "list" if is_item else "map"
        elif parent[2] != indent or parent[3] != ("list" if is_item else "map"):
            raise ValueError(f"line {n}: indentation or a mix of keys and list items YAML would read differently")
        if is_item:
            item = _closed(body[1:].strip(), n)
            if not item or _LOCK_VALUE_START.match(item):
                raise ValueError(f"line {n}: a list item pnpm does not write")
            if parent[1] is None:
                raise ValueError(f"line {n}: list item outside a mapping")
            path = tuple(k for _, k, _, _ in stack[1:]) + ("-",)
            leaves[path] = (leaves.get(path) or "") + item + "\n"
            stack.append([indent, None, -2, "scalar"])  # nothing may nest under an item
            continue
        if body.startswith("'"):
            m = re.match(r"'((?:[^']|'')*)':( |$)", body)
            if not m:
                raise ValueError(f"line {n}: not a quoted key")
            key, rest = m[1].replace("''", "'"), body[m.end():]
        elif ": " in body or body.endswith(":"):
            key, _, rest = body.partition(":")
            if not re.fullmatch(r"[^'{}\[\],&*!|>%@?<-][^'{}\[\],]*", key) or key != key.strip() \
                    or (rest and not rest.startswith(" ")):
                raise ValueError(f"line {n}: not a plain key")
        else:
            raise ValueError(f"line {n}: not a mapping entry")
        rest = _closed(rest.strip(), n)
        if _LOCK_VALUE_START.match(rest) and not re.match(r"-[0-9]", rest):
            raise ValueError(f"line {n}: a YAML indicator pnpm does not write")
        path = tuple(k for _, k, _, _ in stack[1:]) + (key,)
        if path in leaves:
            raise ValueError(f"line {n}: duplicate key {'/'.join(path)}")
        leaves[path] = rest or None
        # A key with a value takes no children; one without opens a container.
        stack.append([indent, key, None, None] if not rest else [indent, key, -2, "scalar"])
    return leaves


def _split_version(value):
    """An importer version, `16.3.8(react@19.3.0)`, as ("16.3.8", "(react@19.3.0)"), or None."""
    m = re.fullmatch(r"(" + _VERSION + r")(\(.*\))?", value or "")
    return (m[1], m[2] or "") if m else None


def lock_problems(shown, base, head, dep, prev, new_version):
    """What a roll of `dep` from `prev` to `new_version` changes in pnpm-lock.yaml beyond that
    dependency's importer entries and its version in other entries' peer suffixes."""
    try:
        old, new = lock_leaves(base), lock_leaves(head)
    except ValueError as e:
        return [f"{shown}: not a lockfile this check can read ({e})"]
    # The bumped dependency's own version inside a peer suffix, e.g. (react@19.3.0), may move.
    peer = re.compile(r"(?<=\()" + re.escape(dep) + "@" + _VERSION)

    def unpeered(suffix):
        return peer.sub(dep + "@V", suffix)

    def peers_moved_to_new(was, now):
        """Every version of `dep` in the new suffix is the new version or one the old suffix had."""
        kept = set(peer.findall(was))
        return all(v in kept or v == f"{dep}@{new_version}" for v in peer.findall(now))
    out = []
    for path in sorted(set(old) | set(new)):
        before, after = old.get(path), new.get(path)
        if (path in old and path in new and before == after) or path[0] in LOCK_TREE:
            continue
        entry_field = (len(path) == 5 and path[0] == "importers" and path[2] in LOCK_DEP_TYPES
                       and path in old and path in new and path[4] in ("specifier", "version"))
        if entry_field and path[3] == dep:
            if path[4] == "specifier" and before and after and new_version in _BARE_VERSION.findall(after) \
                    and _BARE_VERSION.sub("V", before) == _BARE_VERSION.sub("V", after):
                continue
            was, now = _split_version(before), _split_version(after)
            if path[4] == "version" and was and now and was[0] == prev and now[0] == new_version \
                    and unpeered(was[1]) == unpeered(now[1]) and peers_moved_to_new(was[1], now[1]):
                continue
        elif entry_field and path[4] == "version":
            was, now = _split_version(before), _split_version(after)
            if was and now and was[0] == now[0] and unpeered(was[1]) == unpeered(now[1]) \
                    and peers_moved_to_new(was[1], now[1]):
                continue
        out.append(f"{shown}: changes {json.dumps('/'.join(path))[:100]}, which a roll of "
                   f"{json.dumps(dep)} from {json.dumps(prev)} to {json.dumps(new_version)} may not")
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
                if sign == "+" and LOCK_FORBIDDEN.search(line.replace("'", "")):  # quoted keys too
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
            elif len(dep_names) != 1:
                out.append(f"{shown}: a lockfile is checked only for a roll of exactly one dependency")
            else:
                out += lock_problems(shown, *locks[filename], dep_names[0], env.get("PREV_VERSION", "").strip(),
                                     env.get("NEW_VERSION", "").strip())
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
