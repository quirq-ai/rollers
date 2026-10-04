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

## Develop

```sh
python -m pip install -e ".[test]"
python -m pytest -q
```

## v0 status

| Item | What | PR | State |
|---|---|---|---|
| V0-ROL-02 | Lockfile updates (Dependabot config from `rollers.toml`) | #2 | in review |
| V0-ROL-01 | Toolchain pin roller | | not started |

Plan and every v0 item: `quirq-ai/infra-config`, `docs/plan.md` and `docs/v0.md`.
