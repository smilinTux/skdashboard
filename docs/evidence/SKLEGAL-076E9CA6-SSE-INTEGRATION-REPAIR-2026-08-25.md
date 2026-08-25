# SKLegal 076e9ca6 SSE integration repair

Card: `076e9ca6`

Date: `2026-08-25`

Route: Codex Sol session, board agent `codex-sklegal-integration-repair`

Verdict: `PASS_CANDIDATE_READY_FOR_INDEPENDENT_REREVIEW`

Machine-readable manifest:
`docs/evidence/SKLEGAL-076E9CA6-SSE-INTEGRATION-REPAIR-2026-08-25.json`
with SHA-256
`8b01b59d102921fa1fd1a241f57fa7af4080fce59099e0eda78001678ccafa75`.

## Outcome

One linear SKDashboard candidate now repairs the exact `fda6bd7f` blockers.
It starts at the required local `origin/main`, records separate durable
typed-Tenant and lifetime-currentness deltas, resolves both origin-main patch
conflicts, and pins the exact tested CapAuth Git revision.

No merge, push, deployment, restart, credential access, protected-data access,
provider traffic, external action, or cleanup occurred. The candidate requires
a distinct Codex-only independent integration rereview before any later gate.

## Exact candidate

- Repository: `/home/skuser01/work/skdashboard`
- Worktree: `/home/skuser01/worktrees/skdashboard-076e9ca6-integration-repair`
- Branch: `codex/076e9ca6-integration-repair`
- Base commit: `843ac88261f7268dfb67f11c4c628967565b66bc`
- Base tree: `49865da344befe4c9dbff0d33dc17f8b4ea1adce`
- Typed-Tenant commit: `34fed6aa26622dbcfe458859bde2f2c47b80eec9`
- Typed-Tenant tree: `300eb8f373b750961caaacb74e0af7dcd49d9d67`
- Lifetime commit: `f10b96a9fd97f4294e15d612868456657adfd975`
- Lifetime tree: `bea5eed9192ef6239e8d6f866e9a814130f171e6`
- Candidate commit: `27c8f9ed4b10d8665daac724dcfed9972847f882`
- Candidate tree: `24df619bc79abf0fd7afc8644597af4e23e3c822`
- Base to candidate count: `0` base-only and `3` candidate-only commits
- Candidate patch SHA-256:
  `4616bb07c4a3fc53ab85084b5cef207267442a3141f6ebc15e69b7f37b769b4f`

The required base is a direct ancestor of the candidate. No network fetch was
performed, so freshness beyond the pinned local ref is not asserted.

## Disjoint durable ownership

The typed-Tenant delta is the exact parent to child diff from `843ac882` to
`34fed6a`, SHA-256
`808b3811dd181f2432775e8c6a58f3fe6332dc83cb008c3704cea1e40402824a`.
It owns the Tenant and caller boundary, bounded policy lanes, cursor and replay
partitioning, public publisher attribution, fail-closed read-API behavior, and
typed adversarial coverage.

The lifetime delta is the exact parent to child diff from `34fed6a` to
`f10b96a`, SHA-256
`664daea192b3c27d45683e376333cb2a25a0135b0b200e0e8b5f98c7800da690`.
It owns signed authority custody, one-second idle currentness checks, checks
before events and heartbeats, terminal reset behavior, no-late-payload
behavior, and authority and subscriber cleanup.

The CapAuth pin delta is the exact parent to child diff from `f10b96a` to
`27c8f9e`, SHA-256
`2683e3ba69368fbd856415b91f80e72edb3659cd06c9b9cbc033a7cd5a3caa64`.

Shared implementation paths have one integration owner,
`codex-sklegal-integration-repair`, and nonduplicated hunk ownership:

- `src/skdashboard/control_plane_api.py`: typed boundary in `34fed6a`;
  authority lifetime and cleanup in `f10b96a`.
- `src/skdashboard/dashboard_kanban.py`: bounded isolated lanes in `34fed6a`;
  lifetime checks and terminal handling in `f10b96a`.
- `tests/test_control_plane_decision_context.py`: typed resource controls in
  `34fed6a`; clock, TTL, revocation, and fresh bearer controls in `f10b96a`.
- `tests/test_control_plane_sse.py`: typed lane and adversarial coverage in
  `34fed6a`; lifetime, outage, no-late-payload, and cleanup coverage in
  `f10b96a`.
- `CHANGELOG.md` and `tests/test_control_plane_read_api.py`: origin-main
  conflict resolution and typed-Tenant ownership in `34fed6a` only.
- `src/skdashboard/dashboard.py`: typed public-publisher attribution in
  `34fed6a` only.
- `pyproject.toml`: immutable CapAuth pin in `27c8f9e` only.

The complete path and hunk ownership manifest is in the JSON packet.

## Conflict evidence

Direct application of `/tmp/12f022f2-complete.patch` to the pinned base failed
with exit `1` at `CHANGELOG.md:19` and
`tests/test_control_plane_read_api.py:184`.

Three-way application cleanly integrated five source and test paths and left
only `CHANGELOG.md` conflicted. The exact Changelog blobs were:

- Common base: `642bd6cb4b2152acba06cdb05f4efe6f0c453519`
- Origin-main side: `06c46b89c73325dfd31b7e4773c61692aab31f3f`
- Predecessor target: `a2c821311bf7ce1c82644f47dde64e8a3b19d679`
- Resolved candidate: `dfb1d400d116850fb6ef3c5407a05791148b5dd1`

The resolution preserves all four newer origin-main OIDC entries and adds the
SSE Tenant and caller boundary entry.

The read-API three-way result preserves origin-main OIDC fingerprint tests and
integrates typed SSE fail-closed expectations:

- Origin-main blob: `14d59e87daf074b3de61cf727548c62b1a5b5bba`
- Predecessor target: `90ce5b369e50b727252e2c37254113ac4b19387a`
- Resolved candidate: `850c8cfa7cc82dc95b2ee119d33c4f0cbcb66e08`

Conflict-marker scan: `PASS`.

## Immutable CapAuth pin

The floating `capauth>=0.3.8` dependency was replaced with:

```text
capauth @ git+https://github.com/smilinTux/capauth.git@56b161415748f4c3e2bea0e7fad98c6d104376de
```

- PEP 508 requirement parse: `PASS`
- CapAuth commit: `56b161415748f4c3e2bea0e7fad98c6d104376de`
- CapAuth tree: `a1be093b96fc2d41234aa19e5affe3cf062a82a5`
- `control_plane_authorizer.py` SHA-256:
  `f52cd1c729ad2537e31498dd43d0572b76eb6aeeae4716eeec26fbcc066d42f2`
- `delegated.py` SHA-256:
  `8e0bf921937d2a4aee6f66f58d02c2f8fc13e840a68247692680693169443618`
- `__init__.py` SHA-256:
  `f3b0c06c0771f76fbcf9f83a1d6a5fbf7e3a69053503d193c78e67bce40c6edb`
- CapAuth `pyproject.toml` SHA-256:
  `f3876e06f392d4f98fc2d3796aba10dff15fcbf045dfab458384fa2de92ebefd`

Tests imported only the tracked `src` tree from the exact local CapAuth
revision. No dependency download or provider request occurred.

## Exact commands and results

Typed intermediate focused and relevant check:

```bash
PYTHONPATH=/home/skuser01/worktrees/capauth-1b553209-artifact/src:/home/skuser01/worktrees/skdashboard-076e9ca6-integration-repair/src pytest -q tests/test_control_plane_sse.py tests/test_control_plane_decision_context.py tests/test_control_plane_read_api.py
```

Result: `42 passed in 1.07s` before the lifetime delta.

Final focused SSE check:

```bash
PYTHONPATH=/home/skuser01/worktrees/capauth-1b553209-artifact/src:/home/skuser01/worktrees/skdashboard-076e9ca6-integration-repair/src pytest -q tests/test_control_plane_sse.py
```

Result: `16 passed in 0.24s`.

Final relevant SSE, decision-context, and read-API check:

```bash
PYTHONPATH=/home/skuser01/worktrees/capauth-1b553209-artifact/src:/home/skuser01/worktrees/skdashboard-076e9ca6-integration-repair/src pytest -q tests/test_control_plane_sse.py tests/test_control_plane_decision_context.py tests/test_control_plane_read_api.py
```

Result: `46 passed in 1.16s`.

Targeted Ruff:

```bash
ruff check src/skdashboard/control_plane_api.py src/skdashboard/dashboard.py src/skdashboard/dashboard_kanban.py tests/test_control_plane_decision_context.py tests/test_control_plane_read_api.py tests/test_control_plane_sse.py
```

Result: `All checks passed!`.

Candidate diff check:

```bash
git diff --check 843ac88261f7268dfb67f11c4c628967565b66bc..27c8f9ed4b10d8665daac724dcfed9972847f882
```

Result: `PASS`, empty output.

Independent alternate-index forward and reverse checks passed with exit `0`
for the typed-Tenant delta, lifetime delta, CapAuth pin delta, and complete
base-to-candidate delta. The exact retained index paths are recorded in the
JSON packet. No reverse patch was executed against the candidate.

## Candidate file hashes

- `CHANGELOG.md`:
  `91a45f6c4285564cf1d64b0aeebe529634f776499c761c8b72cb714b030a88ac`
- `pyproject.toml`:
  `08d09ef45e7140ed2becd02140f2a0428b209051c947340264870e332de85cdd`
- `src/skdashboard/control_plane_api.py`:
  `db4352c52dd200f4a302e740f3f4cff573b3f487c13994aff9e082d7bc5760b1`
- `src/skdashboard/dashboard.py`:
  `5984b5120a7cc12ec3dc08a5c444a7f6938dd2306cfdac6dca1efdbb99af887a`
- `src/skdashboard/dashboard_kanban.py`:
  `8ee0cfef4efd68174c4a6d50dc4899f8ce38ef363fa5b66e00c01a9416d14f45`
- `tests/test_control_plane_decision_context.py`:
  `f9fc5d92eb6d0358306fd0848990012b3c3c7885fabb473356ef02682c2c6c13`
- `tests/test_control_plane_read_api.py`:
  `900ae5b8c6c330d7754eab48ff8d42709f77079acc31ad724a985858e2167f9c`
- `tests/test_control_plane_sse.py`:
  `e6c56ac73825e6a0f34f03c469accea0f2eade35f79ef6810ea22aa712dc8bda`

Git blob hashes and predecessor artifact hashes are recorded in the JSON
packet.

## Preserved history

- `7aefb544` FAIL, `7ab3f69e` repair, and `5ec78e00` PASS remain unchanged.
- `e71a3248` FAIL, `1b4ca98b` repair, and `a4457548` PASS remain unchanged.
- `2ca5632a` and `fda6bd7f` BLOCKED integration history remains unchanged.

This candidate supersedes none of those records and grants no integration or
deployment authority.

## Limitations

- Local refs were not network refreshed.
- Qualification was public-synthetic and in-process only.
- No socket, TLS, multiprocess, multihost, soak, capacity, production timing,
  deployment, or service test ran.
- Protected replay remains process-local and ephemeral.
- Real dashboard publishers remain public-only.
- Private CapAuth currentness internals remain an intentional dependency, now
  pinned to the exact tested revision. Any revision change requires
  requalification.
- The CapAuth source worktree has a pre-existing untracked `rollback/`
  directory. Tests imported only its tracked `src` tree.
- Temporary independent-index evidence remains under `/tmp` because cleanup
  was prohibited.

## Rollback

Forward and reverse application checks passed for every delta and for the
complete candidate. No rollback was executed. If separately authorized, the
complete mechanical rollback is the reverse of candidate patch SHA-256
`4616bb07c4a3fc53ab85084b5cef207267442a3141f6ebc15e69b7f37b769b4f`
from candidate `27c8f9e` to base `843ac882`.

No schema, data, service, runtime, credential, protected-data, provider, or
external state changed, so no live rollback is required.
