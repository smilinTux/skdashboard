# SKDashboard 0.1.89 source provenance (card e2a2e808, AC-1)

Resolved 2026-08-30 by pi-qwen-chiap04-e2a2e808. This record pins the exact
source bytes the installed package was cut from, so the chiap04 checkout
(dirty branch `codex/554ade2a-cmdb-scheduler-health` in
`/home/skuser01/work/skdashboard`) is not used as the source of truth.

## Identity chain

- Git remote: `origin` = `https://github.com/smilinTux/skdashboard` (the publish
  remote; a second remote `jarvis` = `https://github.com/jarvis1openclaw/skdashboard`
  is the mirror used by the fleet).
- Release tag: `v0.1.89` (annotated tag object `949e2152fa42723b4e97129438a210709b8241e7`,
  peels to commit `640bb42872537be5e4eff3e671d9db75447c904d`).
- Commit: `640bb42872537be5e4eff3e671d9db75447c904d`
  "fix(ci): use qualified CapAuth revision" (2026-08-30 12:35:56 -0500).
- Source tree for the package: `a705c5c4f8fa4b45433f0c9e10e5b3718d119e79`
  (tree `src/skdashboard` at tag v0.1.89).

## Release artifact

- Version derivation: `pyproject.toml` uses `setuptools_scm`; the version is the
  newest git tag. Tag `v0.1.89` therefore IS version `0.1.89`.
- Published artifact: `skdashboard-0.1.89.tar.gz` / `.whl` on PyPI (project page
  https://pypi.org/project/skdashboard/), built by `.github/workflows/publish.yml`
  after CI on the tag commit.

## Deployed package

- Installed into the shared venv: `/home/skuser01/.skenv/lib/python3.12/site-packages/skdashboard`
  currently reports version `0.1.90` (pip metadata), i.e. the deploy is one patch
  newer than the pinned 0.1.89 provenance. The diff between v0.1.89 and
  `origin/main` is exactly 10 added lines in
  `src/skdashboard/control_plane_api.py` (defaulting a bare
  `/api/v1/schedule/projection` request), committed at
  `79d8c47632b309addb1edb08c104359d888d8fc1` "fix(schedule): default bare
  projection requests".
- This worktree is branched from `origin/main` (`79d8c47`), so the candidate is
  built on current main, with the v0.1.89 provenance pinned above as the "source
  provenance" oracle, and the dirty chiap04 checkout explicitly NOT used as
  source.

## Why not the chiap04 checkout

`/home/skuser01/work/skdashboard` sits on branch
`codex/554ade2a-cmdb-scheduler-health` at commit `022a458` with modified
`CHANGELOG.md`, `SOP.md`, `dashboard_cmdb.py` and `test_cmdb_operations.py`
(dirty). That checkout predates current control-plane modules
(`dashboard_architecture.py`, `dashboard_schedule.py`, `dashboard_reports.py`,
`dashboard_governance.py`, `dashboard_skcounter.py`, `control_plane_*`). All panel
work in this card therefore happens in a clean worktree on `origin/main` and is
pinned against the v0.1.89 tree `a705c5c` above, not against that dirty checkout.
