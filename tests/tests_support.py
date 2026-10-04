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

  next@16.3.7:
    resolution: {integrity: sha512-old}
    engines: {node: '>=20.9.0'}
    bundledDependencies:
      - a

snapshots:

  next@16.3.7(@types/node@26.6.4)(react@19.3.0):
    dependencies:
      react: 19.3.0
"""
LOCK_HEAD = LOCK_BASE.replace("16.3.7", "16.3.8").replace("sha512-old", "sha512-new")
LOCKS = {"pnpm-lock.yaml": (LOCK_BASE, LOCK_HEAD)}
PIP_PATCH = "@@ -1 +1 @@\n-requests>=2.32.0\n+requests>=2.33.1\n"


def npm_file(name="package.json", patch=NPM_PATCH, **kw):
    return {"filename": name, "status": "modified", "patch": patch, **kw}
HEAD_RULES = [{"type": "update", "parameters": {}}, {"type": "non_fast_forward"}]
