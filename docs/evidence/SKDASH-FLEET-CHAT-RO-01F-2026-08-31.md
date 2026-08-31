# SKDashboard fleet chat truth repair

Date: 2026-08-31

Card: `d8f3a1c6`

Parent: `c0f1d7a2`

Independent finding: `64c78aec` FAIL, evidence SHA256
`c31deaaab6e4cdea6f10f034053cfb098747bd8f6a72b43dbd6730909979bdf0`

Verdict: `PASS_FOR_REVIEW`

This is source-only repair evidence. It authorizes no merge, deployment,
service action, SKMail write, credential action, protected-data output,
cleanup, or unrelated mutation.

## Exact repaired source

- Branch: `candidate/c0f1d7a2-fleet-chat-reconcile`
- Commit: `7b44b66431babb06c2a118b558019cea26df9910`
- Tree: `14ba8cfb9041a39c09c60927763e1ff0eec7bc4a`
- Parent: `e45eeff9f62bda88ee4c18ee8ade6b8536bfe227`
- Full-index binary diff SHA256:
  `aa9c3abb17f6c1d5357d6afe6c40509c8a43b2432cb8add93e842d422e532ece`

The repair changes exactly three paths:

- `src/skdashboard/fleet_chat.py`, 12,943 bytes, SHA256
  `3a700311d3b97f936f62681d187ceb7152d4e4d77924a78a8729c9e9c9601811`
- `src/skdashboard/static/js/fleet_chat.js`, 6,829 bytes, SHA256
  `934d89f168c9e16be5dd0e8388859f6144e994f7a8524b912e151281f9533b1f`
- `tests/test_fleet_chat.py`, 10,012 bytes, SHA256
  `6727ea764ab0b15f123359d346c8192ec95e43d211b163196662d9a3b7fe5a4f`

## Repaired boundaries

- Accepted timestamps are parsed to timezone-aware UTC instants before heap,
  watermark, channel-last, and channel-order comparisons. Variable ISO
  fractional widths no longer affect chronology.
- Freshness declares a 60-second TTL, exposes age and future offset, reports
  stale observations as `stale`, and reports future observations as
  `unavailable`.
- Every non-empty recipient is bounded, scrubbed with the existing secret and
  protected-payload redactor, and validated as an identity before it can reach
  a message or channel. A redacted recipient becomes only `[REDACTED]`.
- The existing authorization, Tenant binding, writer-derived sender and host,
  newest-400 bound, invalid-record counts, path hiding, responsive UI, and
  no-write boundary are unchanged.

## Verification

- Exact qualified focused suite: 58 passed, 1 inherited warning.
- Exact qualified full suite: 675 passed, 4 skipped, 185 inherited warnings.
- Ruff over source and focused tests: PASS.
- Python compile over `src/skdashboard`: PASS.
- `git diff --check`: PASS.
- Prohibited Unicode dash scan over the three changed paths: PASS.
- Offline `detect-secrets` over the three changed paths: zero findings.

The focused regressions reproduce mixed ISO width ordering, channel ordering,
stale and future freshness, secret-shaped recipient redaction, and invalid
recipient exclusion.

## Rollback

Decline integration or normally revert commit
`7b44b66431babb06c2a118b558019cea26df9910`. No data or service state changed.
