# SKDashboard Schedule Provider Contract v1.0.0

**Contract ID**: `skdashboard.schedule.provider.v1.0.0`
**Status**: PROPOSED - Requires Independent Review
**Base Commit**: `e08d9df73d0cec9ea705b422c5f532ec6543e5e8`
**Trace Card**: `36f3396c`
**Definition Card**: `2a4bb204`
**Created**: 2026-08-30
**Review Required**: YES - No implementation until independently approved

---

## 1. Contract Purpose and Scope

This contract defines the governed production boundary for real schedule and forecast providers in SKDashboard. It establishes the authoritative data sources, authorization boundaries, read semantics, and policy constraints that any schedule provider implementation MUST satisfy.

This contract DOES NOT authorize implementation. It is a specification that MUST be independently reviewed and approved BEFORE any implementation card may proceed.

### 1.1 Invariants

1. **Fail-Closed Default**: Any deviation from this contract, absence of required fields, or source unavailability MUST result in HTTP 503 SCHEDULE_UNAVAILABLE.
2. **No Owner Mutation**: The schedule provider is READ-ONLY. It MUST NOT write, delete, or modify any owner-system records.
3. **Typed Authorization Required**: Every owner read MUST be preceded and followed by a typed CapAuth decision check. Duck-typed authorization is prohibited.
4. **Exact Schema Compliance**: All output MUST validate against `control-plane-schedule-projection.v1.0.0.schema.json`.
5. **Truncation Over Exception**: When input exceeds defined bounds, truncate and log rather than throwing exceptions.

---

## 2. Authoritative Schedule Owner Reader

### 2.1 Owner Source Designation

The authoritative schedule owner reader is designated as:

**Source Name**: `SKCore Work Item Authority` (placeholder pending source-owner approval)
**Source Type**: Production work item and dependency management system
**Read Protocol**: HTTP/REST with OAuth2 client credentials
**Base URL**: TO BE APPROVED - Not specified in this contract
**Classification**: Internal - Restricted to SKDashboard service principal

**Note**: This contract DESIGNATES the need for an authoritative owner source but does NOT SELECT one. Source-owner approval is required to pin an actual endpoint and credential.

### 2.2 Authoritative Data Elements

The owner reader MUST provide canonical, immutable data for:

#### 2.2.1 Schedule Items
- `item_id`: Unique, stable identifier from owner system (string, max 128 chars)
- `item_type`: One of `feature`, `bugfix`, `debt`, `spike`, `infrastructure`, `governance`
- `title`: Human-readable title (string, max 500 chars)
- `description`: Extended description (string, max 10000 chars, markdown permitted)
- `status`: One of `backlog`, `refined`, `in_progress`, `blocked`, `review`, `done`, `cancelled`
- `priority`: One of `critical`, `high`, `medium`, `low`, `none`
- `assignee`: Identifier of assigned team or individual (string, max 256 chars, nullable)
- `team`: Owning team identifier (string, max 128 chars)
- `service`: Associated service identifier (string, max 128 chars, nullable)
- `portfolio`: Portfolio identifier for rollup (string, max 128 chars)
- `created_at`: ISO 8601 timestamp of creation
- `updated_at`: ISO 8601 timestamp of last modification
- `started_at`: ISO 8601 timestamp of work start (nullable)
- `completed_at`: ISO 8601 timestamp of completion (nullable)
- `estimated_hours`: Numeric estimate (decimal, nullable)
- `actual_hours`: Actual hours spent (decimal, nullable)
- `tags`: Array of classification tags (max 20 tags, each max 64 chars)

#### 2.2.2 Dependency Edges
- `predecessor_id`: Item identifier that must complete first
- `successor_id`: Item identifier that depends on predecessor
- `dependency_type`: One of `finish_to_start`, `start_to_start`, `finish_to_finish`, `start_to_finish`
- `lag_days`: Numeric lag (integer, default 0, min -365, max 365)
- `is_hard_constraint`: Boolean indicating if dependency is mandatory

#### 2.2.3 ITIL Overlays
- `change_request_id`: Associated change request identifier (string, max 128 chars, nullable)
- `incident_ids`: Array of associated incident identifiers (max 10)
- `sla_breach_risk`: Boolean indicating SLA jeopardy
- `emergency_change`: Boolean flag for emergency classification
- `approval_status`: One of `not_required`, `pending`, `approved`, `rejected`

#### 2.2.4 Architecture Overlays
- `architectural_impact`: One of `none`, `low`, `medium`, `high`, `critical`
- `affected_components`: Array of component identifiers (max 20)
- `risk_category`: One of `technical`, `operational`, `security`, `compliance`
- `mitigation_required`: Boolean indicating if mitigation plan exists

#### 2.2.5 Source Watermarks
- `source_system_hash`: SHA-256 hash of the source record as retrieved
- `last_sync_at`: ISO 8601 timestamp of last successful sync
- `source_version`: Version or etag from owner system (string, max 256 chars)
- `retention_until`: ISO 8601 timestamp after which cached data is stale

#### 2.2.6 Evidence References
- `trace_id`: Correlation identifier for the read operation (string, max 128 chars)
- `decision_id`: CapAuth decision identifier authorizing the read (string, max 256 chars)
- `audit_log_url**: URL to owner system audit trail (string, max 512 chars, nullable)
- `evidence_chain**: Array of evidence reference objects (see schema)

---

## 3. Source-Side Authorization and Classification

### 3.1 Authorization Flow

The provider MUST implement the following authorization sequence:

```python
# PSEUDOCODE - Authorization contract
def read(context, query, home, *, currentness_verifier):
    # Step 1: Pre-read authorization check
    pre_decision = currentness_verifier.check_before_owner_read(context)
    if pre_decision != DecisionState.ALLOW:
        raise ScheduleUnavailableError("Pre-read authorization failed")

    # Step 2: Owner source read with tenant filtering
    try:
        data = owner_source.read(
            tenant_id=context.tenant_id,
            classification_filter=get_allowed_classifications(context),
            # ... query parameters
        )
    except Exception as e:
        raise ScheduleUnavailableError("Owner source unavailable")

    # Step 3: Post-read authorization check
    post_decision = currentness_verifier.check_after_owner_read(context)
    if post_decision != DecisionState.ALLOW:
        raise ScheduleUnavailableError("Post-read authorization failed")

    # Step 4: Transform and return
    return transform_to_projection(data)
```

### 3.2 Classification Policy

The provider MUST enforce source-side classification filtering:

**Allowed Classifications by Role**:
- `portfolio`: `public`, `internal`
- `project_manager`: `public`, `internal`, `confidential`
- `architect`: `public`, `internal`, `confidential`, `restricted`
- `service`: `public`, `internal`
- `team`: `public`, `internal`

**Fail-Closed Behavior**: If an item's classification exceeds the caller's allowed level, the provider MUST:
- Omit the item from results entirely (do not return redacted placeholders)
- Log the omission at WARN level with item_id and classification
- NOT count the item toward result totals

### 3.3 Tenant Filtering

The provider MUST filter all data by tenant_id from the CapAuth context:
- Every item MUST match `item.tenant_id == context.tenant_id`
- Every dependency edge MUST connect items within the same tenant
- Cross-tenant references MUST be silently dropped (logged at WARN)
- If no tenant_id is present in context, return HTTP 403

### 3.4 Role-Based Scoping

The provider MUST enforce role-based query constraints:

| Role | Allowed Scopes | Allowed Items |
|------|---------------|---------------|
| portfolio | estate | All items in tenant |
| project_manager | estate, project | Items in specified projects |
| architect | estate, service | Items affecting specified services |
| service | estate, service | Items for assigned service |
| team | estate, team | Items for assigned team |

**Invalid Role Mismatch**: If the query requests scope beyond the role's allowance, return HTTP 400 INVALID_SCHEDULE_SCOPE.

---

## 4. Freshness, Timeout, and Bounds Policy

### 4.1 Freshness TTL

**Cache Freshness**: The provider MUST respect the following time-to-live (TTL) policy:

| Data Type | Max TTL | Staleness Threshold |
|-----------|---------|---------------------|
| Schedule items | 5 minutes | 2 minutes |
| Dependency edges | 5 minutes | 2 minutes |
| ITIL overlays | 2 minutes | 1 minute |
| Architecture overlays | 10 minutes | 5 minutes |

**Freshness Check**: Before returning cached data, the provider MUST verify:
- `datetime.utcnow() - cached.last_sync_at <= max_ttl`
- If stale, trigger a refresh from owner source
- If refresh fails, return HTTP 503 SCHEDULE_UNAVAILABLE

### 4.2 Read Timeout

The provider MUST enforce strict read timeouts:

| Operation | Timeout | Action on Exceed |
|-----------|---------|------------------|
| Owner source HTTP request | 10 seconds | Return 503 SCHEDULE_UNAVAILABLE |
| Query validation | 100ms | Return 400 INVALID_SCHEDULE_SCOPE |
| Projection transformation | 500ms | Return 503 SCHEDULE_UNAVAILABLE |
| Full request (end-to-end) | 15 seconds | Return 503 SCHEDULE_UNAVAILABLE |

**Timeout Handling**: All timeouts MUST be fail-closed. Partial results are prohibited.

### 4.3 Input Bounds

The provider MUST enforce these input limits:

| Parameter | Max Value | Truncation/Rejection |
|-----------|-----------|---------------------|
| Query string length | 2048 bytes | Reject 400 |
| Role values | 5 enum values | Reject 400 if not enum |
| Scope values | 3 per query | Truncate excess, log |
| Item filters | 100 per query | Truncate excess, log |
| Date range | 365 days | Reject 400 if exceeded |
| Timezone | 64 chars | Reject 400 if exceeded |

### 4.4 Output Bounds

The provider MUST enforce these output limits:

| Output Type | Max Size | Truncation Behavior |
|-------------|----------|---------------------|
| Total response bytes | 10 MB | Truncate items list, add `truncated: true` |
| Schedule items | 10,000 | Truncate, add `truncated: true` |
| Dependency edges | 50,000 | Truncate, add `truncated: true` |
| Tags per item | 20 | Truncate excess, log |
| Evidence chain depth | 10 levels | Truncate, log |

**Truncation Semantics**:
- When truncating, prioritize items by: 1) status (in_progress first), 2) priority, 3) updated_at
- Always include a `truncated: true` flag in the response metadata
- Log truncation events at INFO level with counts
- Never truncate individual field values - truncate lists/arrays instead

### 4.5 Malformed Source Handling

The provider MUST handle malformed owner source data as follows:

| Malformation | Detection | Response |
|--------------|-----------|----------|
| Missing required field | Schema validation | Omit item, log WARN, continue |
| Invalid enum value | Schema validation | Omit item, log WARN, continue |
| Invalid date format | Date parsing | Omit item, log WARN, continue |
| Circular dependency | Graph traversal | Omit cycle edges, log WARN, continue |
| Self-referencing dependency | Edge validation | Omit edge, log WARN, continue |
| Duplicate item_id | Set insertion | Use first occurrence, log WARN |

**Malformed Source Threshold**: If more than 10% of items are malformed:
- Abort the read
- Return HTTP 503 SCHEDULE_UNAVAILABLE
- Log ERROR with malformed count and sample

### 4.6 Currentness Behavior

The provider MUST implement currentness checks:

**Pre-Read Currentness Check**:
- Verify CapAuth decision is not expired (max 5 minutes old)
- Verify tenant_id matches current session
- Verify role has not been revoked

**Post-Read Currentness Check**:
- Verify decision state is still ALLOW
- Verify no policy change occurred during read

**Currentness Failure**: Return HTTP 503 SCHEDULE_UNAVAILABLE with `retryable: true`.

---

## 5. Aggregate Forecast-History Source

### 5.1 Approved Forecast-History Provider

**Provider Name**: `SKCore Aggregate Throughput History` (placeholder pending source-owner approval)
**Source Type**: Time-series aggregate throughput data store
**Read Protocol**: HTTP/REST with OAuth2 client credentials
**Base URL**: TO BE APPROVED - Not specified in this contract
**Classification**: Internal - Restricted to SKDashboard service principal

**Note**: This contract DESIGNATES the need for an approved forecast-history source but does NOT SELECT one. Source-owner approval is required to pin an actual endpoint and credential.

### 5.2 Cohort Definition

The forecast-history provider MUST aggregate data by the following cohort:

**Cohort Name**: `estate_delivery_cohort`
**Cohort Members**: All teams and services within the tenant estate
**Exclusions**:
- Decommissioned services
- Archive-only projects (status = `archived`)
- Experimental work with `item_type = spike` unless explicitly included

**Cohort Freshness**: Data MUST be updated at least daily within the maintenance window (02:00-04:00 UTC).

### 5.3 Timing Basis

**Period Cadence**: 7-day rolling periods
**Period Anchor**: Periods start on Monday at 00:00:00 UTC
**Minimum History**: 13 periods (91 days)
**Recommended History**: 26 periods (182 days)
**Maximum History**: 52 periods (364 days)

**Period Schema**:
```json
{
  "period_id": "string format YYYY-Www (e.g., 2026-W35)",
  "period_start": "ISO 8601 date",
  "period_end": "ISO 8601 date",
  "items_completed": "integer >= 0",
  "total_story_points": "decimal >= 0",
  "total_hours": "decimal >= 0"
}
```

### 5.4 Remaining Work Source

**Remaining Work Calculation**: The forecast-history provider MUST calculate remaining work from:

**Source**: The same authoritative schedule owner reader defined in Section 2
**Calculation Method**:
- Sum of `estimated_hours` for all items with status in `backlog`, `refined`, `in_progress`
- Exclude items with `status` in `done`, `cancelled`, `blocked` (if blocked > 30 days)
- Weight `in_progress` items at 0.5 (assume half complete if no `actual_hours`)

**Remaining Work Refresh**: Calculated on-demand per forecast request, not cached.

### 5.5 History Window

**Default Window**: Last 13 complete periods (91 days)
**Minimum Window**: 8 periods (56 days) - below this, forecast MUST abstain
**Maximum Window**: 52 periods (364 days)
**Window Alignment**: Always use complete periods; partial current period is excluded

**History Window Validation**:
- If fewer than 8 periods available, forecast MUST return `state: "abstained"` with `abstention_reason: "insufficient_history"`
- If more than 52 periods available, use only the most recent 52

### 5.6 Exclusions

The forecast-history provider MUST exclude the following from throughput aggregation:

**Always Excluded**:
- Items with `item_type = spike`
- Items with `item_type = debt` and `priority = none`
- Items with `emergency_change = true`
- Items completed outside normal business hours (user-configurable, default 08:00-18:00 local time)

**Conditionally Excluded** (configurable via query):
- Items from specific teams
- Items from specific services
- Items with specific tags

**Exclusion Representation**: Each exclusion MUST be recorded as:
```json
{
  "period_id": "2026-W35",
  "timing_basis": "emergency_change",
  "reason": "Emergency changes excluded from throughput baseline"
}
```

### 5.7 Forecast Representation

The forecast MUST be represented within the schedule projection as follows:

**Location**: Under `projection.forecast` (optional field, present only when forecast available)

**Schema Compliance**: MUST validate against the forecast schema in `control-plane-schedule-projection.v1.0.0.schema.json`

**Required Fields**:
- `schema_version`: "1.0.0"
- `artifact_kind`: "schedule_forecast"
- `state`: "ready" or "abstained"
- `method`: "skcore_throughput_v1"
- `calculation_owner`: "skcore_aggregate_history_provider"
- `cohort`: "estate_delivery_cohort"
- `scope`: "estate"
- `history_window`: Object with `start` and `end` dates
- `sample_periods`: Integer (number of periods in history)
- `period_cadence_days`: 7
- `remaining_work`: Integer (hours)
- `iterations`: 10000 (fixed for Monte Carlo)
- `seed`: Random seed used for reproducibility
- `assumptions`: Array of assumption strings
- `exclusions`: Array of exclusion objects
- `individual_ranking_prohibited`: true
- `completion_quantiles_periods`: Object with `p50`, `p85`, `p95` period counts
- `writes_owner_records`: false

**Conditional Fields**:
- `abstention_reason`: Required when `state = "abstained"`
- `milestone_confidence`: Optional, float 0-1, for milestone-specific forecasts

**Forecast Confidence Levels**:
- `p50`: Median completion period (50th percentile)
- `p85`: 85th percentile completion period
- `p95`: 95th percentile completion period

**No Individual Ranking**: The forecast MUST NEVER rank or identify individual contributors. This is a system-level capability forecast only.

---

## 6. OpenAPI and Route Alignment

### 6.1 Role Vocabulary Mismatch

The OpenAPI contract specifies role values: `portfolio`, `project_manager`, `architect`, `service`, `team`

The implemented route accepts role values: `project-manager`, `operator`, `architect`, `service`, `team`

**Resolution Without Broadening Access**:

1. **Map Project-Manager to Project-Manager**: 
   - OpenAPI: `project_manager` 
   - Implementation: `project-manager`
   - **Action**: The provider MUST accept both values and treat them identically

2. **Map Portfolio to Operator** (if appropriate):
   - OpenAPI: `portfolio` 
   - Implementation: `operator`
   - **Action**: The provider MUST map `portfolio` to `operator` semantics if the tenant's operator role has portfolio-level access. Otherwise, reject `portfolio` with HTTP 403.

3. **Accept Architect, Service, Team Unchanged**:
   - These match between OpenAPI and implementation

**Implementation Requirement**: The provider MUST normalize role inputs:
```python
ROLE_NORMALIZATION = {
    "project_manager": "project-manager",
    "portfolio": "operator",  # Conditional on tenant policy
    # architect, service, team pass through
}

def normalize_role(role: str, context) -> str:
    if role in ROLE_NORMALIZATION:
        mapped = ROLE_NORMALIZATION[role]
        # Verify tenant policy allows this mapping
        if role == "portfolio" and not context.tenant_allows_portfolio_as_operator:
            raise ForbiddenError("portfolio role not allowed")
        return mapped
    return role
```

### 6.2 Query Parameter Mismatch

The OpenAPI contract specifies parameters:
- `portfolio_id`, `project_id`, `service_id`, `team_id`
- `timezone`
- `projection_version`

The implemented route accepts:
- `role`, `scope`, `window`, `baseline`, `service`, `lens`, `timezone`, `selected_item`

**Resolution Without Broadening Access**:

1. **Accept OpenAPI Parameters as Aliases**:
   - `portfolio_id` maps to scope filtering
   - `project_id` maps to scope filtering  
   - `service_id` maps to `service` parameter
   - `team_id` maps to scope filtering
   - `projection_version` is accepted for cache invalidation but not used for filtering

2. **Maintain Existing Parameter Semantics**:
   - `role`, `scope`, `window`, `baseline`, `service`, `lens`, `timezone`, `selected_item` continue to work exactly as implemented

3. **No New Capabilities**:
   - The OpenAPI parameters are parsed but MUST NOT enable queries that were not already possible
   - If an OpenAPI parameter would broaden access (e.g., `portfolio_id` without role verification), reject it

**Implementation Requirement**: The provider MUST accept both parameter sets:
```python
def parse_query(query_params):
    # Accept both OpenAPI and legacy parameters
    result = {}
    
    # Legacy parameters (primary)
    for key in ["role", "scope", "window", "baseline", "service", "lens", "timezone", "selected_item"]:
        if key in query_params:
            result[key] = query_params[key]
    
    # OpenAPI parameters (aliases, subject to role validation)
    if "portfolio_id" in query_params:
        if result.get("role") not in ["portfolio", "operator"]:
            raise ForbiddenError("portfolio_id requires portfolio role")
        result["scope"] = f"portfolio:{query_params['portfolio_id']}"
    
    # Similar mapping for project_id, team_id...
    
    return result
```

### 6.3 Capability/Scope Mismatch

The OpenAPI contract requires: `skdashboard.schedule.read`
The implemented route uses: `skdashboard.read`

**Resolution Without Broadening Access**:

1. **Accept Both Capabilities**:
   - The provider MUST accept either `skdashboard.schedule.read` OR `skdashboard.read`
   - Both MUST map to the same authorization checks

2. **No New Permissions**:
   - Accepting `skdashboard.schedule.read` does NOT grant any new access
   - The underlying authorization logic remains unchanged

3. **Audit Logging**:
   - Log which capability was presented for audit trail

**Implementation Requirement**:
```python
def validate_capability(context):
    allowed_capabilities = ["skdashboard.schedule.read", "skdashboard.read"]
    if context.capability not in allowed_capabilities:
        raise ForbiddenError("Invalid capability")
    # Both map to same authorization logic
```

---

## 7. Provider Interface Definition

### 7.1 Python Protocol

```python
from typing import Protocol, Dict, Any, Optional
from datetime import datetime
from dataclasses import dataclass

class DecisionState:
    ALLOW = "allow"
    DENY = "deny"
    ABSTAIN = "abstain"

@dataclass
class CapAuthContext:
    tenant_id: str
    user_id: Optional[str]
    role: str
    capability: str
    decision_id: str
    issued_at: datetime
    expires_at: datetime
    classifications: list[str]
    
class CurrentnessVerifier(Protocol):
    def check_before_owner_read(self, context: CapAuthContext) -> DecisionState:
        """Verify authorization is current before reading from owner source."""
        ...
    
    def check_after_owner_read(self, context: CapAuthContext) -> DecisionState:
        """Verify authorization is still current after reading from owner source."""
        ...

@dataclass
class ScheduleQuery:
    role: str
    scope: str
    window: str
    baseline: str
    service: str
    lens: str
    timezone: str
    selected_item: Optional[str]
    # OpenAPI aliases
    portfolio_id: Optional[str] = None
    project_id: Optional[str] = None
    team_id: Optional[str] = None
    projection_version: Optional[str] = None

@dataclass
class HomeContext:
    """Application home context (environment, config, etc.)"""
    environment: str
    config: Dict[str, Any]
    logger: Any

class ScheduleProvider(Protocol):
    """
    Protocol for the schedule projection provider.
    
    Implementations MUST fail closed on any error or contract violation.
    """
    
    def read(
        self,
        context: CapAuthContext,
        query: ScheduleQuery,
        home: HomeContext,
        *,
        currentness_verifier: CurrentnessVerifier
    ) -> Dict[str, Any]:
        """
        Read a schedule projection from the authoritative owner source.
        
        Args:
            context: Typed CapAuth authorization context
            query: Validated and normalized query parameters
            home: Application home context
            currentness_verifier: Verifier for authorization currentness
        
        Returns:
            A dictionary validating against control-plane-schedule-projection.v1.0.0.schema.json
        
        Raises:
            ScheduleUnavailableError: If the schedule cannot be provided (HTTP 503)
            InvalidScopeError: If the query parameters are invalid (HTTP 400)
            ForbiddenError: If authorization fails (HTTP 403)
        """
        ...

class ScheduleForecastProvider(Protocol):
    """
    Protocol for the schedule forecast provider.
    
    This provider composes the schedule provider with the forecast-history provider
    to generate probabilistic completion forecasts.
    """
    
    def read(
        self,
        context: CapAuthContext,
        query: ScheduleQuery,
        home: HomeContext,
        *,
        currentness_verifier: CurrentnessVerifier
    ) -> Dict[str, Any]:
        """
        Read a schedule forecast.
        
        The forecast MUST be grounded in approved aggregate throughput history
        and MUST follow the representation defined in Section 5.7.
        
        Returns:
            A dictionary with forecast data, valid under the schedule schema
        
        Raises:
            ScheduleForecastUnavailableError: If the forecast cannot be provided (HTTP 503)
        """
        ...
```

### 7.2 Error Contract

All errors MUST return JSON matching:

```json
{
  "code": "ERROR_CODE",
  "message": "Human-readable error message",
  "retryable": true|false,
  "request_id": "correlation-id"
}
```

**Error Codes**:
- `SCHEDULE_UNAVAILABLE`: Provider cannot obtain schedule (503, retryable)
- `SCHEDULE_FORECAST_UNAVAILABLE`: Provider cannot generate forecast (503, retryable)
- `INVALID_SCHEDULE_SCOPE`: Query parameters invalid (400, not retryable)
- `FORBIDDEN`: Authorization failed (403, not retryable)
- `STALE_DATA`: Cached data exceeds staleness threshold (503, retryable)

---

## 8. Test Requirements

### 8.1 Contract Tests

The following tests MUST be implemented and pass before any implementation review:

#### 8.1.1 Schema Validation Tests
1. Valid schedule projection passes JSON schema validation
2. Missing required field fails schema validation
3. Invalid enum value fails schema validation
4. Truncated response includes `truncated: true` flag
5. Forecast with `state: ready` includes all required fields
6. Forecast with `state: abstained` includes `abstention_reason`

#### 8.1.2 Authorization Tests
1. Request without CapAuth context returns 403
2. Request with expired decision returns 503
3. Request with revoked role returns 403
4. Request exceeding classification returns filtered results (not error)
5. Cross-tenant request returns 403
6. Pre-read and post-read currentness checks both enforced

#### 8.1.3 Role and Scope Tests
1. Portfolio role can read entire estate
2. Project manager can read assigned projects only
3. Service role can read assigned service only
4. Team role can read assigned team only
5. Invalid role returns 400
6. Scope exceeding role returns 400

#### 8.1.4 Freshness and Timeout Tests
1. Stale cache (exceeds TTL) triggers refresh
2. Failed refresh returns 503
3. Read timeout returns 503
4. End-to-end timeout returns 503
5. Fresh data within TTL is served from cache

#### 8.1.5 Bounds and Truncation Tests
1. Query string exceeding 2048 bytes returns 400
2. Response exceeding 10 MB is truncated
3. Items exceeding 10,000 are truncated
4. Truncation adds `truncated: true` to response
5. Tags per item exceeding 20 are truncated
6. Truncation prioritizes in_progress items

#### 8.1.6 Malformed Source Tests
1. Missing required field omits item, logs warning
2. Invalid enum value omits item, logs warning
3. Circular dependency is detected and edges omitted
4. Self-referencing dependency is omitted
5. Duplicate item_id uses first occurrence
6. More than 10% malformed returns 503

#### 8.1.7 Forecast Tests
1. Forecast with < 8 periods returns abstained
2. Forecast with 8+ periods returns ready with quantiles
3. Forecast p50 <= p85 <= p95 ordering enforced
4. Forecast exclusions are recorded in response
5. Forecast `individual_ranking_prohibited` is always true
6. Forecast `writes_owner_records` is always false

#### 8.1.8 OpenAPI Alignment Tests
1. `project_manager` and `project-manager` both accepted
2. `portfolio` maps to `operator` when allowed by tenant
3. `portfolio_id` parameter works with portfolio role
4. `skdashboard.schedule.read` and `skdashboard.read` both accepted
5. Invalid role values return 400

### 8.2 Negative Tests

The following negative tests MUST pass:

1. **No Provider Injection**: Request with `schedule_provider = None` returns 503
2. **Missing Context**: Request without CapAuth context returns 503
3. **Missing Verifier**: Request without currentness verifier returns 503
4. **Owner Source Down**: Owner source 500 returns 503 to client
5. **Owner Source Timeout**: Owner source timeout returns 503 to client
6. **Owner Source Malformed JSON**: Returns 503, not 500
7. **Invalid Tenant ID**: Returns 403
8. **Classification Escalation**: High-classification item not returned to low-privilege role
9. **Empty Result Set**: Valid request with no matches returns empty array, not error
10. **Concurrent Requests**: Multiple concurrent requests do not corrupt state

### 8.3 Security Tests

The following security tests MUST pass:

1. **SQL Injection**: Query parameters are parameterized, not concatenated
2. **Command Injection**: No shell command execution with user input
3. **XXE**: XML parser (if used) disables external entities
4. **Path Traversal**: File paths are validated before access
5. **SSRF**: Owner source URLs are allowlisted
6. **Credential Leakage**: Errors never include credentials or tokens
7. **Information Disclosure**: Errors never reveal internal state
8. **Rate Limiting**: Excessive requests return 429

### 8.4 Static Analysis

The following static analysis checks MUST pass:

1. **Type Checking**: `mypy` with strict mode, no errors
2. **Security Linting**: `bandit` with no high-severity findings
3. **Code Quality**: `flake8` with no violations
4. **Import Safety**: No vulnerable dependencies (checked via `safety` or `pip-audit`)
5. **Secret Scanning**: No hardcoded secrets (checked via `trufflehog` or `gitleaks`)

---

## 9. Independent Review Requirements

Before any implementation card may proceed, this contract MUST be independently reviewed by:

### 9.1 Required Reviewers

1. **Source Owner**: The owner of the authoritative schedule source (SKCore or designated system)
2. **Security Review**: Security team member
3. **Architecture Review**: SKDashboard architecture owner
4. **Product Review**: Product owner for schedule functionality

### 9.2 Review Checklist

Reviewers MUST confirm:

- [ ] The designated authoritative owner source is approved and accessible
- [ ] The designated forecast-history source is approved and accessible
- [ ] Classification policy aligns with enterprise data classification standards
- [ ] Role mappings do not broaden access beyond current implementation
- [ ] Timeout and bounds are appropriate for the infrastructure
- [ ] Truncation semantics preserve essential information
- [ ] Malformed source handling is safe and deterministic
- [ ] Forecast methodology is sound and calibrated
- [ ] No individual ranking or PII is exposed in forecasts
- [ ] All tests cover positive, negative, and security cases
- [ ] Static analysis passes with no critical findings

### 9.3 Review Sign-Off

Reviewers MUST provide a written sign-off including:

- Reviewer name and role
- Date of review
- Approval status (APPROVED, APPROVED_WITH_COMMENTS, REJECTED)
- Any required changes
- Commit hash of the reviewed contract

**Sign-off Format**:
```json
{
  "reviewer": "name",
  "role": "source-owner|security|architecture|product",
  "date": "ISO 8601 date",
  "status": "APPROVED|APPROVED_WITH_COMMENTS|REJECTED",
  "comments": "Any comments or required changes",
  "contract_commit": "commit hash",
  "reviewer_approval": "signature or identifier"
}
```

---

## 10. Implementation Prohibitions

This contract does NOT authorize implementation. The following are PROHIBITED until this contract is independently approved:

1. **NO Implementation Code**: No Python implementation of ScheduleProvider or ScheduleForecastProvider
2. **NO Source Integration**: No actual connection to owner systems
3. **NO Deployment**: No deployment to any environment
4. **NO Configuration Changes**: No changes to live configuration
5. **NO Credential Usage**: No use of production credentials
6. **NO Provider Calls**: No actual calls to owner or forecast-history sources
7. **NO Database Writes**: No writes to any database or owner system
8. **NO API Registration**: No registration of live endpoints

**What IS Allowed**:
- Contract definition (this document)
- Test code that validates the contract (mocks only)
- Static analysis of the contract
- Documentation updates

---

## 11. Version History

| Version | Date | Author | Change Summary |
|---------|------|--------|----------------|
| 1.0.0 | 2026-08-30 | pi-glm-chiap01-2a4bb204 | Initial contract definition from trace card 36f3396c |

---

## 12. References

1. **Trace Card 36f3396c**: `/home/skuser01/.skcapstone/coordination/tasks/36f3396c-skdashlive-smoke-forecast-trace-r1s-trac.json`
2. **Trace Evidence**: `/home/skuser01/.skcapstone/evidence/work/36f3396c/read-only-source-audit.json`
3. **Base Commit**: `e08d9df73d0cec9ea705b422c5f532ec6543e5e8`
4. **Schema Contract**: `docs/contracts/schedule/v1.0.0/control-plane-schedule-projection.v1.0.0.schema.json`
5. **OpenAPI Contract**: `docs/contracts/schedule/v1.0.0/openapi.control-plane-schedule.v1.0.0.json`
6. **Forecast Evidence**: `docs/evidence/SKCP-30-FORECAST-ENGINE-2026-08-24.md`

---

## 13. Appendices

### Appendix A: Role Classification Matrix

| Role | Public | Internal | Confidential | Restricted |
|------|--------|----------|--------------|------------|
| portfolio | ✓ | ✓ | ✗ | ✗ |
| project_manager | ✓ | ✓ | ✓ | ✗ |
| architect | ✓ | ✓ | ✓ | ✓ |
| service | ✓ | ✓ | ✗ | ✗ |
| team | ✓ | ✓ | ✗ | ✗ |

### Appendix B: Truncation Priority Algorithm

```python
def truncation_priority(item):
    """Higher priority = kept longer when truncating"""
    priority = 0
    
    # Status priority: in_progress first
    if item.status == "in_progress":
        priority += 1000
    elif item.status in ["backlog", "refined"]:
        priority += 500
    elif item.status == "review":
        priority += 300
    
    # Priority level
    priority += {
        "critical": 100,
        "high": 75,
        "medium": 50,
        "low": 25,
        "none": 0
    }.get(item.priority, 0)
    
    # Recency: more recently updated = higher priority
    days_since_update = (datetime.utcnow() - item.updated_at).days
    priority -= days_since_update
    
    return priority
```

### Appendix C: Forecast Abstention Reasons

| Reason | Condition |
|--------|-----------|
| `insufficient_history` | Fewer than 8 periods available |
| `no_cohort_data` | No items in cohort for any period |
| `high_variance` | Coefficient of variation > 2.0 |
| `trend_break` | Significant trend change detected |
| `configuration_disabled` | Forecast explicitly disabled for tenant |

---

**END OF CONTRACT v1.0.0**

This contract is PROPOSED and requires independent review before any implementation.
