# SKCP-21R Portfolio workspace reconciliation

Date: 2026-08-28

Review card: `11fae2b7`

Source card: `5ee56779`

Verdict: `PASS`

## Review identity and scope

The fleet projection assigns card `11fae2b7` to `pi-codex-11fae2b7` on `chiap02`. This review used a card-specific worktree and branch, `chore/11fae2b7-portfolio-review`, created from `origin/main`. No product code, deployment, runtime, provider, protected Matter, external action, or unrelated state was modified.

The reviewed repository state is main commit `81590335e7c1c33358c185aa49437d7124a565d8`, tree `74ef6218d82cfccf5ffb434175eeb5d55809e95f`. The original SKCP-21 delivery is merged PR [#62](https://github.com/smilinTux/skdashboard/pull/62), merge commit `b0c9218bdc43c73a462c6f70e31a44a5759dd69a`, tree `2ce8a3abdbd3eeeea06001e8de88e408b72f8b4b`, and release `v0.1.56`.

The folded SKCP-21 dependencies are complete:

- `804f14de`, SKCP-12 bounded observation layer: `DONE`, PR [#50](https://github.com/smilinTux/skdashboard/pull/50), commit `dd5bd937c427a64a64bce45737b9f0c34bac6ee4`.
- `b7ada8b9`, SKCP-20 unified scope workspace: `DONE`, PR [#57](https://github.com/smilinTux/skdashboard/pull/57), main commit `5f3b6f68abcdab9e1fd7345cb4bf488e1d07a19f`.

Lifecycle state was used only to verify dependency gates. The PASS verdict below comes from separate implementation, test, and review evidence.

## Acceptance findings

### AC1: Implementation and tests are located

PASS.

Exact implementation:

- `src/skdashboard/static/projects.html`: read-only Portfolio workspace, measurement table, owner-record table, dependency table, milestone table, and explicit velocity boundary. SHA-256 at reviewed main: `6814e571dab77b74c59bb6ea6a95dd0d71de1fd6f6207841759033315fc11386`.
- `src/skdashboard/static/js/projects.js`: bounded projection rendering, metric definitions, unavailable states, owner records, dependency conditions, evidence dialog, and currentness handling. SHA-256: `e38fdced62945509d627f1e4bf99b488be35327d74e9004c46a80eec89e04812`.
- `src/skdashboard/control_plane_api.py`: protected overview and typed owner-record projection boundary.
- `tests/test_control_plane_project_workspace.py`: focused acceptance and anti-ranking checks. SHA-256: `cf43eb9d9a21d067a390a543348eae285efc9638d93e2cd3083f275aca60a82d`.
- `tests/test_control_plane_decision_context.py`: authorization context and owner-projection integration tests.
- `scripts/qualify_control_plane_portfolio_cdp.mjs`: browser qualification.
- `docs/evidence/SKCP-21-PORTFOLIO-WORKSPACE-2026-08-24.md`: original delivery evidence. SHA-256: `ce6e855a8a81e7f0516d97cc845283610d33c94861c3336d0d3e9946e361bd27`.

Fresh focused qualification on reviewed main:

```text
python -m pytest -q tests/test_control_plane_project_workspace.py tests/test_control_plane_decision_context.py
19 passed, 1 warning in 0.27s

node --check src/skdashboard/static/js/projects.js
PASS

node --check scripts/qualify_control_plane_portfolio_cdp.mjs
PASS
```

The original merged candidate evidence also records 420 passed, Ruff pass, JavaScript pass, Chrome 151 pass, and main CI pass. This review does not substitute those historical claims for the fresh focused run.

### AC2: Owner-record traceability remains intact

PASS.

`projects.js` derives objective, project, epic, task, decision, benefit, investment, risk, dependency, and milestone signals only from the typed `project_records` projection. It displays record ID, native kind, allowed classifications, status, priority, owner, relationship count, timestamps, and safe evidence reference. Missing typed populations remain unavailable or unknown. Titles, descriptions, raw events, metadata, links, and unapproved labels are explicitly excluded. No aggregate count is promoted into an owner-record fact.

### AC3: Metric boundaries and missing-evidence truth are explicit

PASS.

Every signal row has result and truth state, literal definition, sample or population, window, exclusions, source, and an evidence action. Historical measures including throughput, cycle percentiles, blocked time, flow efficiency, churn, rollover, decision latency, and forecast inputs remain `Unknown` when required events, samples, windows, or contracts are absent. Current done stock is explicitly not throughput. Current blocked stock is explicitly not blocked duration. Missing evidence is never rendered as zero or healthy. Workspace failure clears prior values and reports unavailable. Policy-filtered records remain outside the authorized population rather than being counted as zero.

### AC4: Dependency and milestone exceptions are visible

PASS.

The dependency table has explicit columns for stale, orphaned, conflicted, and human-gated states. Staleness distinguishes `stale` from `freshness_unknown`. Orphan state comes from unresolved authorized endpoints. Conflict and human-gate states require projected condition evidence and are not inferred from priority or blocked status. Milestone rows include owner, status, path IDs, classified path conditions, timestamps, and evidence.

### AC5: No individual ranking and table equivalents

PASS.

The Portfolio workspace contains no chart. All displayed measurements and summaries have table representations, so every chart has a table equivalent vacuously and all visualized data is directly tabular. The workspace states that velocity is local planning context only and forbids person or team ranking. Focused tests reject individual aggregation markers and verify that cards, tokens, cost, commits, and Joules are not investment proxies. Repository search found no owner-ranking implementation in the Portfolio module.

### AC6: Exact review verdict without unrelated mutation

PASS.

This document is the only repository change. The review performed read-only source inspection, local tests, JavaScript syntax checks, and coordination reads. It did not deploy, restart, mutate a live service, access protected Matter, call a provider, request external action, rank individuals, merge, or modify unrelated product state.

## Verdict

`PASS`

The current main implementation satisfies the exact SKCP-21 criteria and the additional SKCP-21R truth-state requirement. Both folded dependencies are complete. The original implementation, current source identity, focused tests, historical qualification, and criterion-by-criterion findings are linked above.
