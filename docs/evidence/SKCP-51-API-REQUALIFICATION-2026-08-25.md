# SKCP-51 current-origin-main API requalification - 2026-08-25

**Card:** `2d02b6ed`  
**Verdict:** **FAIL - do not complete**

The bounded public-synthetic qualifier found three decisive current-candidate product gaps. This qualification does not implement repairs and does not reinterpret a failure as completion.

## Candidate and environment

- Candidate: `HEAD=origin/main=436b094b86f422c7eaf94ebbbd98b846e7f3a6af`.
- Host: `chiap02`; Linux `7.0.0-28-generic`, x86_64; Python `3.12.3`.
- CPU: 12th Gen Intel Core i9-12900K, 24 logical CPUs.
- Public-synthetic fixture: `tests/fixtures/control_plane_full_estate.v1.0.0.json`, SHA-256 `926f0374b8f32e64d0f370f9a6197b848a95a3403b8d497c5c67848e77fb821d`.
- Transport: Starlette TestClient, same-process ASGI. No protected data or credentials.
- Command: `PYTHONPATH=src python scripts/qualify_control_plane_performance.py`.
- Qualifier SHA-256: `e3f812768f80ac39dfb5bea9b62eb0b5d0f9b3634fa74ee875916a0dc68c1a84`.
- The initial measurement invocation exited 0 before failure signaling was corrected. The final identical bounded method exits 1 when its verdict is FAIL.

## Exact load and observed metrics

The pagination lane used 10,000 public-synthetic owner records, `limit=1`, 10 warmups, 100 measured requests, and concurrency 1.

| Measure | Observation |
|---|---:|
| p50 | 1.895 ms |
| p95 | 2.176 ms |
| p99 | 8.940 ms |
| max | 9.354 ms |
| transport errors | 0 / 100 |
| transport error rate | 0.00% |
| pagination-lane CPU | 0.214 s |
| pagination-lane peak RSS | 53,260 KiB |
| whole qualifier wall / CPU | 0.587 s / 0.595 s |
| whole qualifier max RSS | 60,172 KiB |

These low in-process response times do **not** make the qualification pass because semantic work bounds and isolation fail.

## Decisive failures

1. **Pagination is not owner-bounded.** Every `limit=1` request obtained all 10,000 owner records before slicing. The 110 warmup-plus-measured calls returned 1,100,000 owner records in aggregate: 10,000 owner records per one-item request.
2. **ETag is not a server projection cache hit.** Baseline `200` followed by matching `If-None-Match` returned `304`, but the owner reader ran twice and returned 20,000 records. The conditional request therefore recomputed the full owner projection.
3. **Critical health is not rate-isolated.** After 120 successful common board reads, `/api/v1/health` returned `429 RATE_LIMITED`, not `200`.

These failures violate the cursor/limit/cache/rate contract and the requirement that health and other critical reads remain available. Because acceptance criteria already fail, the run stopped at the card boundary. SSE resume/backpressure, insight concurrency, report generation, and the remaining criteria are **not requalified** by this evidence.

## Limitations

This is a single-host, same-process, sequential public-synthetic run. It is not socket/TLS, network, multiprocess, multihost, deployed-service, production-owner, protected-data, or sustained-soak capacity evidence. Resource values belong to the qualifier process, not a deployed service.

## Rollback

Delete only:

- `scripts/qualify_control_plane_performance.py`
- `docs/evidence/SKCP-51-API-REQUALIFICATION-2026-08-25.md`
- `docs/evidence/SKCP-51-API-REQUALIFICATION-RAW-2026-08-25.json`

No product, schema, dependency, service, deployment, protected data, credential, commit, or push mutation occurred.
