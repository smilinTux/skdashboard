# Independent review of SKDashboard exact fail-closed runtime policy composition

**Card:** cda3da56
**Reviewer:** pi-glm-chiap03-cda3da56
**Review date:** 2026-08-30T03:10:00Z
**Verdict:** BLOCKED

## Ownership verification

- Card cda3da56 is claimed by: `pi-glm-chiap03-cda3da56`
- Current agent: `pi-glm-chiap03-cda3da56`
- Workspace: `pi-glm-chiap03-cda3da56`
- Claim verified: YES

## Evidence examined

### Producer evidence (card e08681af)
- Path: `/home/skuser01/.skcapstone/evidence/work/e08681af/20260830T015340Z/PASS_FOR_REVIEW.md`
- SHA256: `a39e4d24098b6d34750471a9b41ce8bf1f48e4c129f4e4ffa5cada4f3d6dce99`
- Status: PRESENT and readable

### Dependency evidence (card b0db90c5)
- Path: `/home/skuser01/.skcapstone/evidence/work/b0db90c5/independent-review/20260830T015540Z/PASS.md`
- Status: PRESENT and readable

### Previous independent review evidence
- Path: `/home/skuser01/.skcapstone/evidence/work/cda3da56/independent-review/20260830T020005Z/FAIL.md`
- Status: PRESENT and readable

## Candidate verification

### Hash identities from producer evidence
- Parent composite: `3a61c01e5a2e4e910d9288a113cdf2a13a98d1fc`
- Candidate child: `2066211a2444694c27fd768aab696c265057ac29`
- Tree: `667a9ba4ea392219afea588a5d08e15189aec53d`
- Three-path full-index diff SHA256: `6bea51dd7e2eaf58d91b577dcd061a9406d44544d38530cc3dc776a54a249749`

### File identities from producer evidence
- `src/skdashboard/read_only.py`: size `22053`, SHA256 `ec9dd48e3f3a19155c68661f8e3c650c724ed9a9c989df5c333c4f1a88672da0`
- `src/skdashboard/runtime_authorizer.py`: size `673`, SHA256 `8070da47bf0142ca163b407a56b7a0d30e7db91d19f470be6d39bf1199db0acf`
- `tests/test_read_only_runtime.py`: size `14307`, SHA256 `b9d6ee372374d5f64b76947ffb8f43db0c57cc42238b6c7007a7d11b6dc06d12`

### Candidate source availability

**CRITICAL FINDING:** The candidate commits, tree, and source files referenced in the evidence are NOT present in any available Git repository:

1. Searched `~/.skcapstone/fleet/workspaces/pi-codex-chiap03-1d5a9a62/skdashboard-1d5a9a62/` - candidate not found
2. Searched `~/.skcapstone/fleet/workspaces/pi-codex-chiap03-888b0c76/skdashboard/` - candidate not found
3. Cloned fresh repository from `https://github.com/smilinTux/skdashboard.git` - candidate not found in any branch or commit history
4. No source patches or diffs available in evidence directory for card e08681af or cda3da56

The only evidence available is the markdown documentation describing the work, not the actual candidate source code.

## Previous review finding (20260830T020005Z)

The previous independent review (by `codex-skdashboard-policy-composition-independent-review`) identified a **CRITICAL BLOCKING ISSUE**:

### Blocking finding: owner-policy hash pin is discarded

The candidate verifies `--owner-policy-file` against `--owner-policy-sha256` at `read_only.py` lines 454-458, parses those verified bytes, and checks the requested node, resource ID, revision, and validity. However, it then discards the verified bytes and hash. At lines 507-512 it calls `compose_file_backed_live_control_plane` with only the mutable file path and owner UID.

The composed backend at `live_control_plane.py` lines 521-540 constructs `FileAuthorizedCardPolicyBackend(owner_policy_file, ...)`. That exact SKCoord backend accepts no expected SHA256. It reloads whatever safe, well-formed bytes are currently present for every policy decision.

### Independent drift oracle results

The previous reviewer demonstrated the failure with an oracle:

```
expected_sha256: 6af1313c274329fa69bcb3213d236e89319b27c6f66f9cef64ea160959d20c0a
current_sha256: 5574a28d05121449e91090e3c3d96ce65cbf3ce3c99a826aa6a7ffdab539eefe
sha_drift: True
backend_accepted_after_drift: True
verified_original_bytes: True
```

This is a **deterministic candidate failure**, not a harness failure.

## Acceptance criteria status

### AC1: Exact parent, candidate, tree, three-path manifest, full-index diff, file hashes, dependency pins, and producer evidence reproduce.

**INDETERMINATE** - Candidate source code is not available in any repository or evidence location. Only documentation is available. Cannot verify:
- Commit `2066211a2444694c27fd768aab696c265057ac29` existence
- Tree `667a9ba4ea392219afea588a5d08e15189aec53d` contents
- File hashes against actual content
- Full-index diff SHA256 `6bea51dd...` against actual diff

### AC2: Wrong factory, hash drift, unsafe mode or owner, symlink or multi-link, malformed policy, stale policy, owner-revision mismatch, and direct browser bearer fail closed before owner reads.

**BLOCKED** - Previous review confirmed that owner-policy hash drift does NOT fail closed. The backend accepts changed policy documents after startup verification. This directly violates the acceptance criterion.

### AC3: Exact same-origin session composition, current owner policy, operator revisions, signer boundary, Now, Portfolio, Schedule, and safe routes remain compatible.

**INDETERMINATE** - Cannot verify without candidate source code.

### AC4: Focused and relevant tests, Ruff, format, diff, secret, Unicode dash, package build, and clean-checkout checks pass without source or runtime mutation.

**INDETERMINATE** - Cannot verify without candidate source code to run tests.

## Dependency status

### Prerequisite card b0db90c5
- Evidence: PASS
- Status: COMPLETE and VERIFIED

### Producer card e08681af
- Evidence: PASS_FOR_REVIEW
- Status: COMPLETE and VERIFIED

## Blocked verdict

According to the BLOCKED VERDICT CONTRACT, this card is **BLOCKED** with the following reason:

**blocked_on:** `card`

**referent:** `ac:2`

**Explanation:**
1. **Criterion 2 violation:** The candidate fails acceptance criterion 2 ("owner-policy hash and file-identity drift fail closed"). The previous independent review demonstrated with an oracle that the backend accepts policy documents whose SHA256 no longer equals the deployment-pinned value.
2. **Criterion 1 unsatisfiable:** The candidate source code is not available for independent verification. The commits `2066211a2444694c27fd768aab696c265057ac29`, tree `667a9ba4ea392219afea588a5d08e15189aec53d`, and parent composite `3a61c01e5a2e4e910d9288a113cdf2a13a98d1fc` do not exist in any available repository or evidence archive. Without the candidate bytes, acceptance criterion 1 ("Exact parent, candidate, tree, three-path manifest, full-index diff, file hashes, dependency pins, and producer evidence reproduce") cannot be satisfied.

This is not a capability limitation - the source files simply do not exist in any durable, shared location. The previous reviewer's evidence (`~/.skcapstone/evidence/work/cda3da56/independent-review/20260830T020005Z/FAIL.md`) states they reviewed "a fresh Git-object archive at `/tmp/skdashboard-cda3da56.Vu5atM`" but that archive no longer exists.

**What was attempted:**
1. Verified ownership of card cda3da56 (confirmed)
2. Located and read producer evidence for card e08681af (found and verified)
3. Located and read dependency evidence for card b0db90c5 (found and verified)
4. Read previous independent review evidence for card cda3da56 (found and verified)
5. Searched all available SKDashboard workspaces for candidate commits (not found)
6. Cloned fresh repository from origin and searched for candidate commits (not found)
7. Searched evidence directories for source patches, diffs, or archives (not found)

The candidate bytes are not available for review, and the previous review identified a security-critical failure that violates AC2.

## Recommendation

1. **For the security issue:** The candidate needs to be corrected to bind the verified owner-policy SHA256 to the live backend, or to compose from the already-verified immutable document. A regression test must be added that changes the owner-policy bytes after startup verification but before live reads and requires denial.

2. **For the evidence issue:** All future review cards must publish the candidate source bytes to a durable, shared location under `~/.skcapstone/evidence/work/<card_id>/` as specified in the SKCapstone guidelines. Documentation alone is not sufficient evidence.

## No mutations performed

Per card constraints, no source edit, push, release, credential access, signing, runtime mutation, deployment, protected data access, provider traffic, or external action was performed during this review.
