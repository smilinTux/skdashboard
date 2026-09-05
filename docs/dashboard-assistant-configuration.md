# Dashboard Assistant Contract

The dashboard assistant is a provider-neutral, read-only client of the
logical `sk-dashboard-assistant` SKGateway route. Application code does not
select a model, backend, private host, credential, or deployment target.

## Request boundary

Before retrieval or model egress, the caller must provide a typed policy
decision containing `tenant_id`, optional `matter_id`, classification,
source-rights, an approved egress profile, and `read_authorized=true`.
Requests are bounded, contain no tools or capabilities, and always serialize
with `stream: true`.

The assistant never parses model text as a command. It cannot add notes, move
or assign cards, queue agent runs, send communications, or perform any other
external action.

## Response boundary

Every response must carry typed headers for `X-SK-Route-Used`,
`X-SK-Model-Served`, `X-SK-Backend-Id`, `X-SK-Egress-Profile`, and
`X-SK-Retrieval-Traces`. Retrieval traces contain source identifiers, hashes,
and optional source spans. The client rejects missing or malformed provenance,
route drift, backend drift when an approved backend is configured, and egress
profile mismatch.

Streaming is buffered until every SSE record, terminal finish state, `[DONE]`
marker, provenance field, and size bound has passed validation. No partial
content is released on failure.

## Safety and audit

Credential and capability patterns, opaque encoded material, oversized
content, malformed requests, malformed responses, outages, and policy errors
fail closed. Audit records contain actor, card context, reason, route, and
bounded type-safe metadata only. Gateway bodies and protected content are not
copied into logs or error responses.

The logical route and its backend binding are deployment-owned configuration.
Keep that configuration in the governed SKGateway registry and policy
workflow. Do not place private addresses, credentials, registry copies, or
restart and deployment commands in this repository.

## Verification

Focused verification should cover the assistant client, gateway contract,
authorization and scope gate, schema and stream validation, redaction and
audit safety, static checks, Ruff, Python compilation, and secret scanning.
Integration tests must use mocks or simulation mode. No provider traffic is
required for source review.
