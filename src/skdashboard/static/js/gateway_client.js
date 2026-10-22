// Shared protected evidence contract. Values are never summed across snapshots.
export const GATEWAY_FILTERS = Object.freeze(["start", "end", "model", "provider", "node", "client", "app", "rail"]);
const QUERY_KEYS = new Set([...GATEWAY_FILTERS, "role", "scope", "limit"]);
const STATES = new Set(["current", "stale", "partial", "empty", "unavailable"]);

export function gatewayQuery(search) {
  const query = new URLSearchParams(search);
  for (const key of query.keys()) {
    if (!QUERY_KEYS.has(key) || query.getAll(key).length !== 1) throw new Error("Unsupported or repeated gateway filter");
  }
  return query;
}

export function gatewayURL(query) {
  return `/api/v1/gateway/timeseries?${query}`;
}

export async function readGateway(query, signal) {
  const response = await fetch(gatewayURL(query), { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" }, signal });
  if (!response.ok) {
    const error = new Error(({ 400: "Invalid query", 401: "Sign in required", 403: "Denied", 429: "Rate limited", 503: "Unavailable" })[response.status] || "Unavailable");
    error.status = response.status;
    throw error;
  }
  const payload = await response.json();
  if (payload.schema_version !== "skdashboard.gateway.v1" || !STATES.has(payload.state)
      || !Array.isArray(payload.items) || payload.items.length > 200
      || typeof payload.scope !== "string" || !payload.coverage
      || (query.has("scope") && query.get("scope") !== payload.scope)) throw new Error("Malformed or mismatched gateway evidence");
  const expected = Object.fromEntries([...query].filter(([key]) => GATEWAY_FILTERS.includes(key) && key !== "start" && key !== "end"));
  if (JSON.stringify(Object.entries(expected).sort()) !== JSON.stringify(Object.entries(payload.filters || {}).sort())) throw new Error("Mismatched gateway filters");
  if (payload.items.some((item) => !item || typeof item.observed_at !== "string" || !Number.isFinite(Date.parse(item.observed_at)) || !item.facts || typeof item.facts !== "object" || Array.isArray(item.facts))) throw new Error("Malformed gateway observation");
  const latest = payload.items.at(-1);
  if (latest && (latest.observed_at !== payload.observed_at || latest.watermark !== payload.watermark)) throw new Error("Mismatched gateway snapshot identity");
  return payload;
}

export function gatewayValue(value) {
  if (value == null) return "Unknown: not observed";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "Unknown: invalid number";
  if (typeof value === "string") return value || "Unknown: not observed";
  if (typeof value === "object" && !Array.isArray(value)) {
    if (typeof value.unavailable === "string") return `Unknown: ${value.unavailable}`;
    if (value.state === "unknown" || value.state === "unavailable") return `Unknown: ${value.reason || "not observed"}`;
  }
  return "Unknown: unsupported value";
}

export function gatewayFreshness(payload, now = Date.now()) {
  const timestamp = Date.parse(payload.observed_at);
  const elapsed = Number.isFinite(timestamp) ? Math.max(0, (now - timestamp) / 1000) : null;
  const age = elapsed === null ? null : Math.max(elapsed, Number.isFinite(payload.age_seconds) ? payload.age_seconds : 0);
  const stale = age !== null && Number.isFinite(payload.ttl_seconds) && age > payload.ttl_seconds;
  return { state: payload.state === "current" && stale ? "stale" : payload.state, age, stale };
}
