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
- `.github/workflows/qq-roll-land.yml`: lands a clean Dependabot PR with no human. The check is
  [`src/qqroll/land_check.py`](src/qqroll/land_check.py), embedded in the workflow byte for byte. A
  roll is clean only when all of these hold:
  - Dependabot opened the PR and triggered this run, and the PR has exactly one commit, made by GitHub
    for Dependabot (committer `web-flow`, verified). Author emails can be forged; these can't.
  - Every changed file is *modified* (no adds, deletes or renames), at most 20, and is a dependency
    file at the roller's directory: `requirements*.txt` for pip, `package.json` and `pnpm-lock.yaml`
    for npm.
  - Every changed line is a version: existing dependency entries in `package.json` rewritten in place
    (no entries added or removed), plain pins in requirements files (no index options, URLs or
    markers), and registry-only lockfile entries.
  - It bumps one dependency by a patch or minor version. Below 1.0 a minor bump counts as major, below
    0.1 a patch bump does too, and an unknown previous version counts as 0.x.
  - The base branch has a gate a workflow cannot fake: a required workflow (ruleset) pinned by sha, or
    a required check bound to an app other than GitHub Actions. Any workflow's `GITHUB_TOKEN` can
    create a check run owned by GitHub Actions, so a check bound to it does not count.
  - The PR branch has `update` and `non_fast_forward` rules, so nobody can swap its commit after the
    check while auto-merge is on.

  The workflow then turns on auto-merge, so GitHub merges the PR only once the required checks pass.
  Anything else, including any failed step, turns auto-merge off and leaves the PR to a human.

```sh
qqroll dependabot --infra-config ../infra-config --write   # regenerate generated/
qqroll dependabot --infra-config ../infra-config           # check: exit 1 if stale (presubmit runs this)
```

The infra-config commit the files come from is pinned in `infra-config.commit` and recorded in each
file's header. Admin steps the land workflow needs in each repo: Dependabot version updates turned on
(xo-space is a fork, so the config file alone doesn't enable them), "Allow auto-merge", a ruleset
that requires the gate on `main` as a required workflow or as a check bound to a non-Actions app
(V0-ORG-03), and a ruleset that restricts creating, updating and force-pushing `dependabot/**`
branches so only Dependabot writes them. Auto-merge stays on after this run, and GitHub turns it off
only for pushes by people without write access, so without that branch rule someone with write access
could swap the PR's commit after the check. The land check confirms that rule applies to the PR branch, but it
cannot read rulesets' bypass lists, so keeping Dependabot the only exempt actor is on the admin. xo-space has no Python lockfile yet, so its pip
rolls only move `requirements*.txt` floors, and may open no PR at all (`TODO(expert)` in
`rollers.toml`); the done-when is shown on innernet first.

## Toolchain pins: `qqroll roll` and `qqroll rotation` (V0-ROL-01)

`quirq-ai/toolchains` records each promoted toolchain by digest in `promoted.toml` (V0-TCH-03). The
roller reads it from a checkout of toolchains `main`, only through toolchains' own parser
(`qqtc promoted-changed`, run as its own isolated process). With `--apply` the rotation takes only
`--promoted-json`, made where no bot token exists (in CI, a separate job), and re-validates it. It
then moves every repo's `[toolchains.*]` pins in `infra/repo.toml`
to those digests in sync's `oci://` form: `source` names the image manifest by digest and `digest` is
its one toolchain layer. The manifest is edited only through `qqsync`, so a roll changes just the pin
and its `version` label and leaves every other byte alone.

- Only per-platform toolchain pins a manifest already has, from the image the promotion names
  (registry and repository), are moved; a roller never adds a toolchain. A pin for every platform is
  left alone, because each promoted image is for one platform. The toolchain's `version` label moves
  only when every platform ends up at that version. A promotion older than the label is not rolled,
  and a pin without a numeric version label is not rolled either, since a downgrade can't be ruled out.
- A roll stays inside the org-wide pin in infra-config `kinds.toml` (python `3.14`, node `24`). A new
  minor or major means moving that pin first, which is a policy change for suraj.
- Any pin left alone is reported and makes the command exit 1: a skipped pin needs a person and is
  never reported as current.
- Before `qqroll rotation` or `qqroll roll` writes a roll, it checks each new pin again, failing closed, with the same
  checks as toolchains' promotion gate: the manifest fetched with `oras` hashes to the pinned digest
  and is exactly one toolchain layer, the pinned one; and `gh attestation verify` finds build
  provenance from toolchains' `.github/workflows/build.yml` on `main`, at the pin's `built_from`. A
  repo with a pin that fails either check is not rolled.
- `qqroll roll` edits one local manifest; it is the core that v1's `qq roll` reuses, so both make the
  same diffs. `--unverified` skips the registry checks, for offline tests only.
- `qqroll rotation` reads `rollers.toml` (the `quirq-rollers` roller named `toolchains`), fetches each
  listed repo's manifest from its default branch, and opens or refreshes one PR on the branch
  `qq-roll/toolchains`. Repos with no manifest yet are skipped. GitHub calls sit behind
  `qqroll.backends` (`github` now, `launchpad` later); repo slugs come from infra-config `repos.toml`.

```sh
qqroll roll --toolchains ../toolchains --manifest infra/repo.toml --infra-config ../infra-config
qqroll rotation --infra-config ../infra-config --toolchains ../toolchains   # dry run: checks and diffs, opens nothing
QQ_ROLLER_TOKEN=... qqroll rotation --infra-config ../infra-config --toolchains ../toolchains --apply
```

`.github/workflows/roll-toolchains.yml` runs the rotation weekly (the `rollers.toml` cadence), on
`repository_dispatch` (`toolchain-promoted`) and by hand. Roll PRs need the quirq infra bot (a GitHub
App), because PRs opened with a workflow's `GITHUB_TOKEN` trigger no workflows; without its secrets the
workflow is a dry run. A branch that already holds the same roll is not pushed again. The registry
checks read ghcr anonymously, so until the toolchain packages are public every roll fails closed. Auto-merge (`--auto-merge`) stays off until `toolchains` enforces review of
promotions on its `main`, so for now a roll stops at an open PR.

The roll job runs only pinned actions and packages (the runner image aside): actions by commit sha, Python packages from
`requirements/roll.lock` by hash (`--require-hashes --no-deps`) plus qqsync and qqroll by commit, all
installed before the bot token is minted, and the checkout keeps no credentials. The token is scoped
to exactly the repos the toolchains roller covers (`qqroll repos`). With `QQ_ROLLER_TOKEN` set,
`qqroll` refuses `--toolchains` and takes only `--promoted-json`, so qqtc never runs beside the token, and oras and gh run without it.

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
