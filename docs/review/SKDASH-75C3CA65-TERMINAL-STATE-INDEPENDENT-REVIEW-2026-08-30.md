# Terminal protected-read state independent review

Card: `75c3ca65`

Verdict: `PASS`

Reviewer: `pi-codex-chiap08-75c3ca65`

Producer: `codex-skdashboard-live-smoke-repair`

The reviewer identity is distinct from the producer identity. This review used an isolated card-specific worktree and did not alter candidate source, deploy, restart, or mutate runtime state.

## Candidate identity

- Commit: `996c5defbbad2c113f66579d16c40d87a10ebb65`
- Tree: `9bd6812e3982a666934624ad56fedac59f36b06d`
- Parent: `e08d9df73d0cec9ea705b422c5f532ec6543e5e8`
- Full-index binary diff SHA256: `f3085cb9bad310f0fcc82ec0a5493cb9818960deeec9fd0a01f4f6fe7741f18e`
- Producer evidence SHA256: `2be684dce87d2fe5d2e5fdae9ea70d68111cb471c8258f2d3ae3ef366354402a`

Exact six-path scope and candidate file SHA256 values:

- `src/skdashboard/static/js/architecture.js`: `90b518ad3fb361049036c815f67c45f5d4275494f31587902e2a7136592337e4`
- `src/skdashboard/static/js/governance.js`: `71f6e1da7a9aae12ce57eb444bb649fe97641e86b8295e4fd2caa1ebfb8c92eb`
- `src/skdashboard/static/js/reliability.js`: `b58c6f6ee27247a884554c08834c9a3e3abfaa7cb92e07b682f61f5d68717ca9`
- `src/skdashboard/static/js/reports.js`: `8a44358dc0a62b5bd6f47dde92e9e90f5864c2eb262beacc21e57c49b84b90d0`
- `src/skdashboard/static/js/schedule.js`: `80dd269a7b17cdd96d9b2cfdb06bebc6e1fc9f10f85a1c030c8893d4d361628d`
- `tests/test_control_plane_terminal_error_states.py`: `68c204ddbee071033a572492bb882c6fa7b5e5cfd2b5b8dbfde22f93fc01bc1c`

## Terminal error behavior

All five protected workspaces route every `getJSON` rejection through `renderUnavailable`. This includes HTTP 401 and every other non-OK HTTP response because `getJSON` throws for any non-OK status. Invalid or protected query contexts call the same renderer.

An independent executable DOM harness initialized every element whose initial HTML value contains `Loading`, called each candidate renderer with a simulated protected-read 401, and found zero stale loading values:

- Architecture: 8 initial regions, 0 stale.
- Governance: 10 initial regions, 0 stale.
- Reliability: 7 initial regions, 0 stale.
- Reports: 6 initial regions, 0 stale.
- Schedule: 5 initial regions, 0 stale.

Every replacement is an explicit `Unavailable` or `No ... value is inferred` state. The renderers also clear prior projections where a workspace retains one. The focused contract test verifies the catch routing and all required renderer targets.

## Reproduced gates

- Focused terminal-state and workspace tests: `29 passed, 2 warnings in 2.11s`.
- Ruff repository check: `All checks passed!`.
- Ruff format check for the candidate test: `1 file already formatted`.
- Python compileall for `src` and `tests`: passed.
- Changed-file detect-secrets scan: six paths supplied, zero findings.
- `git diff --check`: passed.
- U+2013 and U+2014 scan across all six paths: zero findings.
- Candidate full-index binary diff hash: reproduced exactly.
- Producer evidence hash: reproduced exactly.
- Candidate worktree before and after tests: no tracked or untracked change.

## Boundaries

No candidate source or runtime mutation, deployment, restart, live gateway access, configuration change, credential operation, protected-data read, merge, or main-branch push occurred. Rollback of the reviewed candidate remains a normal revert of commit `996c5defbbad2c113f66579d16c40d87a10ebb65`.
