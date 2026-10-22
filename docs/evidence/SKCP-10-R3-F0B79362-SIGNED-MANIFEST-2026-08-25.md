# SKCP-10-R3 signed control-plane manifest repair

Card: `f0b79362`
Agent: `jarvis`
Scope: isolated SKDashboard worktree only

## Accepted repair

The SKDashboard module producer and typed client now converge on signed
module manifest schema `1.3`. The advertised control-plane API contract stays
independent at `1.1.0` and is validated as its own exact profile.

The client requires a caller-pinned canonical manifest hash, a detached
signature, an allowed signature suite, and an injected verifier. It rejects
downgrade and unknown schema versions, unsigned manifests, tampering, wrong
origin, wrong audience, malformed signature suites, malformed relative paths,
and incompatible control-plane contracts.

The public synthetic fixture carries the exact schema `1.3` payload used by
the SKOS fixture, with synthetic signature metadata added only for client
tests. The read-only Dashboard surface reuses the producer and retains its
existing least-privilege omission of the operator block.

## Changed files

- `src/skdashboard/skdashboard_manifest.py`
- `src/skdashboard/control_plane_client.py`
- `src/skdashboard/control_plane_fixture.py`
- `src/skdashboard/read_only.py`
- `tests/test_control_plane_client.py`
- `tests/test_control_plane_mcp.py`

## Verification

- Focused manifest, client, contract, read-only, and session tests: `80 passed, 2 warnings`.
- MCP focused tests: `7 passed`.
- Full Dashboard suite: `566 passed, 8 warnings`.
- Read-only SKOS signed discovery and shell tests: `39 passed, 7 warnings`.
- Read-only Atlas adapter, profile, and conformance tests: `46 passed, 1 skipped, 287 warnings`.
- Ruff: passed for all changed Python files.
- Python bytecode compilation: passed.
- `git diff --check`: passed.
- The warnings are existing jsonschema `RefResolver` deprecations.

## Cross-tree read-only checks

- SKDashboard base tree was pinned to `8ee5ea227f1f654980bc6c3b7e4b434ac07e27c9`.
- SKOS repaired index tree was read only and matched `510e53dd5baf95db8f3ff0e1db7f1b06718dd54c`.
- Atlas head tree was read only at `814534dbbdc0e38a17b2bd8dcc3fc669c7aa7432`. Its separate repair worktree was not modified.
- No merge, push, deployment, credential, grant, protected-data, or external action occurred.

## Rollback

The repair is a worktree-local uncommitted patch. Reversal is available by
applying the recorded patch in reverse or discarding this isolated worktree.
No data migration or production state change occurred.
