# SKDashboard governed development automation

**Decision:** Chef selects trust **Option A** for SKDashboard. This is a development-only, local control plane. It is not a production deployment mechanism and it cannot authorize external legal action.

- **Audience:** `skdashboard`
- **Principal type:** `human`
- **Status:** `active_local_only`
- **Signer boundary:** DCE (the dashboard capability enforcement boundary) is the only component that accepts a capability for an automation action. Jarvis and Atlas never sign for one another and never share a signer or credential.
- **Decision owner:** Chef
- **Revision:** 1 (superseding revisions are append-only records)

## Trust and capability contract

Jarvis and Atlas have distinct, named identities: `jarvis-development-automation` and `atlas-development-automation`. Each identity has its own revocation record, audit stream, and short-lived capability issuer. A capability is bound to identity, audience (`skdashboard`), environment (`development`), Matter scope, action, issued-at, expiry, nonce, and revision. TTL is at most 15 minutes; expired, not-yet-valid, audience-mismatched, revision-mismatched, malformed, or revoked capabilities fail closed.

The allowed development actions are:

| Identity | Allowed actions | Matter scope |
| --- | --- | --- |
| `jarvis-development-automation` | authenticated UI smoke, authenticated API smoke, health, sanitized diagnostics, test-state workflow, regression report, safe rollback | explicitly named SKDashboard development Matter only |
| `atlas-development-automation` | authenticated UI smoke, authenticated API smoke, health, sanitized diagnostics, test-state workflow, regression report, safe rollback | explicitly named SKDashboard development Matter only |

There are no wildcard principals, wildcard Matter scopes, shared signing keys, secret-reading actions, public exposure actions, protected-data actions, or unrelated Matter permissions. Diagnostic output is schema-filtered and redacts tokens, cookies, authorization headers, private keys, and personal data before audit or report publication.

## Verification, revision, rollback, and audit

DCE verifies the signature and all capability claims before dispatch, then checks the per-identity revocation set and action/environment/Matter policy. Verification is independent of lifecycle links: a lifecycle event alone is never a verdict. Every request produces separate structural and evidence events containing request ID, identity, capability ID, policy revision, decision, redaction result, and hashed sanitized output. CardStore event writers serialize JSON and parse each line before append; no JSON is assembled by string concatenation.

Policy revisions are monotonic and immutable. A revision is activated only after signature verification and a recorded Chef decision. Rollback selects a previously verified revision, emits a rollback audit event, and revokes capabilities minted under the bad revision. Revocation is per identity, so revoking Jarvis does not revoke Atlas, and vice versa. Failures, missing policy, unavailable DCE, invalid signatures, and audit-write failure all fail closed.

## Governed development loop

1. Request a short-lived capability for one named identity, environment, Matter, and action.
2. DCE verifies audience, signature, revision, expiry, revocation, and least-privilege policy.
3. Run authenticated UI and API smoke tests, health checks, or a development test-state workflow.
4. Collect only sanitized diagnostics and emit a hashed regression report with pass/fail details.
5. On regression, stop the loop, preserve evidence, and use the previously verified revision for safe rollback. Rollback never contacts a court, regulator, opposing party, or external legal system.
6. Revoke the capability and retain the audit record.

The loop is local-only and development-scoped. Credentials are injected by the test runner and are never printed, persisted in reports, or returned by diagnostics.

## End-to-end qualification matrix

The qualification must pass for **each identity** (`jarvis-development-automation`, `atlas-development-automation`) and each action (UI smoke, API smoke, health, sanitized diagnostics, test-state workflow, regression report, rollback): valid capability is accepted; expired, revoked, wrong audience, wrong Matter, wrong environment, wrong action, wrong revision, malformed signature, wildcard principal, and missing capability are rejected. It must additionally assert that diagnostic output contains no secret patterns, audit records contain hashes and no raw credentials, and rollback restores the last verified revision without external calls.

A qualification report records test command, revision, identity/action matrix, sanitized output hash, and report hash. This document is the architecture record and policy boundary; runtime qualification evidence is linked from the coordination card.
