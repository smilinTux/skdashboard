# Read-only Dashboard Assistant Configuration

## Purpose

Card `b677a99f` composes the existing governed AI outcome workspace with the approved provider-neutral assistant path from card `5c38b715`. The composition is observational only.

## AI outcome boundary

`/control-plane/ai` reads the protected aggregate control-plane overview. Each source row shows:

- owner and population
- adapter version
- truth state
- exact `observed_at` timestamp and `age_seconds`
- collector coverage
- source watermark
- metric registry version and hash

Harness-reported, gateway-observed, and SKJoule lanes remain separate. The view does not expose prompts, responses, tool input, tool output, credentials, capabilities, workspace paths, or session identifiers. Missing outcome evidence remains Unknown and is not inferred from usage.

## Assistant route

SKDashboard sends one logical route through `skcapstone.skgateway_client`. Application code does not contain a provider hostname, provider SDK, backend identity, or credential.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SKDASHBOARD_ASSISTANT_ROUTE` | `sk-dashboard-assistant` | Bounded logical SKGateway route |

SKGateway deployment configuration owns the backend binding. This repository change does not deploy, restart, or mutate that configuration.

## Request contract

The facade accepts at most 20 messages and 24,000 characters. Every message has exactly `role` and `content`. It rejects:

- tool or function fields
- action or mutation schemas
- capability material
- credential-shaped text
- protected payload field names
- malformed logical routes
- caller-selected route overrides

The assistant receives only a bounded board and ITIL aggregate snapshot. Its system contract forbids commands, actions, tool calls, mutations, credentials, capabilities, and provider endpoints.

## Response and failure behavior

The browser renders token and done events only. There is no model-authored action protocol, action event, command parser, mutation dispatcher, or queue caller.

Gateway failures fail closed to an unavailable message. Failure logging contains only bounded actor attribution, logical route, and error class. Request text, response text, credentials, capabilities, provider endpoints, and protected payloads are not logged.

## Verification

```bash
pytest -q tests/test_assistant_client.py tests/test_control_plane_ai_workspace.py
pytest -q tests/test_queue_gate_enforcement.py
ruff check src/skdashboard/assistant_client.py src/skdashboard/dashboard_assistant.py
```

The tests prove that requests cross only the shared SKGateway abstraction, aggregate freshness is rendered, forbidden fields fail before handoff, and the assistant module has no action or mutation surface.
