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

## Develop

```sh
python -m pip install -e ".[test]"
python -m pytest -q
```

## v0 status

| Item | What | PR | State |
|---|---|---|---|
| V0-ROL-02 | Lockfile updates (Dependabot config from `rollers.toml`) | | not started |
| V0-ROL-01 | Toolchain pin roller | | not started |

Plan and every v0 item: `quirq-ai/infra-config`, `docs/plan.md` and `docs/v0.md`.
