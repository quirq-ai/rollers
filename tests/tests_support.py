"""Shared samples for the land check tests: what GitHub returns for a clean Dependabot roll."""

CLEAN_COMMIT = {"sha": "a" * 40, "author": {"login": "dependabot[bot]"}, "committer": {"login": "web-flow"},
                "commit": {"verification": {"verified": True}}}
RULES = [{"type": "required_status_checks",
          "parameters": {"required_status_checks": [{"context": "qq-gate", "integration_id": 424242}]}}]
NPM_PATCH = '@@ -1,3 +1,3 @@\n   "dependencies": {\n-    "next": "^16.3.7",\n+    "next": "^16.3.8",\n'
LOCK_PATCH = "@@ -10,2 +10,2 @@\n-  next@16.3.7:\n-    resolution: {integrity: sha512-old}\n" \
             "+  next@16.3.8:\n+    resolution: {integrity: sha512-new}\n"
LOCK_BASE = """lockfileVersion: '9.0'

settings:
  autoInstallPeers: true
  excludeLinksFromLockfile: false

importers:

  .:
    dependencies:
      next:
        specifier: ^16.3.7
        version: 16.3.7(@types/node@26.6.4)(react@19.3.0)
      react:
        specifier: ^19.3.0
        version: 19.3.0
    devDependencies:
      '@types/node':
        specifier: ^26.6.4
        version: 26.6.4

packages:

  '@types/node@26.6.4':
    resolution: {integrity: sha512-typesnode}

  next@16.3.7:
    resolution: {integrity: sha512-old}
    engines: {node: '>=20.9.0'}
    hasBin: true
    peerDependencies:
      '@types/node': '>=18'
      react: ^18.2.0 || ^19.0.0
    bundledDependencies:
      - a

  react@19.3.0:
    resolution: {integrity: sha512-react}
    engines: {node: '>=0.10.0'}

  styled-jsx@5.1.6:
    resolution: {integrity: sha512-styled}

snapshots:

  '@types/node@26.6.4': {}

  next@16.3.7(@types/node@26.6.4)(react@19.3.0):
    dependencies:
      '@types/node': 26.6.4
      react: 19.3.0
      styled-jsx: 5.1.6(react@19.3.0)

  react@19.3.0: {}

  styled-jsx@5.1.6(react@19.3.0):
    dependencies:
      react: 19.3.0
"""
# next 16.3.8 also starts depending on client-only, so the bump adds a package to the tree.
LOCK_HEAD = LOCK_BASE.replace("16.3.7", "16.3.8").replace("sha512-old", "sha512-new").replace(
    "  react@19.3.0:\n    resolution", "  client-only@0.0.1:\n    resolution: {integrity: sha512-clientonly}\n\n"
    "  react@19.3.0:\n    resolution").replace(
    "      '@types/node': 26.6.4\n      react: 19.3.0\n      styled-jsx",
    "      '@types/node': 26.6.4\n      client-only: 0.0.1\n      react: 19.3.0\n      styled-jsx").replace(
    "  react@19.3.0: {}", "  client-only@0.0.1: {}\n\n  react@19.3.0: {}")
NEXT_PEERS = {"@types/node": ">=18", "react": "^18.2.0 || ^19.0.0"}
NEXT_META = {"engines": {"node": ">=20.9.0"}, "bin": {"next": "dist/bin/next"}, "bundleDependencies": ["a"],
             "peerDependencies": NEXT_PEERS}
REACT_META = {"engines": {"node": ">=0.10.0"}}
REGISTRY = {
    ("next", "16.3.7"): {"dist": {"integrity": "sha512-old"}, "dependencies": {"styled-jsx": "5.1.6"}, **NEXT_META},
    ("next", "16.3.8"): {"dist": {"integrity": "sha512-new"},
                         "dependencies": {"styled-jsx": "5.1.6", "client-only": "0.0.1"}, **NEXT_META},
    ("client-only", "0.0.1"): {"dist": {"integrity": "sha512-clientonly"}},
    ("styled-jsx", "5.1.6"): {"dist": {"integrity": "sha512-styled"}, "peerDependencies": {"react": ">= 16.8.0"}},
    ("react", "19.3.0"): {"dist": {"integrity": "sha512-react"}, **REACT_META},
    ("react", "19.3.1"): {"dist": {"integrity": "sha512-react1"}, **REACT_META},
    ("@types/node", "26.6.4"): {"dist": {"integrity": "sha512-typesnode"}},
    ("@types/node", "26.6.5"): {"dist": {"integrity": "sha512-typesnode5"}},
}


def registry(name, version, table=None):
    """A fake npm registry: the manifest of `name` at `version`, as registry.npmjs.org returns it."""
    found = (REGISTRY if table is None else table).get((name, version))
    if found is None:
        raise LookupError(f"{name}@{version}")
    return {"name": name, "version": version, **found}


def registry_from(lock):
    """A fake registry that agrees with a lockfile: each package's integrity and other fields, and its
    snapshot edges as exact dependencies of the same kind."""
    from qqroll import land_check
    leaves, table = land_check.lock_leaves(lock), {}
    for key, entry in land_check._section(leaves, "packages").items():
        name, _, version = key.rpartition("@")
        meta = land_check._package_fields(entry)
        if meta.pop("hasBin", None):
            meta["bin"] = {name: "bin"}
        if "bundledDependencies" in meta:
            meta["bundleDependencies"] = meta.pop("bundledDependencies")
        for d in meta.get("peerDependenciesMeta", {}).values():
            d.update({k: v == "true" for k, v in d.items()})
        meta["peerDependencies"] = {n: r for n, r in meta.get("peerDependencies", {}).items()
                                    if not (r == "*" and n in meta.get("peerDependenciesMeta", {}))}
        resolution = entry[("resolution",)]
        table[(name, version)] = {"dist": {"integrity": resolution[len("{integrity: "):-1]}, "dependencies": {},
                                  "optionalDependencies": {}, **meta}
    for path, value in leaves.items():
        if path[0] == "snapshots" and len(path) == 4 and path[2] in ("dependencies", "optionalDependencies"):
            name, _, version = path[1].partition("(")[0].rpartition("@")
            table[(name, version)][path[2]][path[3]] = value.partition("(")[0]
    return lambda name, version: registry(name, version, table)


LOCKS = {"pnpm-lock.yaml": (LOCK_BASE, LOCK_HEAD)}
PIP_PATCH = "@@ -1 +1 @@\n-requests>=2.32.0\n+requests>=2.33.1\n"


def npm_file(name="package.json", patch=NPM_PATCH, **kw):
    return {"filename": name, "status": "modified", "patch": patch, **kw}
HEAD_RULES = [{"type": "update", "parameters": {}}, {"type": "non_fast_forward"}]
