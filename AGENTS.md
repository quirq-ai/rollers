# Agent guide

How an agent changes this repo safely. Read `README.md` first.

- Every change is a pull request, titled with its work item id (for example `V0-ROL-01: ...`).
  It lands only when the `presubmit` check is green on the merge result.
- Manifests (`infra/repo.toml`) are read and edited only through `qqsync`. Never parse them here.
- Roller settings come from `quirq-ai/infra-config` (`config/rollers.toml`). Don't hardcode them.
- `.github/CODEOWNERS` names suraj (`@sharmasuraj0123`) as owner of the policy and trust paths,
  including the code privileged workflows run; owner names are his call, so never change them. Leave any other `owners` list empty.
- Mark a decision you cannot make with a one-line `TODO(suraj):` or `TODO(expert):`.
- This repo is public: no secrets, tokens or internal hostnames.
- GitHub-specific code stays behind a `backend` field (`github` now, `launchpad` later).
- Use other qq repos by pinned commit, never by copying their code.
