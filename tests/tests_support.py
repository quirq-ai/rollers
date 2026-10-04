"""Shared samples for the land check tests: what GitHub returns for a clean Dependabot roll."""

CLEAN_COMMIT = {"sha": "a" * 40, "author": {"login": "dependabot[bot]"}, "committer": {"login": "web-flow"},
                "commit": {"verification": {"verified": True}}}
RULES = [{"type": "required_status_checks",
          "parameters": {"required_status_checks": [{"context": "qq-gate", "integration_id": 424242}]}}]
NPM_PATCH = '@@ -1,3 +1,3 @@\n   "dependencies": {\n-    "next": "^16.3.7",\n+    "next": "^16.3.8",\n'
LOCK_PATCH = "@@ -10,2 +10,2 @@\n-  next@16.3.7:\n-    resolution: {integrity: sha512-old}\n" \
             "+  next@16.3.8:\n+    resolution: {integrity: sha512-new}\n"
PIP_PATCH = "@@ -1 +1 @@\n-requests>=2.32.0\n+requests>=2.33.1\n"


def npm_file(name="package.json", patch=NPM_PATCH, **kw):
    return {"filename": name, "status": "modified", "patch": patch, **kw}
HEAD_RULES = [{"type": "update", "parameters": {}}, {"type": "non_fast_forward"}]
