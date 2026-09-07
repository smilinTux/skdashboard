// Shared protected evidence contract. Values are never summed across snapshots.
export const GATEWAY_FILTERS = Object.freeze(["start", "end", "model", "backend", "provider", "node", "client", "app", "rail"]);
const QUERY_KEYS = new Set([...GATEWAY_FILTERS, "scope", "limit"]);
const STATES = new Set(["current", "stale", "partial", "empty", "unavailable"]);
export const GATEWAY_VIEW_STATES = Object.freeze(["loading", "current", "stale", "partial", "empty", "denied", "unavailable"]);

export function gatewayQuery(search) {
  const query = new URLSearchParams(search);
  for (const key of query.keys()) {
    if (!QUERY_KEYS.has(key) || query.getAll(key).length !== 1) throw new Error("Unsupported or repeated gateway filter");
  }
  return query;
}

export function gatewayFilters(search) {
  const source = new URLSearchParams(search);
  const query = new URLSearchParams();
  for (const key of QUERY_KEYS) {
    const values = source.getAll(key);
    if (values.length > 1) throw new Error("Unsupported or repeated gateway filter");
    if (values[0]) query.set(key, values[0]);
  }
  return query;
}

export function gatewayURL(query, endpoint = "timeseries") {
  if (endpoint !== "timeseries" && endpoint !== "summary") throw new Error("Unsupported gateway endpoint");
  const suffix = query.toString();
  return `/api/v1/gateway/${endpoint}${suffix ? `?${suffix}` : ""}`;
}

export function gatewayEvidence(payload) {
  return Object.freeze({ observed_at: payload.observed_at || null, watermark: payload.watermark || null, scope: payload.scope, filters: Object.freeze({ ...(payload.filters || {}) }) });
}

export async function readGateway(query, signal, endpoint = "timeseries") {
  const response = await fetch(gatewayURL(query, endpoint), { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" }, signal });
  if (!response.ok) {
    const error = new Error(({ 400: "Invalid query", 401: "Sign in required", 403: "Denied", 429: "Rate limited", 503: "Unavailable" })[response.status] || "Unavailable");
    error.status = response.status;
    throw error;
  }
  const payload = await response.json();
  const rows = endpoint === "timeseries" ? payload.items : payload.summary;
  if (payload.schema_version !== "skdashboard.gateway.v1" || !STATES.has(payload.state)
      || (endpoint === "timeseries" && (!Array.isArray(rows) || rows.length > 200))
      || (endpoint === "summary" && rows !== null && (typeof rows !== "object" || Array.isArray(rows)))
      || typeof payload.scope !== "string" || !payload.coverage
      || (query.has("scope") && query.get("scope") !== payload.scope)) throw new Error("Malformed or mismatched gateway evidence");
  const expected = Object.fromEntries([...query].filter(([key]) => GATEWAY_FILTERS.includes(key) && key !== "start" && key !== "end"));
  if (JSON.stringify(Object.entries(expected).sort()) !== JSON.stringify(Object.entries(payload.filters || {}).sort())) throw new Error("Mismatched gateway filters");
  if (endpoint === "timeseries") {
    if (rows.some((item) => !item || typeof item.observed_at !== "string" || !Number.isFinite(Date.parse(item.observed_at)) || !item.facts || typeof item.facts !== "object" || Array.isArray(item.facts))) throw new Error("Malformed gateway observation");
    const latest = rows.at(-1);
    if (latest && (latest.observed_at !== payload.observed_at || latest.watermark !== payload.watermark)) throw new Error("Mismatched gateway snapshot identity");
  }
  payload.evidence = gatewayEvidence(payload);
  return payload;
}

export function gatewayViewState(value) {
  if (value === "loading") return "loading";
  if (value && (value.status === 401 || value.status === 403)) return "denied";
  if (value && STATES.has(value.state)) return value.state;
  return "unavailable";
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
