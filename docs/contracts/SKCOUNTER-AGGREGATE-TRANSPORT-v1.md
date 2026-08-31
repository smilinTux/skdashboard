# SKCounter aggregate transport v1

## Decision

Use one authenticated server-side pull. The SKDashboard service on chiap08
pulls a bounded, versioned aggregate bundle from the SKCounter collector on
chiap04. Browsers never contact the collector.

| Pin | Value |
| --- | --- |
| Authority | `skcounter-collector@chiap04` |
| Authority host | `chiap04.tail204f0c.ts.net` |
| HTTPS endpoint | `https://chiap04.tail204f0c.ts.net:9398/v1/dashboard/aggregates/latest` |
| Target | `skdashboard-read-only@chiap08` |
| Target host | `chiap08.tail204f0c.ts.net` |
| CapAuth audience | `skcounter` |
| Required capability | `skcounter.dashboard.aggregates.read` |
| Response schema | `skcounter.dashboard-aggregate-transport.v1` |
| Source index schema | `skcounter.latest-observation-index.v1` |

The caller presents the capability only to the collector over verified
tailnet HTTPS. The response never contains a capability, credential, cookie,
browser session, prompt, response, raw observation, or source path.

## Bounds

- Connect timeout: 2 seconds.
- Response read timeout: 5 seconds.
- Total attempt budget: 7 seconds, with no retry inside one dashboard request.
- Maximum response body: 1,048,576 bytes.
- Maximum aggregate records: 10,000.
- Currentness TTL: 2,700 seconds from the newest `observed_at` in the bundle.
- The HTTP `ETag` is the quoted `payload_sha256`. `If-None-Match` may return
  `304`, which is an idempotent unchanged read.

`payload_sha256` is SHA-256 over canonical UTF-8 JSON containing exactly
`records` and `provenance`, with object keys sorted, compact separators, and
no trailing newline. `payload_bytes` is the byte count of those same bytes.
`rows` equals the number of records. Each record represents one unique source
index identity keyed by measurement lane, node, principal, view, and bucket.
Its `aggregates` array preserves every bounded aggregate row for that identity.

## Acceptance and replay

The consumer validates TLS identity, CapAuth authorization, HTTP status,
content type, content length, response schema, exact fields, body bounds,
row count, payload byte count, payload hash, index hash, record hashes, and
UTC timestamps before publishing a generation.

Repeated source index identities fail closed. Every aggregate row grouped in
a record must repeat that record's exact view and bucket. This preserves model
and provider breakdown rows without allowing the index identity itself to be
duplicated.

A response fails closed when it is partial, oversized, malformed,
unauthorized, stale, or outside the timeout budget. It also fails closed when
it has a `generated_at` older than the last accepted generation, or the same
`generated_at` with a different hash. The same hash and generation are an
idempotent replay and do not create a new accepted generation.

The dashboard currentness verifier runs before the pull and again before the
accepted generation is exposed. A response is current only when the newest
record is no more than 2,700 seconds old and `fresh_until` has not passed.
Future timestamps, missing records, or mixed index generations fail closed.

## Outage and rollback

An outage never becomes zero usage or a healthy result. The last accepted
bundle may remain available only with its age and `stale` truth state. Once no
accepted bundle exists, or policy forbids stale display, the projection is
`unavailable` with value fields omitted.

Rollback disables this pull adapter and returns SKDashboard to its prior
local aggregate reader. It does not delete collector observations, index
generations, accepted dashboard bundles, or audit evidence. Deployment,
credential provisioning, service restart, and rollback execution require
separate reviewed work.
