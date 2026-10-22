# SKCP-51F1A exact API budget proposal - HUMAN DECISION REQUIRED

Date: 2026-08-25  
Preparation card: `2933ef14`  
Human decision card: `2b49789d`  
Failed source qualification: `2d02b6ed` (**BLOCKED; not a completed dependency**)  
Candidate measured by the source: `9b2cbff7204d3e76a69f4bf21e9bf1de8ec04ef5` (`v0.1.73`)  
Status of this artifact: **proposal only; neither approval nor rejection**

## 1. Scope, provenance, and integrity

This artifact converts measurements from the failed public-synthetic source
qualification into exact limits that an eligible human may approve or require
changes to on `2b49789d`. It does not cure or pass `2d02b6ed`. In particular,
the source found no approved budget, no bounded owner scan, no server projection
cache, no health-rate isolation, no implemented insight/report isolation, and
no resumable bounded SSE replay. Numeric performance inside the source run was
observational only.

| Item | Path / identity | SHA-256 |
|---|---|---|
| Failed source narrative | `docs/evidence/SKCP-51-API-QUALIFICATION-2026-08-24.md` | `b5c2f859b8230efab48cfb490f121b7c003e390e442c3948b7df986882def067` |
| Failed source raw JSON | `docs/evidence/SKCP-51-API-QUALIFICATION-RAW-2026-08-24.json` | `01c030231d70090c92e5790b7a12d052f1a52ab14bb32cfe3f20dce4f1edcadd` |
| Public-synthetic fixture | `tests/fixtures/control_plane_full_estate.v1.0.0.json` | `926f0374b8f32e64d0f370f9a6197b848a95a3403b8d497c5c67848e77fb821d` |
| Measured git tree | `9b2cbff7204d3e76a69f4bf21e9bf1de8ec04ef5` | Git object ID |

The hash of this proposal is recorded on card `2933ef14` after writing and is
not embedded here, avoiding a self-referential hash.

Preparation route and runtime: `skgateway/sk-codex`, reasoning `high`; host
`chiap08`; cwd
`/home/skuser01/worktrees/skdashboard-2d02b6ed-api-qual`; tmux
`sk-parallel-wave2-20260824:1:skdashboard`, pane `%14`; startup log
`/home/skuser01/.skcapstone/runtime/sk-parallel-wave2-20260824-dashboard-budget-proposal.log`.
The startup log is live and append-only during the session, so its final hash
must be captured by the coordinator after the process exits; a mid-session hash
would not be stable. The source qualification route was also
`skgateway/sk-codex`, reasoning `high`; its original measurement context was
pane `%5`, with the source paths and hashes above authoritative for its results.

No product code, service, deployment, protected data, dependency, commit,
merge, or push was changed by this preparation.

## 2. Observed source values - not limits and not passes

### 2.1 Observed hardware, process, dataset, and load

- Host/process: `chiap08`, Linux `7.0.0-30-generic`, Python `3.12.3`, Intel
  Core i9-12900K, 24 logical CPUs.
- Lane: one Python qualification process using Starlette HTTPX/TestClient,
  same-process/in-process, sequential concurrency `1`; warm state where named.
  This is not a socket, TLS, multiprocess, multihost, deployed-service, or
  production-owner measurement.
- Dataset: fixture hash above, classification `public`, `synthetic=true`, 16
  distinct adapter populations; associated metric pack 17 cases.
- Overview: 10 warm-ups, then 100 measured `GET /api/v1/overview` requests.
- Conditional overview: one baseline, then 100 measured `If-None-Match`
  requests; every measured response was 304, but each recomputed its projection.
- Health: 10 warm-ups, then 100 measured `GET /api/v1/health` requests.
- Additional one-shot semantic probes: 10,000 synthetic board owner items with
  `limit=1`; 120 board reads then health; fresh/resume/three reconnect event
  requests; four typed owner-failure classes; one real 1,000 ms subprocess
  timeout.

### 2.2 Observed latency, errors, throughput, CPU, and RSS

| Workload | Requests | p50 ms | p95 ms | p99 ms | max ms | Errors | Throughput req/s | Process CPU s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Warm full-estate overview | 100 | 3.390 | 13.257 | 16.298 | 27.461 | 0 | 197.939 | 0.558477 |
| Warm conditional overview, 304 | 100 | 2.777 | 3.415 | 3.638 | 3.655 | 0 | 358.820 | 0.309976 |
| Warm health | 100 | 1.668 | 2.598 | 2.759 | 3.736 | 0 | 550.242 | 0.185209 |

Observed process peak RSS was `57,888 KiB`. This includes the shared Python
test runner, fixture, app, and measurement code, so it is not an isolated app
RSS baseline. The three measured loops consumed `1.053662` aggregate process
CPU seconds. Owner timeout was declared `1,000 ms` and observed at `1,001.458
ms`; the existing test guard was `1,400 ms`.

Observed semantic failures relevant to the proposal:

- `limit=1` returned one item but materialized all `10,000` owner items.
- After 120 common board reads from one identity, health returned `429`.
- Every conditional 304 recomputed the full projection; there was no bounded
  server cache.
- `/api/v1/insights/query` returned `404`; no report generation route or
  worker-isolation mechanism was available to measure.
- SSE had no event IDs, replay buffer, or bounded buffer; resume was reset-only.

## 3. Proposed human limits - inactive unless card `2b49789d` approves them

### 3.1 Exact qualification envelope

The proposed limits apply only when **all** of the following are pinned in the
requalification evidence:

1. Public-synthetic fixture SHA-256
   `926f0374b8f32e64d0f370f9a6197b848a95a3403b8d497c5c67848e77fb821d`,
   exactly 16 adapter populations and 17 metric cases. No protected data.
2. Linux x86_64, CPython 3.12.x, one application process, at least 8 logical
   CPU cores, and no less than 8 GiB RAM available to the lane. CPU model,
   kernel, Python patch, app commit, dependency lock/hash, and process command
   must be recorded. The measured 24-core i9 host qualifies; slower qualifying
   hardware gets no relaxed limits.
3. The first approval covers the same in-process TestClient lane at concurrency
   `1`. A separate **required** contention lane runs the same request classes at
   fixed concurrency `8` (8 workers, at most 8 in flight) after the missing
   isolation paths exist. It is a stop condition if either lane is absent.
4. For each measured request class and lane: 10 untimed warm-ups followed by
   1,000 timed requests. Run five independent trials in fresh processes; use
   the worst trial for every gate. Record every raw duration, status, process
   CPU time, and peak RSS. Percentiles use nearest-rank order statistics
   (`ceil(p*n)`, 1-indexed); `max` is the largest sample. Use
   `time.perf_counter_ns`, `time.process_time_ns`, and
   `resource.getrusage(RUSAGE_SELF).ru_maxrss` on Linux.
5. Warm projection tests begin only after one successful unmeasured population.
   Cold projection tests use an empty server cache and fresh process for each of
   100 requests; their limits are in section 3.2. Conditional-hit tests require
   a real server cache hit counter to increase and zero owner calls. A 304
   obtained by recomputation is a cache miss and cannot enter the hit sample.
6. Error rate denominator is all measured requests. Only the expected status is
   success: 200 for overview/health, 304 for conditional hits. Any hidden,
   omitted, relabelled, or falsely healthy stale/partial/unavailable/unknown
   owner evidence is a semantic error and an immediate stop, regardless of
   latency.

### 3.2 Latency, throughput, error, CPU, RSS, and timeout limits

All rows are mandatory; `<=` applies to latency/error/CPU/RSS, `>=` to
throughput. The concurrency-8 rows are exact proposals but have no successful
source observations; they must be measured and may not be inferred to pass.

| Request class and condition | p50 | p95 | p99 | Max | Error rate | Throughput floor | CPU bound |
|---|---:|---:|---:|---:|---:|---:|---:|
| Overview, warm, concurrency 1 | 5 ms | 20 ms | 25 ms | 40 ms | 0.0% | 150 req/s | 0.75 CPU s / 100 requests |
| Overview, cold, concurrency 1 | 10 ms | 40 ms | 50 ms | 80 ms | 0.0% | 75 req/s | 1.50 CPU s / 100 requests |
| Conditional overview true cache hit, concurrency 1 | 4 ms | 5 ms | 6 ms | 8 ms | 0.0% | 300 req/s | 0.45 CPU s / 100 requests |
| Health, concurrency 1 | 3 ms | 4 ms | 5 ms | 8 ms | 0.0% | 450 req/s | 0.30 CPU s / 100 requests |
| Overview, warm, concurrency 8 | 10 ms | 40 ms | 50 ms | 80 ms | 0.0% | 75 req/s | 6.00 CPU s / 100 requests |
| Overview, cold, concurrency 8 | 20 ms | 80 ms | 100 ms | 160 ms | 0.0% | 35 req/s | 12.00 CPU s / 100 requests |
| Conditional overview true cache hit, concurrency 8 | 8 ms | 10 ms | 12 ms | 16 ms | 0.0% | 150 req/s | 3.60 CPU s / 100 requests |
| Health, concurrency 8 | 6 ms | 8 ms | 10 ms | 20 ms | 0.0% | 225 req/s | 2.40 CPU s / 100 requests |

Global process resource limits for either lane: peak RSS `<= 98,304 KiB` (96
MiB) in the qualification process and RSS growth from immediately after warm-up
to end of the 1,000-request sample `<= 8,192 KiB` (8 MiB). Aggregate process CPU
for each 100-request equivalent across the three warm sequential classes is
`<= 1.50 CPU s`. The RSS growth rule catches accumulation that a generous
absolute cap could hide.

Owner budgets: each owner timeout remains `1,000 ms`; timeout return/partial
projection must be observed `<= 1,100 ms` from invocation, while the enclosing
HTTP request must remain bounded by its explicitly approved fan-out strategy.
The current `1,400 ms` test guard may remain as a regression kill guard but is
not the proposed qualification limit. A timeout must preserve typed unavailable
or partial truth; it must never be converted to healthy or omitted.

### 3.3 Pagination work caps

For every paginated owner-backed endpoint:

- Accepted `limit` is exactly `1..200`; default `50`; response items `<= limit`.
- Owner records fetched/materialized/mapped per request `<= limit + 1`, including
  cursor look-ahead. Thus absolute owner-work cap is `201` records/request.
- At `limit=1`, owner records touched must be `<=2`, not 10,000.
- Cursor bytes after URL decoding `<=256`; malformed, stale, or oversized cursor
  is rejected before owner enumeration.
- Per-request temporary pagination allocation attributable to the page is
  `<=1 MiB` above its pre-page baseline.
- The 10,000-record public-synthetic sentinel remains mandatory and must show
  work independent of total owner cardinality.

### 3.4 Projection cache limits and invalidation

- Server projection cache maximum `256` entries and estimated aggregate payload
  bytes `<=16 MiB`; least-recently-used or equivalent deterministic eviction.
- Fresh TTL `<=5 s`; stale entries are never served as healthy. There is no
  stale-while-revalidate allowance in this proposal.
- Cache key must include route, API/schema version, authorized subject/scope,
  normalized query/scope, fixture/source revision, and every input that changes
  visible truth. Cross-subject or cross-scope reuse is forbidden.
- A true conditional hit performs `0` owner calls and increases an observable
  hit counter by exactly one. Source revision/change invalidates the affected
  entry before its next response; the next request must be a miss/200 with a
  changed ETag and visible changed truth.
- Cache effectiveness gate after one warm-up: `>=95%` hits in 1,000 unchanged
  conditional requests; `0` false hits; `0` stale-truth responses; eviction
  leaves entry and byte counters at or below both caps.

### 3.5 Rate and workload isolation expectations

- Health has a dedicated limiter/admission class, not a bucket shared with
  common reads. After 10,000 common-read attempts from the same client identity,
  100 interleaved health requests must return 100×200, with health p99 `<=10 ms`
  and max `<=20 ms` in the concurrency-8 contention lane.
- Evidence/control-plane reads likewise have admission separate from insight,
  report generation, and SSE. At least 2 of 8 execution slots are reserved for
  health/evidence/common reads; expensive workloads may use at most 6 slots.
  Queue caps: insight waiting `<=16`, report waiting `<=4`; excess receives 429
  or 503 with `Retry-After`, never an unbounded queue.
- Under 6 concurrent slow insight/report jobs, two reserved read slots issue
  exactly 1,000 reads in seeded shuffled order: 200 health (20%), 400 overview
  (40%), and 400 true conditional overview hits (40%), at no more than 2 reads
  in flight. Expected-status error rate is `0.0%`; health p99 `<=10 ms`/max
  `<=20 ms`; overview p99 `<=50 ms`/max `<=80 ms`; conditional-hit p99 `<=12
  ms`/max `<=16 ms`. Record the PRNG algorithm and seed. Typed owner failures
  and partial truth must remain visible.
- These isolation tests cannot pass while insight/report execution paths are
  absent. A 404 or omitted workload is a stop, not evidence of isolation.

## 4. Margin rationale

Each margin is deliberate and must not be read as an observed pass.

- **Warm overview latency:** proposed 5/20/25/40 ms exceeds observed
  3.390/13.257/16.298/27.461 by 1.610/6.743/8.702/12.539 ms, or
  47.5%/50.9%/53.4%/45.7%. Roughly 50% absorbs scheduler and interpreter jitter
  without normalizing a 2x regression.
- **Cold overview latency:** 10/40/50/80 ms is exactly 2x the warm proposal.
  There is no observed cold sample, so this conservative allowance covers
  initial population while keeping cold behavior finite; it must be measured
  before any pass.
- **Conditional-hit latency:** 4/5/6/8 ms exceeds the observed recomputing 304
  values by 44.0%/46.4%/64.9%/118.9%. The high max percentage is only 4.345 ms
  in absolute terms. Because a qualifying hit must make zero owner calls, these
  caps allow instrumentation overhead but not recomputation.
- **Health latency:** 3/4/5/8 ms exceeds observed values by
  79.9%/54.0%/81.2%/114.1%. The relative margins look large because the base is
  1.668-3.736 ms; the absolute headroom is only 1.332-4.264 ms and protects a
  critical endpoint from broad budget inflation.
- **Throughput:** floors 150/300/450 req/s permit degradation of
  24.2%/16.4%/18.2% from observed overview/conditional/health. Throughput is
  secondary to tail latency; these rounded floors detect material regressions
  while tolerating run noise. Cold overview is half the warm floor because its
  latency allowance is doubled.
- **CPU:** 0.75/0.45/0.30 CPU s per 100 exceeds observed by
  34.3%/45.2%/62.0%. The aggregate 1.50 CPU s cap exceeds observed 1.053662 by
  42.4%. This permits instrumentation/cache bookkeeping but rejects CPU savings
  gained by hiding source work or truth.
- **RSS:** 96 MiB is 40,416 KiB (69.8%) above observed 57,888 KiB. The source
  RSS includes the test harness, and Python allocators retain arenas; the
  separate 8 MiB growth cap is the tighter leak/backlog control.
- **Timeout:** 1,100 ms is 98.542 ms (9.84%) above observed 1,001.458 ms. It
  allows timer/scheduler wake-up overhead while improving materially on the old
  1,400 ms guard. The owner budget itself is not increased.
- **Pagination:** `limit+1` grants exactly one look-ahead record for
  `has_more`; anything more is unnecessary work. Caps 200/201 preserve the
  existing external page maximum while eliminating the observed 10,000-item
  scan.
- **Cache:** 256 entries × an average 64 KiB equals the independent 16 MiB byte
  cap; either cap can bind. Five seconds limits stale exposure, while a 95% hit
  floor over 1,000 unchanged requests allows at most 50 misses for deterministic
  expiry/eviction activity but no false hit.
- **Concurrency 8 and rate/isolation:** every concurrency-8 latency threshold is
  exactly 2x its concurrency-1 threshold and every throughput floor is half
  (cold overview rounds 37.5 down to 35 req/s). CPU allows at most 8x the
  single-lane bound because eight requests may execute simultaneously; this is
  a ceiling, not an efficiency claim. Reserving 2 of 8 slots leaves 75% for
  expensive work while guaranteeing read capacity. Queues 16 and 4 bound
  waiting work to 2x and 0.5x total worker count. The 20/40/40 mixed-read split
  gives health 200 tail samples and weights common projections most heavily.
  Contended tails are capped at the same 2x factor; it absorbs contention but
  prevents starvation.
- **Five trials × 1,000:** 1,000 samples makes p99 the 990th ordered value rather
  than a single extrapolated tail; worst-of-five avoids selecting a favorable
  run. Ten warm-ups match the source while reducing cold-start contamination.

## 5. Expiry, stop conditions, residual risks, and rollback

### Expiry

If approved, this envelope expires at the earliest of:

1. `2026-11-23T23:59:59Z` (90 days after preparation);
2. any change to these proposed limits, API/schema contract, fixture hash/scale,
   process model, or qualifying hardware floor; or
3. after a candidate successfully qualifies, any change to that pinned app
   commit, dependency lock, Python minor version, cache/limiter/pagination/SSE
   or workload-isolation implementation, or the first qualifying/deployed
   observation outside a limit or semantic invariant.

Expiry requires a new human decision; it does not silently extend or loosen a
limit.

### Immediate stop / CHANGES REQUIRED conditions

Stop qualification and do not call `2d02b6ed` passed if any required envelope
field or raw sample is absent; any limit is exceeded in any trial; any expected
status error occurs; owner work exceeds `limit+1`; cache entries/bytes/TTL or
zero-owner-hit rules fail; health/evidence is rate-starved; isolation routes are
absent; queues/slots are unbounded; RSS growth exceeds 8 MiB; a timeout exceeds
1,100 ms; an SSE/cache/pagination/rate semantic requirement fails; or stale,
partial, unavailable, unknown, unauthorized, unreachable, or timed-out truth is
hidden, omitted, or presented as healthy. Stop also on protected-data exposure,
credential output, or scope drift.

### Residual risks

- The source is same-process, single-host, sequential, short-run synthetic
  evidence; it says nothing about TLS/network, deployed workers, cgroups,
  multihost owners, noisy neighbors, sustained soak, or production scale.
- Only three read classes had latency/resource observations. Cold cache,
  concurrency 8, real cache hits, insight/report contention, bounded owner scan,
  and SSE backpressure have no successful source measurements.
- Peak RSS is process-wide and CPU is short-duration process CPU, not isolated
  per route. Python allocator behavior and TestClient scheduling can differ
  from deployment.
- A fixed 16-population fixture may underrepresent cardinality, payload size,
  skew, and correlated owner failures. The 10,000-item sentinel tests work
  bounding, not realistic data distribution.
- Tight in-process budgets may need a separate human-ratified socket/TLS
  envelope before deployment; these numbers must not be transplanted to live
  service claims.

### Rollback

Before human approval, rollback is deletion of only this proposal artifact and
removal/supersession of its links on `2933ef14`; no runtime rollback exists
because no product or service changed. After approval, rollback of the **budget
decision** is an append-only human revocation/superseding decision on
`2b49789d`, immediately returning `2d02b6ed` to unqualified status. Product
rollback for future implementations belongs to their own cards and is not
claimed here.

## 6. Copy-ready human decision template for `2b49789d`

The eligible human must select exactly one line, fill identity/time/reference,
and append it to card `2b49789d`. This template itself selects neither outcome.

```text
SKCP-51F1 HUMAN DECISION - card 2b49789d

Decision (select exactly one; unselected line must remain visibly unselected):
[ ] CHANGES REQUIRED - I do not authorize these limits. Required changes:
    <exact fields/limits/rationale; no blank value>
[ ] APPROVE - I authorize only the exact proposed envelope in
    docs/evidence/SKCP-51F1A-API-BUDGET-PROPOSAL-2026-08-25.md
    sha256=<proposal_sha256 linked on preparation card 2933ef14>.

I verified the failed source qualification remains BLOCKED and is not treated
as a completed dependency:
- narrative sha256=b5c2f859b8230efab48cfb490f121b7c003e390e442c3948b7df986882def067
- raw JSON sha256=01c030231d70090c92e5790b7a12d052f1a52ab14bb32cfe3f20dce4f1edcadd
- fixture sha256=926f0374b8f32e64d0f370f9a6197b848a95a3403b8d497c5c67848e77fb821d
- measured candidate=9b2cbff7204d3e76a69f4bf21e9bf1de8ec04ef5

Scope acknowledged: public-synthetic only; in-process concurrency-1 baseline
plus mandatory concurrency-8 contention lane; exact latency/throughput/error,
CPU/RSS/timeout, pagination, cache, rate/isolation, method, expiry, stop,
truth-preservation, limitations, residual-risk, and rollback terms in the
hash-pinned proposal. This decision authorizes targets only. It does not pass
2d02b6ed, approve an implementation/deployment, authorize protected data,
waive failed semantics, or claim any successor.

Eligible human name/role: <name and authority>
Decision timestamp UTC: <YYYY-MM-DDTHH:MM:SSZ>
Append-only/signed decision reference: <reference>
Signature or authenticated identity proof: <reference>
```
