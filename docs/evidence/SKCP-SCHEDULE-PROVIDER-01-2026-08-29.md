# SKCP Schedule projection provider evidence

Card: `1d5a9a62`

## Candidate

- Branch: `feat/1d5a9a62-schedule-projection-provider`
- Pull request: https://github.com/smilinTux/skdashboard/pull/116
- Candidate commit: `65024f4`
- No merge, deployment, credential, protected-data, provider, or external runtime action was performed.

## Contract and source mapping

`src/skdashboard/dashboard_schedule.py` pins schema `1.0.0`, authorization target
`/api/v1/schedule/projection`, capability `skdashboard.read`, source currentness
of at most 300 seconds by default, exact tenant, role and estate scope, and a
field-level `FIELD_PROVENANCE` map. The source protocol must return a single
atomic, policy-filtered snapshot. It receives the tenant and authorization
target before any records are read.

The provider maps only typed canonical fields. It does not accept card title or
description text as date, owner, progress, dependency, or forecast evidence.
Baseline, planned, actual, forecast, dependency, release, milestone, and ITIL
window semantics remain typed. Date states preserve known, unknown, stale,
partial, unavailable, policy-filtered, and not-applicable distinctions.

## Fail-closed controls

Focused tests cover tenant, role, scope, owner-policy target, authorization,
currentness before read, currentness during read, post-validation currentness,
stale source, unavailable source, policy-filtered records, and hidden dependency
IDs. All provider failures cross the HTTP boundary as the constant authorized
Schedule unavailable response.

## Qualification

- Focused Schedule, contract, API, and performance lane: 30 passed in 0.59s.
- Synthetic 2,000-item projection: 0.17s.
- Accessibility and workspace contract lane: 21 passed in 27.69s.
- Ruff: pass.
- Git diff check: pass.
- Local secret pattern scan: pass.
- GitHub gitleaks: pass.
- GitGuardian: pass.
- GitHub build: pass.
- Full suite: 588 passed and 2 unrelated pre-existing consent tests failed on
  synthetic cards without foldable CardStore cores. The same failures reproduce
  locally and in Python 3.10 and 3.12 CI. No Schedule test failed.
- Independent review remains assigned to downstream card `56aeffcd`; this
  implementation card does not self-review.

## Safety

All fixtures are public-synthetic. The provider is read-only and contains no
owner mutation, reschedule, dispatch, deployment, credential, or external action
path. Roadmap, Gantt, and Flow lens values are excluded from source selection and
projection identity, yielding identical serialized projection bytes for equal
scope and timezone.
