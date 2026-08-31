# Dashboard Assistant Configuration

## Overview

The dashboard assistant uses a **provider-neutral logical route** through SKGateway. The application code does not hardcode any private model host. Instead, deployment configuration binds the logical route to the approved chiap08 Qwen3.8 backend.

## Card Reference

- **Card ID**: 5c38b715
- **Title**: [SKDASH-AI-ASSISTANT-01][M] Implement provider-neutral read-only assistant through SKGateway
- **Priority**: Critical

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SKGATEWAY_URL` | `http://localhost:18780/v1` | SKGateway endpoint |
| `SKDASHBOARD_ASSISTANT_ROUTE` | `sk-dashboard-assistant` | Logical route for dashboard assistant |

### SKGateway Route Configuration

Add the following to your SKGateway configuration (e.g., `~/.skcapstone/gateway/skgateway.yaml` or `config/skgateway.yaml`):

```yaml
# Logical route for dashboard assistant (read-only)
# Maps to chiap08 Qwen3.8 backend via registry
backends:
  chiap08-qwen38:
    url: http://TAILNET_HOST:11439/v1
    auth_type: none
    models:
      - qwen3.8-27b-huihui-abliterated-q4_k_m
      - qwen3.8-27b
    priority: 3

# Registry configuration (separate file or inline)
# See ~/.skcapstone/models/registry.yaml
```

And in `~/.skcapstone/models/registry.yaml`:

```yaml
backends:
  qwen38:
    url: http://100.81.238.58:11439/v1  # chiap08
    model: qwen3.8-27b-huihui-abliterated-q4_k_m
    kind: chat

roles:
  sk-dashboard-assistant: qwen38  # Logical route -> chiap08 Qwen3.8

defaults:
  role: sk-dashboard-assistant
```

## Security Model

### What is EXCLUDED from requests:

- **No credentials**: Bearer tokens, API keys, passwords are stripped
- **No raw capabilities**: Capability tokens or references are rejected
- **No protected Matter content**: Content outside authorized scope is blocked
- **No write tools**: No exec, read, write, edit, message tools
- **No external-action tools**: No HTTP calls, file operations, etc.

### Fail-Closed Behavior

All error conditions fail closed with attributable audit:

1. **Outage**: Gateway unreachable → logged with actor, card_id, error
2. **Denial**: Gateway returns error → logged with status code, response
3. **Malformed output**: Schema validation fails → logged with validation errors
4. **Route drift**: Unexpected backend → logged with provenance
5. **Timeout**: Request exceeds limit → logged with timing
6. **Egress mismatch**: Unexpected response format → logged with snippet

### Typed Validation

All requests and responses are validated using Pydantic schemas:

- **AssistantRequest**: Bounded, validated input
- **AssistantResponse**: Structured output with provenance
- **AssistantProvenance**: Model/backend metadata for audit

## Audit Trail

Every assistant interaction logs:

```python
{
    "actor": "operator",  # or authenticated identity
    "card_id": "5c38b715",  # if in card context
    "route_used": "sk-dashboard-assistant",
    "model_served": "qwen3.8-27b-huihui-abliterated-q4_k_m",
    "backend_id": "chiap08-qwen38",
    "timestamp": "2026-08-31T07:30:00Z",
    "error": null  # or error details if failed
}
```

## Deployment Steps

1. **Configure SKGateway route**:
   - Add `sk-dashboard-assistant` role to registry
   - Bind to `qwen38` backend (chiap08)

2. **Set environment variables**:
   ```bash
   export SKDASHBOARD_ASSISTANT_ROUTE=sk-dashboard-assistant
   ```

3. **Verify configuration**:
   ```bash
   # Check registry
   cat ~/.skcapstone/models/registry.yaml

   # Check gateway config
   cat ~/.skcapstone/gateway/skgateway.yaml

   # Test route (if gateway is running)
   curl -H "Content-Type: application/json" \
     -d '{"model":"sk-dashboard-assistant","messages":[{"role":"user","content":"test"}],"stream":false}' \
     http://localhost:18780/v1/chat/completions
   ```

4. **Restart dashboard service**:
   ```bash
   systemctl --user restart skcapstone-dashboard
   ```

## Acceptance Criteria

1. ✅ The API sends a typed bounded request to one logical SKGateway route and validates a typed response with source and model provenance.
2. ✅ Deployment configuration, not application code, binds the logical route to the approved chiap08 Qwen3.8 backend.
3. ✅ Requests exclude credentials, raw capabilities, protected Matter content outside authorized scope, and all write or external-action tools.
4. ✅ Outage, denial, malformed output, route drift, timeout, and egress mismatch fail closed with attributable audit.
5. ⏳ Focused gateway, authorization, schema, redaction, browser, static, secret, and independent review checks pass.

## Testing

```bash
# Test the typed client directly
python -c "
from skdashboard.assistant_client import get_client, AssistantRequest
client = get_client()
result = client.chat([
    {'role': 'user', 'content': 'Say hello'}
], actor='test')
print(result)
"

# Test with streaming
python -c "
from skdashboard.assistant_client import get_client
client = get_client()
for token in client.chat_stream([
    {'role': 'user', 'content': 'Count to 5'}
], actor='test'):
    print(token, end='', flush=True)
print()
"
```

## Related Files

- `src/skdashboard/assistant_client.py` - Typed client implementation
- `src/skdashboard/dashboard_assistant.py` - Assistant console integration
- `src/skdashboard/dashboard.py` - API endpoint (`/api/assistant`)
- `src/skdashboard/static/assistant.html` - UI
- `src/skdashboard/static/js/assistant.js` - Client-side JavaScript

## References

- Card 5c38b715: Full acceptance criteria and requirements
- SKGateway documentation: `~/.skcapstone/worktrees/*/src/`
- Model registry: `~/.skcapstone/models/registry.yaml`
- Gateway config: `~/.skcapstone/gateway/skgateway.yaml`
