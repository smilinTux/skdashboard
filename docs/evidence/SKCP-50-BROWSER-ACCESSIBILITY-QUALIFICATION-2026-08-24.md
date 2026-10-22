# SKCP-50 browser accessibility qualification — BLOCKED

Card: `83a8c40b`

Verdict: **FAIL / not complete.** The required WCAG 2.2 AA landmark criterion is not met, so the card must not be completed.

## Exact blocker

Real Chrome 151 found **zero `main` landmarks in both the DOM and accessibility tree on all seven qualified control-plane surfaces**: Now, Portfolio, Reliability, Architecture, AI outcomes, Governance, and Reports. Each page exposes the product brand as its only `h1`; the workspace page title is an `h2`. Navigation landmarks exist and keyboard Tab focus is visibly outlined, but the missing main landmark prevents a WCAG 2.2 AA pass and fails the card criterion requiring correct landmarks.

Durable reproducer and result:

```text
PYTHONPATH=$HOME/work/capauth/src:$PWD/src \
SKCP50_ARTIFACT_DIR=$PWD/docs/evidence/artifacts/SKCP-50-2026-08-24 \
node scripts/qualify_control_plane_accessibility_cdp.mjs

result: FAIL
7/7 pages: mainLandmarks=0, axMain=0
7/7 pages: navigationLandmarks=1, axNavigation=1, focusVisible=true
non-GET requests=0, external HTTP requests=0, runtime exceptions=0
```

Artifacts:

- `docs/evidence/artifacts/SKCP-50-2026-08-24/accessibility-landmark-matrix.json`
- `docs/evidence/artifacts/SKCP-50-2026-08-24/now-desktop.png`
- `docs/evidence/artifacts/SKCP-50-2026-08-24/run.log`
- `docs/evidence/artifacts/SKCP-50-2026-08-24/timing.tsv`
- `docs/evidence/artifacts/SKCP-50-2026-08-24/SHA256SUMS`

The screenshot is a local public-synthetic/unprivileged page state. No protected response data or credential is present.

## Environment and execution identity

- Host: `chiap08` WSL/Linux.
- Exact repository revision: `9b2cbff7204d3e76a69f4bf21e9bf1de8ec04ef5` (`v0.1.73`, `origin/main` at task start).
- Working tree: `/home/skuser01/worktrees/skdashboard-83a8c40b-browser-qual`.
- Branch: `pi/83a8c40b-browser-qual`.
- Python: `3.12.3`.
- Browser: Google Chrome `151.0.7922.173`; CDP user agent reports HeadlessChrome `151.0.0.0`.
- Node: `v22.23.2`.
- Corrected bootstrap/test path: `$HOME/work/capauth/src:$HOME/work/skcoord/src:$HOME/work/skcapstone/src:$PWD/src`; the final hermetic browser reproducer narrows this to `$HOME/work/capauth/src:$PWD/src` so it uses installed released SKCoord while overriding the contaminated editable CapAuth path.
- Model route: provider `skgateway`, model `sk-codex`, reasoning `high` (`PI_PROVIDER`, `PI_MODEL`, `PI_REASONING_LEVEL`).
- tmux: session `sk-parallel-rebase-20260824`, window `2:skdashboard`, pane `%4`.
- Startup log: `/home/skuser01/.skcapstone/runtime/sk-parallel-rebase-20260824-pane-4.log`.

The first bootstrap inherited `PYTHONPATH=/home/skuser01/work/capauth/src` and failed because installed SKCoord resolved CapAuth from `/tmp/sklegal-f080-clean/vendor/capauth/src`. Bootstrap was rerun successfully for coordination with the corrected path above. No applicable `AGENTS.md` exists in this repository or its ancestor path.

## Dependency evidence read

All exact card dependencies were folded as done and their linked repository evidence was read before qualification:

- `c6828b8a` SKCP-13 — `docs/evidence/SKCP-13-NOW-WORKSPACE-2026-08-24.md`
- `b7ada8b9` SKCP-20 — `docs/evidence/SKCP-20-UNIFIED-SCOPE-2026-08-24.md`
- `5ee56779` SKCP-21 — `docs/evidence/SKCP-21-PORTFOLIO-WORKSPACE-2026-08-24.md`
- `da097cbb` SKCP-22 — `docs/evidence/SKCP-22-ITIL-SRE-RELIABILITY-2026-08-24.md`
- `866ffaac` SKCP-23 — `docs/evidence/SKCP-23-DORA-ARCHITECTURE-CMDB-2026-08-24.md`
- `77d6bae0` SKCP-24 — `docs/evidence/SKCP-24-AI-OUTCOME-WORKSPACE-2026-08-24.md`
- `b548a77a` SKCP-25 — `docs/evidence/SKCP-25-GOVERNANCE-LINEAGE-2026-08-24.md`

The implementation commits linked by those cards are ancestors of the tested revision.

## Other qualification observations

Existing real-browser lanes passed for Reliability, Architecture, Governance, Reports, authorization preview, and the 11-surface light/dark navigation contrast matrix. Those lanes cover keyboard role changes, Architecture exception-to-CI evidence and focus return, 390/320 CSS-pixel containment, explicit Unknown/unavailable/non-color text, fail-closed authorization states, and zero writes/external requests/runtime exceptions in their synthetic state.

The Now, Portfolio, and AI scripts are not hermetic under the host's editable-package contamination as committed: they replace `PYTHONPATH` with only the repository `src`, which resolves stale CapAuth `0.3.1` from `/tmp/sklegal-f080-clean`; Now also needs released `skcoord.authorized_card_snapshot`. A process-local corrected debug run showed AI passes its keyboard/focus, accessible names, light/dark contrast, reduced motion, 390/320 layout, and protected-DOM purge checks. This environment limitation does not supersede the independently reproduced landmark failure.

Task interaction budgets already evidenced by dependency qualifiers:

- KPI/signal to evidence: one activation on Now and Portfolio.
- Architecture exception to bounded CI detail: one activation (within the two-interaction maximum).
- Authorization preview state inspection: one activation; action remains synthetic and dispatches nothing.

No honest end-to-end common-task timing claim is made after the landmark gate failed. The durable failing matrix itself completed in the linked timing artifact; stop-on-first-material-WCAG-failure avoided creating misleading completion metrics.

## Acceptance disposition

1. Keyboard completion, visible focus, names, and landmarks: **FAIL** — visible focus and names observed, but every named surface lacks a main landmark.
2. State communication without color: **partial evidence only** — literal state text exists for current, stale, partial, unavailable, unknown, not applicable, estimated, and forecast boundaries; no completion claim after criterion 1 failed.
3. Interaction counts: **pass in the qualified dependency task set**, as listed above.
4. Mobile/tablet/desktop/zoom/reduced-motion screenshots: **not complete** — existing 390/320 and reduced-motion lanes pass, but the card cannot claim the full approved matrix after the landmark gate failed.
5. WCAG 2.2 AA, task time, click count, limitations: **FAIL overall** — exact WCAG blocker and limitations are linked; completion is prohibited.

## Limitations, rollback, and safety

This was local synthetic qualification only. It did not deploy, restart a service, use protected data, add a dependency, mutate owner records, commit, merge, push, or touch another worktree. Local Uvicorn and headless Chrome child processes were terminated by each qualifier.

Rollback of this qualification-only change is deletion of:

- `scripts/qualify_control_plane_accessibility_cdp.mjs`
- `docs/evidence/SKCP-50-BROWSER-ACCESSIBILITY-QUALIFICATION-2026-08-24.md`
- `docs/evidence/artifacts/SKCP-50-2026-08-24/`

No migration or runtime rollback is required. Product remediation is intentionally not included in this exact qualification card; a follow-up should wrap each workspace content region in one `<main>` (or equivalent `role="main"`) and promote the unique workspace title to the page-level heading, then rerun the full matrix.
