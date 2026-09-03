# SKDashboard Schedule Provider Contract v1.0.0 (R2)

**Contract ID**: `skdashboard.schedule.provider.v1.0.0`
**Status**: PROPOSED - Requires Independent Review
**Card**: `2a4bb204` (R2 realignment of the R1 contract from the same card)
**Trace Card**: `36f3396c`
**Frozen Schema**: `docs/contracts/schedule/v1.0.0/control-plane-schedule-projection.v1.0.0.schema.json` (inventory hash b696d55d, source e08d9df73d0cec9ea705b422c5f532ec6543e5e8)
**Frozen OpenAPI**: `docs/contracts/schedule/v1.0.0/openapi.control-plane-schedule.v1.0.0.json` (same revision)
**Realigned Against**: current `main` at f83f0d9 (merge of PR #149), which adds `dashboard_schedule.py`, `forecast.py`, the `/api/v1/schedule/forecasts` route, and extends the projection schema and OpenAPI
**Created**: 2026-08-30 (R1) / 2026-09-03 (R2)
**Review Required**: YES - no implementation composition of the missing production sources until independently approved

---

## 0. R2 Revision Summary

R1 of this contract was defined at base commit e08d9df7. Its PR (#121) went red
because the branch base predates later main work, and independent main-lane work
subsequently landed real schedule machinery that R1 predates:

- `src/skdashboard/dashboard_schedule.py`: `CanonicalScheduleSource`,
  `ScheduleSourceRequest`, and `ScheduleProjectionProvider` compose a
  file-backed canonical owner snapshot into the projection.
- `src/skdashboard/forecast.py`: `forecast()` and `simulate_dependencies()`
  implement deterministic aggregate throughput Monte Carlo.
- Route `/api/v1/schedule/forecasts` with error code
  `SCHEDULE_FORECAST_UNAVAILABLE`.
- Schema additions: `field_provenance`, `dates.forecast_start`,
  `dates.forecast_target`, and `stale`/`partial` date states.
- OpenAPI additions: `/schedule/insights` GET and an insight schema.

R2 therefore realigns rather than rewrites: every unresolved R1 question is
answered against the code that now exists, the vocabulary is unified with the
frozen schema, and contradictions are resolved by amendment of THIS document
only. The R2 delta over R1 is exactly:

1. The authoritative schedule owner reader is pinned as the
   `CanonicalScheduleSource` protocol implemented by the file-backed canonical
   snapshot source behind `ScheduleProjectionProvider` (replacing the R1
   placeholder designation).
2. The approved aggregate forecast-history source is pinned as the canonical
   throughput history consumed by `forecast()` (replacing the R1 placeholder).
3. Vocabulary aligned to the frozen schema: canonical item types and statuses
   (with a bounded compatibility map from the R1 set), dependency
   `edge_type`/`direction`/`lag_seconds` replacing R1's
   `dependency_type`/`lag_days`.
4. Dependency output bound corrected from 50,000 to the schema's 20,000, and
   truncation semantics replaced with the implemented fail-closed reject for
   over-bound source snapshots (the route already rejects, it never truncates).
5. AC4 resolved as: OpenAPI `skdashboard.schedule.read` and implemented
   `skdashboard.read` are aliases for the SAME single schedule read
   authorization target; `portfolio` is a canonical schema role and `operator`
   is its route-level compatibility alias; empty query defaults are preserved
   exactly as implemented.

---

## 1. Contract Purpose and Scope

This contract defines the governed production boundary for real schedule and
forecast providers in SKDashboard. It establishes the authoritative data
sources, authorization boundaries, read semantics, and policy constraints that
any schedule provider implementation MUST satisfy.

This contract DOES NOT authorize composing any NEW production source. It pins
the boundary that existing composition must honor and that any future source
integration MUST satisfy, and it MUST be independently reviewed before any
card that composes a new owner or history source proceeds.

### 1.1 Invariants

1. **Fail-Closed Default**: Any malformed source record, schema violation,
   over-bound snapshot, stale watermark, or authorization failure MUST raise
   for the route boundary to convert into the route's constant 503
   SCHEDULE_UNAVAILABLE (projection) or SCHEDULE_FORECAST_UNAVAILABLE
   (forecast). Partial results MUST NOT be released.
2. **No Owner Mutation**: Both providers are READ-ONLY. They MUST NOT write,
   delete, or modify any owner-system records. The forecast artifact MUST
   carry `writes_owner_records: false`.
3. **Typed Authorization Required**: Every owner read MUST run through the
   typed CapAuth binding with target `/api/v1/schedule/projection` and
   capability `skdashboard.read`, plus the single-use currentness verifier
   called once before the owner read and once after projection, immediately
   before release. Duck-typed authorization is prohibited.
4. **Exact Schema Compliance**: All schedule projection output MUST validate
   against `control-plane-schedule-projection.v1.0.0.schema.json`; forecast
   output MUST satisfy the route's typed forecast acceptance contract
   (Section 5.7).
5. **Reject Over Truncate**: When a source snapshot exceeds a declared bound,
   the provider MUST reject the snapshot (fail closed), not truncate. The
   frozen schema bounds are hard caps, never a truncation target.

---

## 2. Authoritative Schedule Owner Reader

### 2.1 Owner Source Pin

The authoritative schedule owner reader for canonical schedule data is pinned
as the `CanonicalScheduleSource` protocol:

```python
class CanonicalScheduleSource(Protocol):
    def read(self, context, request: "ScheduleSourceRequest", home: Path) -> Mapping | None: ...
```

Its production implementation is `AuthorizedCardScheduleSource`, which folds
canonical records from CardStore. `compose_file_backed_live_control_plane`
uses a file-backed backend only for owner-policy configuration. `read` MUST:

- Select owner policy by the authorization target BEFORE enumerating records.
- Return records for the request tenant only, never another tenant.
- Return one atomic snapshot (single `snapshot_revision`), or `None` when
  unavailable.
- Never parse item titles or descriptions as schedule values, and never
  guess any schedule value.

### 2.2 Snapshot Contract

The owner reader MUST provide an atomic snapshot containing exactly:
`schema_version` ("1.0.0"), `tenant_id`, `snapshot_revision` (max 128 chars),
`observed_at` (ISO 8601, timezone-aware, not in the future by more than 5
seconds), `projected_at` (>= `observed_at`), `authorization` (typed decision:
`state=authorized`, matching `target` `/api/v1/schedule/projection`, matching
`tenant_id`, `role`, and `scope`, with a policy decision reference and owner
policy revision), `source_watermarks` (max 64, each `{source: max 128 chars,
value: max 256 chars}`), `items`, `dependencies`, and `overlays`.

### 2.3 Canonical Data Elements (schema vocabulary)

Schedule items in the snapshot are canonical coordination records mapped to
the frozen schema `item` shape. The authoritative field provenance table is
pinned by `FIELD_PROVENANCE` in `dashboard_schedule.py`; in particular, item
`title` comes only from `canonical coordination record.display_title`, and
status from `canonical coordination record.lifecycle_status`. No field may
copy from free text.

#### 2.3.1 Schedule Items

Canonical `item_type` values (frozen schema enum):
`outcome`, `project`, `epic`, `release`, `milestone`, `work_package`, `team`,
`service`, `architecture_migration`, `itil_change_window`.

R1 used an owner-system vocabulary (`feature`, `bugfix`, `debt`, `spike`,
`infrastructure`, `governance`). R2 requires owners to map into the canonical
set; the compatibility map is: `feature -> project`, `bugfix -> work_package`,
`debt -> work_package`, `spike -> work_package`,
`infrastructure -> architecture_migration`, `governance -> itil_change_window`.
Unmapped values MUST be rejected (fail closed).

Canonical item `status` is the owner lifecycle status string (1 to 64 chars),
carried as the owner's canonical value; it is not an enum in the frozen
schema. Lifecycle gating (for example which statuses are schedulable or
counted) belongs to the owner policy, not the projection provider.

All other item fields, bounds, `truth_state`, and `visibility` follow the
frozen schema exactly. `dates` uses `date_value` objects whose `state` is one
of `known`, `unknown`, `stale`, `partial`, `unavailable`,
`policy_filtered`, `not_applicable` (R2 adds `stale` and `partial` to the R1
set), and includes `forecast_start`/`forecast_target` sourced only from an
authorized forecast artifact.

#### 2.3.2 Dependency Edges

Dependency edges use the frozen schema shape: `dependency_id`,
`source_item_id`, `target_item_id`, `edge_type` (one of `finish_to_start`,
`start_to_start`, `finish_to_finish`, `start_to_finish`, `unknown`),
`direction` (`known` | `unknown`), `lag_seconds` (replacing R1's
`lag_days`; owners MUST convert days to seconds, integer),
`truth_state`, `visibility`, `blocker_state` (`blocking` | `not_blocking` |
`unknown`), `cycle_state`, `evidence_refs`. R1's `dependency_type` and
`is_hard_constraint` map to `edge_type` and `blocker_state` respectively.

The projection pins cycles as DETECTED, never silently pruned: the projection
reports `cycle_analysis.state = cycles_detected` with `cycle_item_ids` and
per-cycle evidence refs; it does not omit cycle edges. Self-referencing edges
are invalid and rejected. Duplicate `item_id` values are rejected
(fail closed), not deduplicated.

#### 2.3.3 Overlays

Overlays are typed windows with `overlay_type`, `start`, `end`,
`conflict_state`, `owner_service_id`, `truth_state`, `visibility`, and
`evidence_refs` per the frozen schema. Canonical overlay types include ITIL
change windows, blackout windows, architecture migrations, and architecture
deprecations, matching `FIELD_PROVENANCE`.

#### 2.3.4 Source Watermarks

Each watermark is `{source, value}` per the frozen schema (max 64 per
projection). The snapshot additionally carries `snapshot_revision` and
`observed_at`, which the provider freshness check uses (Section 4.1).

#### 2.3.5 Evidence References

Evidence references are provider-defined bounded strings (max 500 chars)
under `evidence_refs`, plus the projection-level `field_provenance` map
(maxProperties 1+, each value 1 to 500 chars) which MUST be present and
non-empty in every projection. `policy_decision_ref` in `visibility` carries
the CapAuth decision correlation.

---

## 3. Source-Side Authorization and Classification

### 3.1 Authorization Flow

The provider MUST implement this exact sequence (as implemented in
`ScheduleProjectionProvider.read`):

1. Build the typed request from the context binding: the binding target MUST
   equal `/api/v1/schedule/projection` and the binding capability MUST equal
   `skdashboard.read`, and the joined decision MUST allow; otherwise raise
   (route: 503).
2. Check currentness BEFORE the owner read via the single-use verifier;
   non-ALLOW raises (route: 503).
3. Perform the owner read; any error raises (route: 503).
4. Project and validate the snapshot; any malformed record, mismatched
   tenant, stale authority, or over-bound snapshot raises (route: 503).
5. Check currentness AFTER projection, immediately before release;
   non-ALLOW raises (route: 503).
6. Release; the verifier is single use and MUST NOT be reused across reads.

### 3.2 Classification and Visibility Policy

Classification enforcement is source-side and happens BEFORE the snapshot
reaches the provider: the canonical source must apply the owner policy
(selection, filtering, redaction) for the exact tenant, role, and scope, and
stamp every released field's `visibility` and `authorization`. Owner-policy
filtered records, fields, and dependency edges are omitted before the snapshot
is released; they are never replaced by invented values. The provider re-verifies the snapshot's
authorization block matches the request exactly and raises on any mismatch.

### 3.3 Tenant Filtering

The provider MUST enforce: `snapshot.tenant_id` equals the configured request
tenant, which equals the context tenant. Any mismatch raises
(fail closed). Cross-tenant references MUST NOT be silently dropped by the
provider; they indicate a source policy failure and MUST reject the snapshot.

### 3.4 Role Handling

The canonical role vocabulary is the frozen schema's: `portfolio`,
`project_manager`, `architect`, `service`, `team`. The route accepts the
implemented alias set (`project-manager`, `operator`, `architect`, `service`,
`team`) and the provider maps it canonically:

- `project-manager` and `project_manager` both map to `project_manager`.
- `operator` maps to `portfolio`; `portfolio` passes through as `portfolio`.
- `architect`, `service`, `team` pass through unchanged.

This mapping is narrowing, never broadening: `operator` and `portfolio` name
the same estate-wide role in one direction. The route requires
`scope=estate` and `service=all`; any other scope or service raises
400 INVALID_SCHEDULE_SCOPE at the route before the provider is invoked. R1's
per-role scope matrix (project/service/team scoping) is NOT implemented in
the route and MUST NOT be enabled by a provider; future scope broadening
requires a new contract revision.

---

## 4. Freshness, Timeout, and Bounds Policy

### 4.1 Freshness TTL

Source freshness is watermark-based, not cache-based. The provider MUST
compute snapshot age as `now - observed_at` (clock injectable for tests) and:

| Condition | Behavior |
|-----------|----------|
| age < -5 s (future watermark) | raise, 503 |
| age > max_source_age_seconds | raise, 503 |
| projected_at < observed_at | raise, 503 |

`max_source_age_seconds` default is 300 s and MUST be configured between 1
and 86,400 inclusive. R1's per-type TTL table is superseded: the atomic
snapshot carries one watermark age for the whole projection.

### 4.2 Timeout Policy

| Operation | Timeout | On exceed |
|-----------|---------|-----------|
| Owner source read | `schedule_owner_read_timeout_seconds`, default 2 s | raise, 503 SCHEDULE_UNAVAILABLE |
| Full request (end-to-end) | `schedule_request_timeout_seconds`, default 5 s | raise, 503 SCHEDULE_UNAVAILABLE |

Both deployment values are bounded from 0.01 through 30 seconds and the owner
read timeout cannot exceed the full request timeout. Timeouts are fail-closed; partial results are prohibited. The projection
transformation itself is synchronous and bounded by the snapshot bounds in
Section 4.4. R1's millisecond-level table is superseded as unspecified here;
the route and provider convert all failures to the same constant 503.

### 4.3 Input Bounds (route, as implemented)

| Parameter | Bound | Violation |
|-----------|-------|-----------|
| Any single query value length | 128 chars | 400 INVALID_SCHEDULE_SCOPE |
| Duplicate query keys | prohibited | 400 INVALID_SCHEDULE_SCOPE |
| Unknown query keys | prohibited | 400 INVALID_SCHEDULE_SCOPE |
| Empty values | prohibited | 400 INVALID_SCHEDULE_SCOPE |
| role | route enum (alias set) | 400 INVALID_SCHEDULE_SCOPE |
| scope | `estate` only | 400 INVALID_SCHEDULE_SCOPE |
| window | `latest` only | 400 INVALID_SCHEDULE_SCOPE |
| baseline | `none` only | 400 INVALID_SCHEDULE_SCOPE |
| service | `all` only | 400 INVALID_SCHEDULE_SCOPE |
| lens | `roadmap` \| `gantt` \| `flow` | 400 INVALID_SCHEDULE_SCOPE |
| timezone | non-empty (len <= 128) | 400 INVALID_SCHEDULE_SCOPE |
| selected_item | optional; projection scope is estate | 400 on other violations |

Empty query defaults to `{role: project-manager, scope: estate,
window: latest, baseline: none, service: all, lens: roadmap, timezone: UTC}`.
`lens` MUST NOT change the data: all three lenses receive byte-identical
projection bytes (lens is presentation only, excluded from scope, ID,
version, and hash). OpenAPI-only parameters (`portfolio_id`, `project_id`,
`service_id`, `team_id`, `projection_version`) are NOT accepted by the
implemented route (it rejects unknown keys); a future route revision MAY map
them, and if it does, they MUST alias within the same role/scope authority
without broadening access. R1's alias-acceptance resolution is superseded.

### 4.4 Output Bounds (schema-bounded, fail closed)

| Element | Bound (frozen schema) | On violation of source snapshot |
|---------|----------------------|--------------------------------|
| source_watermarks | 64 | raise, 503 |
| schedule items | 10,000 | raise, 503 |
| dependency edges | 20,000 (R1 said 50,000; corrected) | raise, 503 |
| overlays | 5,000 | raise, 503 |
| cycle item ids | 10,000 | schema rejects |
| evidence_refs per field | 128 entries | schema rejects |
| item_id / identifier length | 128 chars | raise |
| watermark value length | 256 chars | raise |
| provenance / reason strings | 500 chars | raise |

Additionally: item ids MUST be unique and sorted ascending; the projection
hash is the SHA-256 over the projection with `projection_hash` null; no
`truncated` flag exists anywhere in this contract. R1's truncation priority
algorithm (Appendix B) is RETIRED.

### 4.5 Malformed Source Handling

Malformed source data is fail closed. The following MUST raise for a 503
(not be skipped or repaired):

- Snapshot missing any required key, or carrying unknown keys, or wrong
  `schema_version`.
- Any item, dependency, overlay, watermark, or date failing schema shape.
- Any invalid enum value, including R1 vocabulary not mapped per Section 2.3.1.
- Non-unique or unsorted item ids.
- Self-referencing dependency edges.
- Any tenant, role, scope, target, or authorization mismatch.
- Stale (Section 4.1) or future watermarks.

Because every record is validated, the R1 "10% malformed threshold" is
meaningless and is retired: the first malformed record rejects the whole
snapshot. Route behavior on any provider raise: constant 503
SCHEDULE_UNAVAILABLE (projection) with `retryable: true`; the error body
carries only `code`, `message`, `retryable`, `request_id`. Provider internals
MUST NOT leak into the message.

### 4.6 Currentness Behavior

The single-use currentness verifier is invoked exactly twice per read:

1. `check_before_owner_read(context)` MUST be ALLOW before the owner read.
2. `check_after_owner_read(context)` MUST be ALLOW after projection,
   immediately before release.

Any non-ALLOW result, missing context, missing verifier, or missing provider
raises to the route's constant 503 with `retryable: true`. The verifier MUST
NOT be reused across reads.

---

## 5. Aggregate Forecast-History Source

### 5.1 Forecast-History Source Pin

The approved aggregate forecast-history source is pinned as the canonical
throughput history consumed by `forecast()` in
`src/skdashboard/forecast.py`: a sequence of `ThroughputPeriod` records
(`period_id`, `start`, `end`, `completed`, `timing_basis`). Periods whose
`timing_basis` is not `canonical_period` are excluded and recorded as
exclusions with reason; canonical periods MUST NOT overlap (violation raises).

Any future external history store (for example R1's placeholder
"SKCore Aggregate Throughput History") MAY be integrated as a producer of
these canonical periods, but that integration is exactly what this contract
withholds pending independent review.

### 5.2 Cohort

Forecast cohort is the caller-supplied `cohort` string (non-empty; route does
not constrain its value). The R1 `estate_delivery_cohort` name and its
membership/exclusion list are retired as unenforceable; cohort semantics are
owned by the caller of `forecast()` and MUST be recorded in the artifact.

### 5.3 Timing Basis

The supported timing basis is `canonical_period` exactly. Periods on any
other basis are excluded (never pooled), each recorded as
`{period_id, timing_basis, reason}` in the artifact's `exclusions`. Canonical
periods MUST share one cadence: mixed cadence abstains with reason
`canonical throughput periods have mixed cadence`. R1's fixed Monday-anchored
7-day periods are retired; cadence is derived and reported as
`period_cadence_days` (integer, or null when abstained).

### 5.4 Remaining Work Source

`remaining_work` is a positive integer supplied by the caller of `forecast()`
(units are periods-of-work agnostic counts; the route validates type, not
units). It MUST come from the same tenant's authorized canonical data; a
provider composing it from the Section 2 owner reader MUST derive it only
from owner-policy-filtered records and MUST NOT derive it from titles or
descriptions. Non-positive remaining work raises. R1's weighted
half-complete heuristic is retired.

### 5.5 History Window

The window is whatever canonical periods the caller supplies, after
non-canonical exclusion. `sample_periods` is the included count. Abstention
replaces any minimum-window forecast: with fewer than `minimum_sample`
(default 6) included periods, zero total completed work, or mixed cadence,
the forecast MUST abstain with an explicit `abstention_reason` string and
null quantiles. R1's 8/13/26/52 period windows are retired.

### 5.6 Exclusions

Exclusions are recorded per period as
`{period_id, timing_basis, reason}` (all strings; exactly these three keys).
Non-canonical timing bases are always excluded with reason. R1's
business-hours, spike, debt, and emergency-change exclusions are retired as
unenforceable at this layer; if owners pre-filter history, the exclusion
record MUST reflect it. Exclusion records MUST NOT name individual
contributors.

### 5.7 Forecast Representation

The forecast is a standalone read-only artifact with EXACTLY these keys
(route acceptance contract, verified on main):

Required on all artifacts: `schema_version` ("1.0.0"), `artifact_kind`
("aggregate_schedule_forecast"), `state` ("ready" | "abstained"), `method`
("aggregate_throughput_bootstrap_monte_carlo"), `calculation_owner`
("deterministic_engine"), `method_discrimination` (exactly
`{throughput_forecast: "probabilistic aggregate flow in periods",
date_critical_path: "not calculated or blended by this artifact"}`),
`cohort` (string), `scope` (string), `history_window` (dict with exactly
`start` and `end`, string or null), `sample_periods` (int),
`period_cadence_days` (int or null), `remaining_work` (int), `iterations`
(int), `seed` (int), `assumptions` (list of strings), `exclusions` (list of
`{period_id, timing_basis, reason}`), `individual_ranking_prohibited`
(true), `completion_quantiles_periods` (dict with exactly `p50`, `p85`,
`p95`), `milestone_confidence`, `writes_owner_records` (false).

Ready state additionally requires: `abstention_reason` null, integer
quantiles with `p50 <= p85 <= p95`, and `milestone_confidence` null or float
in [0, 1]. Abstained state requires: non-empty string `abstention_reason`,
all quantiles null, `milestone_confidence` null. Any extra key, wrong method
or calculation owner, or `writes_owner_records` not false is a 503.

Defaults: `iterations` 2000, `minimum_sample` 6, quantiles at the 50th, 85th,
and 95th percentile by nearest-rank. Sampling is a deterministic seeded
bootstrap with a bounded horizon. R1's fixed 10,000 iterations and
`skcore_throughput_v1` method are retired. R1's `projection.forecast`
embedding is superseded: the forecast is served at its own authorized route
and MUST NOT be blended into the schedule projection document.

---

## 6. OpenAPI and Route Alignment (AC4)

### 6.1 Capability Resolution

The frozen OpenAPI declares `skdashboard.schedule.read` for schedule reads.
The implemented protected route enforces `skdashboard.read` as the typed
binding capability with authorization target `/api/v1/schedule/projection`.
Resolution WITHOUT broadening access:

- `skdashboard.read` remains the ONLY capability the route accepts.
- `skdashboard.schedule.read` is pinned as the OpenAPI DOCUMENTATION alias
  for this same single authorization. Consumers presenting capabilities MUST
  hold `skdashboard.read`; no token, policy, or route change grants access
  based on `skdashboard.schedule.read` alone, and no new capability is
  minted.
- Any future route that accepts `skdashboard.schedule.read` as a second
  accepted capability MUST come from a new contract revision; it is
  explicitly NOT authorized by this contract. R1's dual-acceptance
  resolution is superseded because it would require authorization changes.

### 6.2 Role Vocabulary Resolution

OpenAPI `Role` enum: `portfolio`, `project_manager`, `architect`, `service`,
`team`. Route enum: `project-manager`, `operator`, `architect`, `service`,
`team`. Resolution: `project-manager`/`project_manager` are spelling aliases
of one role; `operator` is the route spelling of canonical `portfolio`
(schema scope enum and FIELD_PROVENANCE already use `portfolio`).
Normalization is one mapping table (Section 3.4), narrowing only. No role
gains permissions it did not have.

### 6.3 Query Parameter Resolution

OpenAPI declares `Role` (required), `PortfolioId`, `ProjectId`, `ServiceId`,
`TeamId`, `Timezone` (required), `ProjectionVersion`; the route accepts
`role`, `scope`, `window`, `baseline`, `service`, `lens`, `timezone`,
`selected_item` and rejects unknown keys. Resolution: today the implemented
route is authoritative and the OpenAPI parameters `PortfolioId`,
`ProjectId`, `ServiceId`, `TeamId`, and `ProjectionVersion` are
NOT-WIRE-ABLE: they exist in the frozen document but MUST NOT be sent to the
implemented route, and the implemented route MUST NOT accept them until a
contract revision defines their aliasing. `Role` and `Timezone` map 1:1.
This is the no-broadening resolution: no alias acceptance on either side
without a new revision.

---

## 7. Provider Interface Definition

The provider interfaces are the ones implemented on main, restated here as
the contract: `CanonicalScheduleSource` (Section 2.1),
`ScheduleProjectionProvider.read(context, query, home, *,
currentness_verifier)` (schedule projection), and `forecast(history, *,
cohort, scope, remaining_work, seed, iterations, minimum_sample,
milestone_period, assumptions)` plus `simulate_dependencies(...)` (forecast).
The route-side query contract is the validated route dict (Section 4.3),
not R1's `ScheduleQuery` dataclass, which is retired. R1's Python protocol
block is superseded by the implemented classes; the binding contract points
are `dashboard_schedule.py` constants: `AUTHORIZATION_TARGET`,
`AUTHORIZATION_CAPABILITY`, `ROLE_MAP`, `FIELD_PROVENANCE`, `MAX_ITEMS`,
`MAX_DEPENDENCIES`, `MAX_OVERLAYS`, and constructor bounds
(`max_source_age_seconds` 1 to 86,400; identifier-shaped `tenant_id`).

### 7.1 Error Contract

All errors MUST use the route's envelope, which is exactly:

```json
{
  "code": "ERROR_CODE",
  "message": "constant, non-leaking",
  "retryable": true,
  "request_id": "correlation id (x-request-id or generated)"
}
```

| Code | HTTP | retryable | Meaning |
|------|------|-----------|---------|
| `SCHEDULE_UNAVAILABLE` | 503 | true | projection provider cannot produce a valid projection |
| `SCHEDULE_FORECAST_UNAVAILABLE` | 503 | true | forecast provider cannot produce a valid artifact |
| `INVALID_SCHEDULE_SCOPE` | 400 | false | query parameters invalid |
| `FORBIDDEN` | 403 | false | authorization failed (route-level) |

`STALE_DATA` from R1 is retired: staleness surfaces as the same constant 503
`SCHEDULE_UNAVAILABLE` and never as a distinct code.

---

## 8. Test Requirements

The contract test suite MUST cover, with the real frozen schema (not a mock
schema) and mock providers only:

1. Schema validation: valid projection validates; missing required field,
   invalid enum, unsorted or duplicate ids, and over-bound arrays fail.
2. Authorization: missing context, missing verifier, denied pre-read, denied
   post-read, mismatched binding target or capability, and verifier reuse
   all fail closed.
3. Role and scope: alias normalization both directions; non-estate scope and
   non-all service rejected at 400; lens produces byte-identical projections.
4. Freshness: future watermark, stale watermark, and `projected_at` before
   `observed_at` all reject.
5. Bounds and malformed: over-bound items/dependencies/overlays reject;
   malformed records reject the snapshot; self-reference rejects; duplicate
   ids reject; mixed-cadence and low-sample forecasts abstain.
6. Forecast representation: exact key set, method and calculation owner
   strings, exclusion record shape, quantile ordering and nullity rules,
   `individual_ranking_prohibited` true, `writes_owner_records` false.
7. Alignment: role alias set matches route; OpenAPI-not-wire-able parameters
   are rejected by the route; error envelope shape.

Static/security checks: repo lint (ruff), build, gitleaks, GitGuardian per
repository CI; bandit, mypy, pip-audit runs recorded in the review evidence
as available.

---

## 9. Independent Review Requirements

Before any card composes a NEW production owner source or external history
store against this boundary, this contract MUST be independently reviewed.
The reviewer MUST be distinct from the producer by agent identity, host,
session, and workspace, MUST review the pushed branch bytes (not a local
worktree), and MUST verify the R2 delta list (Section 0) against current
main: every superseded R1 clause must be checked against the implementation
constants cited above.

Sign-off JSON (posted as review evidence, not committed as code):

```json
{
  "reviewer": "agent-name",
  "role": "independent-reviewer",
  "date": "ISO 8601 date",
  "status": "APPROVED|APPROVED_WITH_COMMENTS|REJECTED",
  "comments": "required changes or none",
  "contract_commit": "sha of reviewed head",
  "evidence_sha256": "sha256 of review artifact"
}
```

---

## 10. Implementation Prohibitions (unchanged in force)

Until this contract is independently approved, the following remain
PROHIBITED for new work: implementing or composing a NEW production owner
source or external forecast-history store; any source integration beyond the
existing file-backed canonical snapshot; deployment or configuration
changes; credential usage; live provider traffic; database or owner-system
writes; registration of new live endpoints. Test code with mocks, contract
documents, and static analysis remain allowed.

---

## 11. Version History

| Version | Date | Author | Change Summary |
|---------|------|--------|----------------|
| 1.0.0 | 2026-08-30 | pi-glm-chiap01-2a4bb204 | Initial contract (R1) from trace card 36f3396c |
| 1.0.0 R2 | 2026-09-03 | pi-glm-chiap01-2a4bb204 | Realigned to main f83f0d9: pinned implemented owner reader and forecast source, schema vocabulary, fail-closed bounds, forecast route contract, AC4 alias resolution; retired unenforceable R1 semantics |

---

## 12. References

1. Trace Card 36f3396c evidence: `~/.skcapstone/evidence/work/36f3396c/read-only-source-audit.json`
2. Base commit: `e08d9df73d0cec9ea705b422c5f532ec6543e5e8`
3. Realignment reference: `main` f83f0d9 (`src/skdashboard/dashboard_schedule.py`, `src/skdashboard/forecast.py`, `src/skdashboard/control_plane_api.py` routes `/api/v1/schedule/projection` and `/api/v1/schedule/forecasts`)
4. Schema contract: `docs/contracts/schedule/v1.0.0/control-plane-schedule-projection.v1.0.0.schema.json`
5. OpenAPI contract: `docs/contracts/schedule/v1.0.0/openapi.control-plane-schedule.v1.0.0.json`
6. R1 PR (superseded, red CI): `https://github.com/smilinTux/skdashboard/pull/121`
