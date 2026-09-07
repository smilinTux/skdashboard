const $ = (id) => document.getElementById(id);
const fmt = (value) => value === null || value === undefined ? "No data" : Number(value).toLocaleString(undefined, {maximumFractionDigits: 2});
const esc = (value) => String(value ?? "").replace(/[&<>\"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;", "'":"&#39;"}[ch]));
function kv(label, value) { return `<div class="kv"><span>${esc(label)}</span><strong>${esc(fmt(value))}</strong></div>`; }
function sourceState(source, id) { const state = source?.truth_state || "unavailable"; const node = $(id); node.textContent = state; node.className = `status ${state}`; }
function render(data) {
  const state = data?.freshness?.truth_state || "unavailable"; $("truth").textContent = state; $("truth").className = `status ${state}`;
  $("freshness").textContent = `Observed ${data?.observed_at || "unknown"}. ${data?.errors?.length ? data.errors.join("; ") : "No source errors."} Refreshing every 15 seconds.`;
  const gateway = data.sources?.find((item) => item.source === "skgateway"); const vllm = data.sources?.find((item) => item.source === "vllm"); sourceState(gateway, "gateway-state"); sourceState(vllm, "vllm-state");
  const gs = gateway?.summary || {}; const vs = vllm?.summary || {};
  $("summary").innerHTML = [
    ["Requests", gs.totalRequests], ["Active", gs.activeRequests], ["Recent errors", gs.recentErrors5m], ["Queued", gs.pool?.totalQueued ?? gateway?.pool?.totalQueued],
    ["vLLM running", vs.running], ["vLLM queued", vs.queued], ["KV cache", vs.kv_cache_usage_percent === null || vs.kv_cache_usage_percent === undefined ? null : `${fmt(vs.kv_cache_usage_percent)}%`], ["Cached prompt tokens", vs.cached_prompt_tokens]
  ].map(([label,value]) => `<article class="metric"><span class="metric-label">${esc(label)}</span><strong class="metric-value">${esc(value === null || value === undefined ? "No data" : fmt(value))}</strong></article>`).join("");
  $("gateway").innerHTML = gateway ? [kv("Status", gateway.status), kv("Pool active", gateway.pool?.totalActive), kv("Pool capacity", gateway.pool?.totalCapacity), kv("Input tokens", gs.totalInputTokens), kv("Output tokens", gs.totalOutputTokens), kv("Unpriced requests", gs.unpricedRequests)].join("") : '<div class="empty">SKGateway unavailable.</div>';
  $("vllm").innerHTML = vllm ? [kv("Models", vllm.models?.join(", ")), kv("Prefix queries", vs.prefix_cache_queries), kv("Prefix hits", vs.prefix_cache_hits), kv("Preemptions", vs.preemptions), kv("Prompt tokens", vs.prompt_tokens), kv("Generation tokens", vs.generation_tokens)].join("") : '<div class="empty">vLLM unavailable.</div>';
  const backends = Object.entries(gateway?.backends || {}).map(([name,item]) => `<tr><td>${esc(name)}</td><td>${esc(item.status)}</td><td>${esc(fmt(item.errorRate))}</td><td>${esc(fmt(item.latencyP50))}</td></tr>`).join("");
  $("attribution").innerHTML = backends ? `<table><thead><tr><th>Backend</th><th>State</th><th>Error rate</th><th>P50 latency</th></tr></thead><tbody>${backends}</tbody></table>` : '<div class="empty">No backend attribution available.</div>';
}
async function refresh() { try { const response = await fetch("/api/v1/observability", {credentials:"same-origin", cache:"no-store"}); if (response.status === 401 || response.status === 503) { $("truth").textContent = response.status === 401 ? "Sign in required" : "Session unavailable"; $("truth").className = `status ${response.status === 401 ? "unknown" : "partial"}`; const link = $("obs-sign-in"); if (link) { link.hidden = false; link.href = `/auth/login?${new URLSearchParams({return_to: `${location.pathname}${location.search}`})}`; } return; } if (!response.ok) throw new Error(`HTTP ${response.status}`); render(await response.json()); } catch (error) { $("truth").textContent = "Unavailable"; $("truth").className = "status unavailable"; $("freshness").textContent = `Telemetry unavailable: ${error.message}`; } }
refresh(); setInterval(refresh, 15000);
