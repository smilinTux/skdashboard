# SKDashboard fleet chat source reconciliation

Date: 2026-08-31

Card: `c0f1d7a2`

Verdict: `PASS_FOR_REVIEW`

This is source-only reconciliation evidence. It does not authorize merge,
deployment, service changes, mail writes, credentials, cleanup, or any external
action.

## Pinned source and runtime

- GitHub main: `7642e3423039a1a8a314ba85ec8b73fb9014d8ae`
- Lumina branch: `feat/fleet-chat-panel`
- Lumina commit: `9afb97b317f89eea417bfba55bdbe7d487784ebb`
- Lumina tree: `21fdfa595243b10c7d1f435130a0a63f57513462`
- Lumina parent: `7642e3423039a1a8a314ba85ec8b73fb9014d8ae`
- Lumina diff: 22 paths, 421 insertions, no deletions
- Reconciled Lumina commit: `0704444a96c34b48dfdcdc39010efb1dcb3aeabb`
- Reconciled Lumina tree: `21fdfa595243b10c7d1f435130a0a63f57513462`
- Source candidate commit: `a6f86f4390ae990879d500b8dd98606ed2d2f47d`
- Source candidate tree: `6fe0d2faef31d60b9bbc8d3ae5cae2a9fb25f845`
- Candidate branch: `candidate/c0f1d7a2-fleet-chat-reconcile`
- Deployed chiap04 distribution observed: `0.1.93.dev1+g9afb97b`
- Deployed service observed: `skcapstone-dashboard.service`, active since
  `2026-08-31 02:25:34 CDT`, bound by its existing command to port 7778

The Lumina commit parent exactly equals fetched GitHub main, and the reconciled
Lumina tree exactly equals the source tree at `9afb97b`.

## Exact source candidate paths

The candidate changes 27 paths against pinned main:

- `src/skdashboard/control_plane_api.py`
- `src/skdashboard/dashboard.py`
- `src/skdashboard/fleet_chat.py`
- `src/skdashboard/live_control_plane.py`
- `src/skdashboard/read_only.py`
- `src/skdashboard/static/ai.html`
- `src/skdashboard/static/architecture.html`
- `src/skdashboard/static/assistant.html`
- `src/skdashboard/static/board.html`
- `src/skdashboard/static/cmdb.html`
- `src/skdashboard/static/cockpit.html`
- `src/skdashboard/static/css/board.css`
- `src/skdashboard/static/economy.html`
- `src/skdashboard/static/fleet.html`
- `src/skdashboard/static/fleet_chat.html`
- `src/skdashboard/static/governance.html`
- `src/skdashboard/static/js/fleet_chat.js`
- `src/skdashboard/static/models.html`
- `src/skdashboard/static/overview.html`
- `src/skdashboard/static/projects.html`
- `src/skdashboard/static/reliability.html`
- `src/skdashboard/static/reports.html`
- `src/skdashboard/static/schedule.html`
- `src/skdashboard/static/trust.html`
- `tests/test_dashboard_link_accessibility.py`
- `tests/test_fleet_chat.py`
- `tests/test_live_control_plane_composition.py`

Exact aggregate diff: 829 insertions and 6 deletions.

## Review findings and reconciliation

The deployed source returned HTTP 200 without authentication during the bounded
reach probe. It also trusted record-level sender and host claims over the writer
filename, silently discarded invalid records, returned an absolute source path,
returned unredacted subject and body text, read unbounded history into memory,
and did not expose freshness or partial state.

The source candidate preserves Lumina's read-only projection and presentation,
then applies these controls:

- `GET /api/v1/fleet-chat` uses the existing protected control-plane wrapper,
  `skdashboard.read`, exact-origin checks, no-store responses, rate control, and
  the authenticated browser session bridge.
- The typed live composition binds the target to the exact configured Tenant.
- Only regular, non-symlink SKMail writer files with a recognized fleet host are
  read. Sender and host are derived from the writer filename. Conflicting claims
  are excluded and counted.
- Output is the newest 400 verified messages with bounded file, line, subject,
  body, recipient, and in-memory limits.
- Secret, credential, token, bearer, key, capability, private-key, API-key, and
  protected-payload values are redacted. Unselected fields and raw invalid lines
  are never returned.
- Safe error categories and counts produce explicit partial state without raw
  record content or filenames.
- The API retains messages, channels, speakers, total, source, and read-only
  fields and adds verified agent, sender, host, lane, card, redaction, freshness,
  source-total, partial, and invalid-record fields.
- The authenticated read-only runtime serves the page and asset. The transcript
  reports freshness and partial state, preserves navigation and icon invariants,
  carries accessible labels, and collapses to one column at 900 pixels.
- POST is method-denied, and no send, reply, mutation, daemon, socket, or service
  path exists.

At `2026-08-31T08:05:16Z`, a source-only local dry projection returned the newest
400 of 3,079 valid records. It excluded 65 records using category counts only.
All 400 returned messages had verified sender identity and bounded text fields.

## Verification

- Exact qualified full suite:
  `uv run --isolated --with-requirements requirements-qualified.txt --with pytest python -m pytest tests/ -q`
  resulted in `672 passed, 4 skipped, 185 warnings in 58.68s`.
- Exact qualified focused suite across fleet chat, live composition, read-only
  runtime, navigation accessibility, and protected read API resulted in
  `56 passed, 1 warning in 3.69s`.
- `python -m ruff check src/skdashboard tests/test_fleet_chat.py tests/test_live_control_plane_composition.py`
  passed.
- `git diff --check` passed.
- `detect-secrets` with all installed plugins and offline verification found no
  result in the new fleet-chat test after explicit fake-fixture allowlists.
- No prohibited dash characters were found in changed Python, HTML, or
  JavaScript paths.

Warnings are inherited dependency deprecations. The four skips are the existing
suite skips.

## Limitations and follow-up

- Cursor pagination is not implemented. The explicit current contract remains
  the newest 400 verified messages.
- Authorization is Tenant-level fleet-chat reach, not recipient-specific mailbox
  delegation. This is an intentional reach change and must be reviewed as such.
- The currently deployed chiap04 source remains the unauthenticated Lumina wheel.
  This card made no deployment or service change.
- There is no data migration. Rollback is to decline integration or revert the
  candidate commits. SKMail data is unchanged.
