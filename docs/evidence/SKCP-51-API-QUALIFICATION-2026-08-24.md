# SKCP-51 API qualification — BLOCKED

Date: 2026-08-24  
Card: `2d02b6ed`  
Candidate: `9b2cbff7204d3e76a69f4bf21e9bf1de8ec04ef5` (`v0.1.73`, `origin/main` at start)  
Verdict: **BLOCKED — do not complete**

## Scope and safety

This was an in-process, read-only qualification using the existing Starlette,
HTTPX/TestClient, pytest, public-synthetic fixture, and Python standard-library
resource tooling. No service was deployed or restarted. No network owner,
production, Tenant, Matter, HammerTime Inbox, prompt, response, credential, or
other protected data was read. No dependency was added. No commit, merge, push,
or other-worktree cleanup was performed.

No applicable `AGENTS.md` exists at or above this worktree. The search did find
files in sibling worktrees; those are not applicable and were not treated as
instructions.

## Dependency evidence read

All direct dependencies are folded `done`:

| Dependency | Evidence read |
|---|---|
| `d12b8951` — versioned read API | card core; board state; PR 47; merge `dd62165a8326006288bfd46c0bbdbfbe79aa434e`; linked result `343 passed; ruff passed; CI all green` |
| `804f14de` — bounded estate adapters | card core; board state; PR 50; merge `dd5bd937c427a64a64bce45737b9f0c34bac6ee4`; release `v0.1.44`; linked focused/full/Ruff/CI evidence and limitations |
| `38731952` — immutable reports | card core; board state; PR 78; merge `844a75bc5b4670033951c55fd51d07242fa37497`; release `v0.1.72`; `docs/evidence/SKCP-32-IMMUTABLE-REPORTS-2026-08-24.md`; linked CI/publish/tests/limits/rollback |

## Exact environment and load

- Host: `chiap08`, Linux `7.0.0-30-generic`, Python `3.12.3`.
- CPU: Intel Core i9-12900K, 24 logical CPUs.
- Model route: `skgateway/sk-codex`, reasoning `high`.
- CWD: `/home/skuser01/worktrees/skdashboard-2d02b6ed-api-qual`.
- tmux pane: `%5` in `sk-parallel-rebase-20260824:skdashboard`.
- Corrected bootstrap `PYTHONPATH`: `/home/skuser01/work/capauth/src`.
- Corrected bootstrap log: `/tmp/2d02b6ed-sk-bootstrap-corrected.log`.
- Startup log: `/home/skuser01/.skcapstone/runtime/sk-parallel-rebase-20260824-pane-5.log`.
- Fixture: `tests/fixtures/control_plane_full_estate.v1.0.0.json`, SHA-256
  `926f0374b8f32e64d0f370f9a6197b848a95a3403b8d497c5c67848e77fb821d`,
  classification `public`, `synthetic=true`, 16 distinct adapter populations;
  associated metric pack contains 17 cases.
- Load mode: in-process sequential TestClient, warm cache where explicitly
  stated. Overview: 10 warm-up + 100 measured reads. Conditional overview: one
  baseline + 100 measured `If-None-Match` reads. Health: 10 warm-up + 100
  measured reads. Additional probes: 10,000 synthetic board items with
  `limit=1`; 120 common reads followed by health; fresh/resume/three reconnect
  event requests; each typed owner-failure class; one real 1,000 ms subprocess
  timeout.

Raw machine-readable output, including exact status counts, CPU time, latency,
resource high-water mark, and blocker facts:
`docs/evidence/SKCP-51-API-QUALIFICATION-RAW-2026-08-24.json`.

## Measurements

These are observations, **not budget passes**: no approved numeric latency,
throughput, CPU, or memory threshold was found in the card, contracts,
architecture, planning, tests, or dependency evidence. The planning document
only says that latency budgets must pass; it does not define them.

| Workload | Requests | Status/errors | p50 | p95 | p99 | Max | Throughput |
|---|---:|---|---:|---:|---:|---:|---:|
| Full-estate overview | 100 | 100×200, 0 errors | 3.390 ms | 13.257 ms | 16.298 ms | 27.461 ms | 197.939 req/s |
| Conditional overview | 100 | 100×304, 0 errors | 2.777 ms | 3.415 ms | 3.638 ms | 3.655 ms | 358.820 req/s |
| Health | 100 | 100×200, 0 errors | 1.668 ms | 2.598 ms | 2.759 ms | 3.736 ms | 550.242 req/s |

The Python qualification process reported peak RSS `57,888 KiB`. Measured CPU
was 0.558 s for overview, 0.310 s for conditional overview, and 0.185 s for
health. Host-wide CPU/RSS deltas were not claimed because the in-process test
runner shares the process with fixtures and measurement code.

## Semantic results

### Passed observations

- ETag stability and source invalidation: unchanged projections returned 304;
  changing one owner to timeout returned 200 with a different ETag.
- Cursor shape and response page bound: `limit=1` returned one item, opaque
  `djE6MQ`, and `has_more=true`; limits outside 1..200 and malformed/oversized
  cursors are covered by focused tests.
- Rate response shape: the 121st request in the existing focused test returns
  429 `RATE_LIMITED` with `Retry-After: 60`.
- Timeout: declared owner budget 1,000 ms; observed subprocess timeout
  1,001.458 ms and under the existing 1,400 ms guard.
- Bounded typed owner failures remained visible: timeout →
  `unavailable/SOURCE_TIMEOUT`; unreachable →
  `unreachable/SOURCE_UNREACHABLE`; unauthorized →
  `unknown/SOURCE_UNAUTHORIZED`; unavailable →
  `unavailable/SOURCE_UNAVAILABLE`.
- Stream requests returned heartbeat; a valid `Last-Event-ID` returned the
  explicit `reset-required` event; reconnects returned a heartbeat without an
  HTTP error.
- Focused qualification/regression selection: **51 passed**, 4 existing
  `jsonschema.RefResolver` deprecation warnings, in 3.25 s.

### Exact blockers

1. **No approved numeric budget.** Acceptance criterion 1 requires approved
   latency and resource budgets, but no numeric target exists. Measurements
   cannot truthfully be called a pass.
2. **No workload isolation path.** The production app returns 404 for
   `/api/v1/insights/query`; report creation/generation is deliberately offline;
   no queue, semaphore, executor partition, or admission class exists to prove
   that insight concurrency or report generation cannot starve health,
   evidence, or common reads.
3. **Health is not isolated from common-read rate pressure.** The limiter is one
   per-client bucket shared by every route in the `routes()` closure. After 120
   board reads, health returned 429. A common workload can therefore starve
   health for that client identity.
4. **Pagination does not bound the owner scan.** The board handler materializes
   and maps the complete owner result before `_page` slices it. A synthetic
   `limit=1` request read and mapped all 10,000 owner items. This violates the
   criterion that owner failure/load never causes an unbounded request scan.
5. **Resume is reset-only, not resumable.** The event implementation has no
   event IDs, replay buffer, or bounded buffer. Every syntactically valid resume
   cursor emits `reset-required`, then one heartbeat, then closes. Fresh and
   reconnect requests likewise emit one heartbeat and close. Thus reset and
   reconnect syntax is present, but SSE resume/backpressure semantics are not
   implemented as required by the contract/ADR.
6. **No server projection cache.** Conditional ETag requests recompute the full
   projection and hash before returning 304. Source-driven ETag invalidation
   works, but there is no bounded cache, cache hit policy, TTL, or invalidation
   mechanism to qualify.

Because these are product/contract gaps rather than missing test assertions,
adding a larger benchmark or more test dependencies would not make the criteria
pass. No production code was changed under this qualification card.

## Tests executed

```text
PYTHONPATH=/home/skuser01/work/capauth/src \
python -m pytest -q \
  tests/test_control_plane_read_api.py \
  tests/test_control_plane_adapters.py \
  tests/test_control_plane_client.py \
  tests/test_control_plane_full_estate_fixture.py \
  tests/test_control_plane_report_provider.py \
  tests/test_control_plane_reports.py

51 passed, 4 warnings in 3.25s
```

The load/semantic probe is preserved as raw evidence rather than added as a
passing regression test because its required outcome is currently BLOCKED.

## Limitations and rollback

This is same-process synthetic qualification, not socket/TLS, multi-process,
multi-host, deployed-service, production-owner, or sustained soak evidence. It
measures framework and application behavior on one host and does not estimate
network latency. It uses no protected data and does not claim live owner or
production capacity.

Rollback for this card is deletion of only:

- `docs/evidence/SKCP-51-API-QUALIFICATION-2026-08-24.md`
- `docs/evidence/SKCP-51-API-QUALIFICATION-RAW-2026-08-24.json`

No application, dependency, schema, owner data, service, deployment, or other
worktree state changed.
