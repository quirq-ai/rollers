# rollers

`rollers` moves pinned dependencies and toolchains forward for quirq infra (`qq`), through the same
gate as any other change, with no human in the loop. An agent may land a clean roll alone (plan D4).

## Chromium counterpart

Skia AutoRoll, and `roll-dep` in depot_tools. AutoRoll and similar rollers write about a fifth of
Chromium's commits; here they do the same job for every repo quirq infra builds.

## What it rolls

| Roller | Moves | How |
|---|---|---|
| Toolchain pins (V0-ROL-01) | `[toolchains.*]` pins in each repo's `infra/repo.toml` | Reads the digests `quirq-ai/toolchains` promoted and edits the manifest only through `sync` (`qqsync`) |
| Lockfiles (V0-ROL-02) | Python requirements, `pnpm-lock.yaml` | Dependabot, configured from `rollers.toml` in `quirq-ai/infra-config` |

Which repos get which roller, and how often, is config: `config/rollers.toml` in
`quirq-ai/infra-config`, read with `qqcfg.load`.

## Lockfile updates: `qqroll dependabot` (V0-ROL-02)

Every repo that a `tool = "dependabot"` roller covers gets two generated files, kept under
`generated/github/<repo>/` and copied into that repo as is:

- `.github/dependabot.yml`: one update per roller, with its ecosystem, directory, cadence and open limit.
- `.github/workflows/qq-roll-land.yml`: lands a clean Dependabot PR with no human. Clean means only
  dependency files changed, at the roller's directory (requirements files and `pyproject.toml` for pip;
  `package.json`, `pnpm-lock.yaml` and `pnpm-workspace.yaml` for npm), every commit is Dependabot's
  and verified, and the bump is not a major version. The workflow turns on auto-merge, so GitHub merges
  the PR only once the required checks pass: the roll goes through the same gate as any change.
  Anything else, including any failed step, turns auto-merge off and leaves the PR to a human.

```sh
qqroll dependabot --infra-config ../infra-config --write   # regenerate generated/
qqroll dependabot --infra-config ../infra-config           # check: exit 1 if stale (presubmit runs this)
```

The infra-config commit the files come from is pinned in `infra-config.commit` and recorded in each
file's header. Admin steps the land workflow needs in each repo: "Allow auto-merge", and a ruleset
that requires the gate check on `main` (V0-ORG-03). xo-space has no Python lockfile yet, so its pip
rolls only move `requirements*.txt` floors, and may open no PR at all (`TODO(expert)` in
`rollers.toml`); the done-when is shown on innernet first.

## Toolchain pins: `qqroll roll` and `qqroll rotation` (V0-ROL-01)

`quirq-ai/toolchains` records each promoted toolchain by digest in `promoted.toml` (V0-TCH-03). The
roller reads it from a checkout of toolchains `main`, only through toolchains' own parser
(`tools/qqtc.py`, `load_promoted`), and moves every repo's `[toolchains.*]` pins in `infra/repo.toml`
to those digests in sync's `oci://` form: `source` names the image manifest by digest and `digest` is
its one toolchain layer. The manifest is edited only through `qqsync`, so a roll changes just the pin
and its `version` label and leaves every other byte alone.

- Only per-platform toolchain pins a manifest already has, from the image the promotion names
  (registry and repository), are moved; a roller never adds a toolchain. A pin for every platform is
  left alone, because each promoted image is for one platform. The toolchain's `version` label moves
  only when every platform ends up at that version. A promotion older than the label is not rolled.
- A roll stays inside the org-wide pin in infra-config `kinds.toml` (python `3.14`, node `24`). A new
  minor or major means moving that pin first, which is a policy change for suraj.
- Any pin left alone is reported and makes the command exit 1: a skipped pin needs a person and is
  never reported as current.
- Before `qqroll rotation` writes a roll, it checks each new pin again, failing closed, with the same
  checks as toolchains' promotion gate: the manifest fetched with `oras` hashes to the pinned digest
  and is exactly one toolchain layer, the pinned one; and `gh attestation verify` finds build
  provenance from toolchains' `.github/workflows/build.yml` on `main`, at the pin's `built_from`. A
  repo with a pin that fails either check is not rolled.
- `qqroll roll` edits one local manifest; it is the core that v1's `qq roll` reuses, so both make the
  same diffs. It does not run the registry checks.
- `qqroll rotation` reads `rollers.toml` (the `quirq-rollers` roller named `toolchains`), fetches each
  listed repo's manifest from its default branch, and opens or refreshes one PR on the branch
  `qq-roll/toolchains`. Repos with no manifest yet are skipped. GitHub calls sit behind
  `qqroll.backends` (`github` now, `launchpad` later); repo slugs come from infra-config `repos.toml`.

```sh
qqroll roll --toolchains ../toolchains --manifest infra/repo.toml --infra-config ../infra-config
qqroll rotation --infra-config ../infra-config --toolchains ../toolchains                 # dry run: diffs only
QQ_ROLLER_TOKEN=... qqroll rotation --infra-config ../infra-config --toolchains ../toolchains --apply
```

`.github/workflows/roll-toolchains.yml` runs the rotation weekly (the `rollers.toml` cadence), on
`repository_dispatch` (`toolchain-promoted`) and by hand. Roll PRs need the quirq infra bot (a GitHub
App), because PRs opened with a workflow's `GITHUB_TOKEN` trigger no workflows; without its secrets the
workflow is a dry run. A branch that already holds the same roll is not pushed again. The registry
checks read ghcr anonymously, so until the toolchain packages are public every roll fails closed. Auto-merge (`--auto-merge`) stays off until `toolchains` enforces review of
promotions on its `main`, so for now a roll stops at an open PR.

## Develop

```sh
python -m pip install -e ".[test]"
python -m pytest -q
# tests that read toolchains' parser or sync's fixture need checkouts (presubmit pins both):
QQ_TOOLCHAINS=../toolchains QQ_SYNC=../sync python -m pytest -q
```

## v0 status

| Item | What | PR | State |
|---|---|---|---|
| V0-ROL-02 | Lockfile updates (Dependabot config from `rollers.toml`) | #2; delivery xo-space#213, innernet#38 | merged here; delivery PRs wait on suraj, then the done-when on V0-ORG-03 (merge queue, auto-merge setting) |
| V0-ROL-01 | Toolchain pin roller | #3 | merged; done-when waits on V0-ONB-01 (manifests), V0-ORG-03, the quirq infra bot App, and toolchains enforcing promotion review (auto-merge held off until then) |

Plan and every v0 item: `quirq-ai/infra-config`, `docs/plan.md` and `docs/v0.md`.
