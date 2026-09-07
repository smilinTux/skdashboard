import { esc } from "./read_only_api.js";
import { GATEWAY_FILTERS, gatewayQuery, gatewayURL, readGateway, gatewayValue, gatewayFreshness } from "./gateway_client.js";

const byId = (id) => document.getElementById(id);
const cell = (value) => esc(gatewayValue(value));
function table(title, headings, rows) {
  return `<div class="table-wrap" tabindex="0" role="region" aria-label="${esc(title)}"><table><caption>${esc(title)}</caption><thead><tr>${headings.map((heading) => `<th scope="col">${esc(heading)}</th>`).join("")}</tr></thead><tbody>${rows.length ? rows.map((row) => `<tr>${row.map((value) => `<td>${value}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${headings.length}">No observations in this snapshot. Values remain unknown.</td></tr>`}</tbody></table></div>`;
}

export function economyTables(payload) {
  const latest = payload.items.at(-1);
  if (!latest) return "<p>No matching observations. No zero usage is inferred.</p>";
  const facts = latest.facts;
  const measures = [
    ["Requests", facts.requests?.total, "requests"],
    ["Request rate (5 minutes)", facts.requests?.rate_5m_per_second, "requests/second"],
    ["Active concurrency", facts.requests?.active_concurrency, "requests"],
    ["Input tokens", facts.tokens?.input, "tokens"],
    ["Output tokens", facts.tokens?.output, "tokens"],
    ["Cache read tokens", facts.tokens?.cache_read, "tokens"],
    ["Cache write tokens", facts.tokens?.cache_write, "tokens"],
    ["Token throughput (5 minutes)", facts.tokens?.throughput_5m_per_second, "tokens/second"],
    ["Generation speed", facts.generation?.throughput_tokens_per_second, "tokens/second"],
    ["Time to first token", facts.generation?.ttft_ms, "milliseconds"],
    ["Queue wait", facts.queue?.wait_ms_percentiles, "milliseconds"],
    ["Terminal errors", facts.requests?.error_count, "requests"],
    ["HTTP 429", facts.rate_limits?.http_429_count, "requests"],
    ["Cost (known portion)", ["actual", "estimated", "partial"].includes(facts.cost?.truth) ? facts.cost?.total_usd : null, "USD; partial cost excludes unpriced requests"],
    ["Cost state", facts.cost?.truth, "state; missing or unpriced cost is unknown"],
    ["Unpriced requests", facts.cost?.unpriced_requests, "requests"],
  ];
  const metricTable = table("Latest gateway snapshot: original aggregate population", ["Measurement", "Observed value", "Unit"], measures.map(([label, value, unit]) => [esc(label), cell(value), esc(unit)]));
  const dimensions = table("Observed attribution; unavailable dimensions remain unknown", ["Dimension", "Observed members"], ["models", "providers", "nodes", "clients", "apps", "rails", "routes"].map((key) => [esc(key), Array.isArray(facts.breakdowns?.[key]) ? facts.breakdowns[key].map(cell).join(", ") || "Unknown: no members observed" : cell(facts.breakdowns?.[key])]));
  const daily = Array.isArray(facts.daily_token_rows) ? facts.daily_token_rows : [];
  const dailyTable = table("Daily token rows from the latest snapshot, not added to history", ["Bucket", "Model", "Backend", "Client", "Requests", "Input tokens", "Output tokens", "Cache read tokens", "Cache write tokens"], daily.map((row) => [row.bucket, row.model, row.backend, row.agent, row.request_count, row.input_tokens, row.output_tokens, row.cache_read_tokens, row.cache_write_tokens].map(cell)));
  const latency = facts.latency_ms && typeof facts.latency_ms === "object" ? Object.entries(facts.latency_ms).filter(([, value]) => value && typeof value === "object") : [];
  const latencyTable = table("Backend/model latency in milliseconds", ["Backend/model", "p50", "p95", "p99", "Sample count"], latency.map(([key, value]) => [key, value.p50, value.p95, value.p99, value.count].map(cell)));
  const rates = payload.items.map((item) => item.facts.requests?.rate_5m_per_second).filter((rate) => typeof rate === "number" && Number.isFinite(rate) && rate >= 0);
  const maximum = Math.max(1, ...rates);
  const history = table("Observation history: overlapping snapshots, never summed", ["Observed (UTC)", "Request rate (5 minutes)", "Input tokens", "Output tokens", "Cost (USD)", "Cost state", "Snapshot identity"], payload.items.map((item) => {
    const rate = item.facts.requests?.rate_5m_per_second;
    const bar = typeof rate === "number" && Number.isFinite(rate) && rate >= 0 ? `<meter aria-hidden="true" min="0" max="${maximum}" value="${rate}"></meter>` : "";
    return [cell(item.observed_at), `${cell(rate)}${bar}`, cell(item.facts.tokens?.input), cell(item.facts.tokens?.output), cell(["actual", "estimated", "partial"].includes(item.facts.cost?.truth) ? item.facts.cost.total_usd : null), cell(item.facts.cost?.truth), cell(item.watermark)];
  }));
  return metricTable + dimensions + dailyTable + latencyTable + history;
}

function initialize() {
  let query;
  let epoch = 0;
  let controller;
  let snapshot;
  let lastLoaded = 0;
  const form = byId("gateway-filters");
  function clear(message) {
    snapshot = null;
    byId("gateway-state").textContent = message;
    byId("gateway-tables").replaceChildren();
    byId("gateway-provenance").textContent = "Snapshot identity, scope, freshness, and coverage unavailable.";
    byId("gateway-source").hidden = true;
    byId("gateway-source-note").hidden = true;
  }
  function freshness() {
    if (!snapshot) return;
    const current = gatewayFreshness(snapshot);
    const label = `${current.state}${current.stale && current.state !== "stale" ? " (stale)" : ""}${snapshot.unavailable_reason ? `: ${snapshot.unavailable_reason}` : ""}`;
    if (byId("gateway-state").textContent !== label) byId("gateway-state").textContent = label;
    byId("gateway-provenance").textContent = `Gateway observed | Scope: ${snapshot.scope} | Observed: ${snapshot.observed_at || "Unknown"} | Age: ${current.age === null ? "Unknown" : Math.floor(current.age) + "s"} | TTL: ${gatewayValue(snapshot.ttl_seconds)}s | Coverage: ${gatewayValue(snapshot.coverage.returned)} returned, ${gatewayValue(snapshot.coverage.examined)} examined, ${gatewayValue(snapshot.coverage.malformed)} malformed | Snapshot: ${gatewayValue(snapshot.watermark)}`;
  }
  async function load() {
    controller?.abort();
    const current = ++epoch;
    const requestController = new AbortController();
    controller = requestController;
    clear("Loading protected gateway observations.");
    byId("gateway-evidence").setAttribute("aria-busy", "true");
    const timeout = setTimeout(() => requestController.abort(), 8000);
    try {
      const response = await readGateway(query, requestController.signal);
      if (current !== epoch) return;
      snapshot = response;
      byId("gateway-tables").innerHTML = economyTables(response);
      byId("gateway-source").href = gatewayURL(query);
      byId("gateway-source").hidden = false;
      byId("gateway-source-note").hidden = false;
      freshness();
    } catch (error) {
      if (current === epoch) clear(error.name === "AbortError" ? "Unavailable: gateway request timed out" : error.message);
    } finally {
      clearTimeout(timeout);
      if (current === epoch) {
        lastLoaded = Date.now();
        byId("gateway-evidence").setAttribute("aria-busy", "false");
      }
    }
  }
  function readLocation() {
    try {
      query = gatewayQuery(location.search);
      for (const key of GATEWAY_FILTERS) {
        const value = query.get(key) || "";
        form.elements[key].value = value && (key === "start" || key === "end") ? new Date(value).toISOString().slice(0, 19) : value;
      }
      load();
    } catch (error) { ++epoch; controller?.abort(); query = null; clear(error.message); byId("gateway-evidence").setAttribute("aria-busy", "false"); }
  }
  function apply(reset = false) {
    if (!query) return;
    const next = new URLSearchParams(query);
    for (const key of GATEWAY_FILTERS) {
      const value = reset ? "" : form.elements[key].value;
      if (!value) next.delete(key);
      else next.set(key, key === "start" || key === "end" ? new Date(value + "Z").toISOString() : value);
    }
    history.pushState({}, "", `${location.pathname}?${next}`);
    readLocation();
  }
  form.addEventListener("submit", (event) => { event.preventDefault(); apply(); });
  byId("gateway-reset").addEventListener("click", () => apply(true));
  byId("gateway-refresh").addEventListener("click", () => { if (query) load(); });
  window.addEventListener("popstate", readLocation);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && query) load(); });
  setInterval(() => {
    freshness();
    if (!document.hidden && query && byId("gateway-live").checked && Date.now() - lastLoaded >= 15000 && byId("gateway-evidence").getAttribute("aria-busy") !== "true") load();
  }, 1000);
  readLocation();
}
if (typeof document !== "undefined" && document.getElementById("gateway-filters")) initialize();
