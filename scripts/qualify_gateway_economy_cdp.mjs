#!/usr/bin/env node
// Synthetic data only. Exercise the actual Economy assets in Chrome.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const assets = path.join(root, "src/skdashboard/static");
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "gateway-economy-browser-"));
const requests = [];
const errors = [];
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, description) {
  for (let attempt = 0; attempt < 150; attempt += 1) {
    if (await check()) return;
    await sleep(40);
  }
  throw new Error(description);
}
function fixture(query) {
  const mode = query.get("model") || "current";
  const state = ["stale", "partial", "empty"].includes(mode) ? mode : "current";
  const observed = new Date(Date.now() - (state === "stale" ? 300000 : 1000)).toISOString();
  const facts = {
    requests: { total: 120, rate_5m_per_second: 0.1, active_concurrency: 2, error_count: 1 },
    tokens: { input: 1200, output: 450 },
    cost: { total_usd: 0, truth: "unknown", unpriced_requests: 3 },
    generation: { throughput_tokens_per_second: { unavailable: "generation_not_observed" } },
    breakdowns: { models: ["<img src=x onerror=alert(1)>"], providers: ["local"], nodes: ["node-one"], clients: ["client-one"], apps: { unavailable: "application_not_observed" } },
    daily_token_rows: [{ model: "test-model", backend: "local-backend", agent: "test-client", input_tokens: 1200, output_tokens: 450 }],
    latency_ms: { "local-backend/test-model": { p50: 20, p95: 50, p99: 80, count: 120 } },
  };
  return {
    schema_version: "skdashboard.gateway.v1", state,
    unavailable_reason: state === "partial" ? "malformed_observations_omitted" : null,
    observed_at: state === "empty" ? null : observed, age_seconds: 1, ttl_seconds: 180,
    scope: query.get("scope") || "fleet", watermark: "synthetic-snapshot-one",
    filters: Object.fromEntries([...query].filter(([key]) => ["model", "provider", "node", "client", "app", "rail"].includes(key))),
    coverage: { returned: state === "empty" ? 0 : 1, examined: 2, malformed: state === "partial" ? 1 : 0 },
    items: state === "empty" ? [] : [{ observed_at: observed, watermark: "synthetic-snapshot-one", facts }],
  };
}
const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, "http://localhost");
  requests.push(url.pathname);
  if (url.pathname === "/api/v1/gateway/timeseries") {
    const mode = url.searchParams.get("model");
    if (mode === "slow") await sleep(500);
    const status = ["401", "403", "429", "503"].includes(mode) ? Number(mode) : 200;
    response.writeHead(status, { "Content-Type": "application/json" });
    response.end(JSON.stringify(status === 200 ? fixture(url.searchParams) : { state: "unavailable" }));
    return;
  }
  const relative = url.pathname === "/economy" ? "gateway_economy.html" : url.pathname.replace(/^\/static\//, "");
  const file = path.resolve(assets, relative);
  if (!file.startsWith(assets + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { response.writeHead(404); response.end(); return; }
  response.writeHead(200, { "Content-Type": file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : "text/html" });
  response.end(fs.readFileSync(file));
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const chrome = spawn(process.env.CHROME_PATH || "/usr/bin/google-chrome", ["--headless=new", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
let socket;
try {
  const activePort = path.join(profile, "DevToolsActivePort");
  await until(() => fs.existsSync(activePort), "Chrome startup");
  const port = fs.readFileSync(activePort, "utf8").split("\n")[0];
  const pages = await fetch(`http://127.0.0.1:${port}/json/list`).then((response) => response.json());
  socket = new WebSocket(pages.find((page) => page.type === "page").webSocketDebuggerUrl);
  await new Promise((resolve) => { socket.onopen = resolve; });
  const pending = new Map();
  let serial = 0;
  socket.onmessage = ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) { const pair = pending.get(message.id); pending.delete(message.id); message.error ? pair.reject(message.error) : pair.resolve(message.result); }
    if (message.method === "Runtime.exceptionThrown") errors.push(message.params);
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => { const id = ++serial; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })); });
  const evaluate = async (expression) => (await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true })).result.value;
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Page.navigate", { url: `${origin}/economy?scope=fleet` });
  await until(() => evaluate("document.getElementById('gateway-state')?.textContent === 'current'"), "Current snapshot render");
  assert.equal(await evaluate("document.querySelectorAll('#gateway-tables table').length"), 5);
  assert.equal(await evaluate("document.querySelectorAll('#gateway-tables img').length"), 0);
  assert.match(await evaluate("document.getElementById('gateway-tables').textContent"), /Unknown: generation_not_observed/);
  assert.equal(await evaluate("Array.from(document.querySelectorAll('tr')).find(row => row.textContent.includes('Cost (known portion)')).cells[1].textContent"), "Unknown: not observed");
  assert.equal(await evaluate("document.querySelectorAll('meter[aria-hidden=true]').length"), 1);
  const contrast = async () => evaluate(`(() => {
    const linear = v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; };
    const luminance = color => color.match(/[\\d.]+/g).slice(0,3).map(Number).map(linear).reduce((sum,v,i) => sum + v * [.2126,.7152,.0722][i], 0);
    const style = getComputedStyle(document.documentElement);
    const text = luminance(style.color), background = luminance(style.backgroundColor);
    return (Math.max(text,background)+.05)/(Math.min(text,background)+.05);
  })()`);
  assert.ok(await contrast() >= 4.5);
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "dark" }, { name: "prefers-reduced-motion", value: "reduce" }] });
  assert.ok(await contrast() >= 4.5);
  const tree = await send("Accessibility.getFullAXTree");
  const names = tree.nodes.map((node) => node.name?.value || "");
  assert.ok(names.includes("Gateway observation filters"));
  assert.ok(names.includes("Apply filters"));
  assert.ok(tree.nodes.some((node) => node.role?.value === "table"));
  assert.equal(await evaluate(`(async () => {
    const { gatewayFreshness, readGateway } = await import('/static/js/gateway_client.js');
    if (gatewayFreshness({state:'current',observed_at:'2026-01-01T00:00:00Z',ttl_seconds:10},Date.parse('2026-01-01T00:00:11Z')).state !== 'stale') return false;
    const original = window.fetch;
    try {
      const response = await original('/api/v1/gateway/timeseries?scope=fleet');
      const data = await response.json();
      data.scope = 'other-tenant';
      window.fetch = async () => ({ok:true,json:async()=>data});
      try { await readGateway(new URLSearchParams('scope=fleet')); return false; } catch (_) {}
      data.scope = 'fleet'; data.watermark = 'different-snapshot';
      try { await readGateway(new URLSearchParams('scope=fleet')); return false; } catch (_) {}
      return true;
    } finally { window.fetch = original; }
  })()`), true);
  assert.equal(await evaluate("(() => { const button=document.querySelector('button[type=submit]'); button.focus(); return document.activeElement===button; })()"), true);
  for (const width of [320, 390]) {
    await send("Emulation.setDeviceMetricsOverride", { width, height: 900, deviceScaleFactor: 1, mobile: false });
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
  }
  const change = (model) => evaluate(`(() => { const form=document.getElementById('gateway-filters'); form.elements.model.value=${JSON.stringify(model)}; form.requestSubmit(); return document.getElementById('gateway-tables').children.length; })()`);
  assert.equal(await change("slow"), 0);
  assert.match(await evaluate("document.getElementById('gateway-state').textContent"), /Loading/);
  await change("403");
  await until(() => evaluate("document.getElementById('gateway-state').textContent === 'Denied'"), "Denied state");
  await sleep(650);
  assert.equal(await evaluate("document.getElementById('gateway-tables').children.length"), 0);
  assert.equal(await evaluate("document.getElementById('gateway-state').textContent"), "Denied");
  const labels = { stale: "stale", partial: "partial", empty: "empty", 401: "Sign in required", 403: "Denied", 429: "Rate limited", 503: "Unavailable" };
  for (const [mode, label] of Object.entries(labels)) {
    await change(mode);
    await until(() => evaluate(`document.getElementById('gateway-state').textContent.startsWith(${JSON.stringify(label)})`), `${mode} state`);
    if (Number(mode)) assert.equal(await evaluate("document.getElementById('gateway-tables').children.length"), 0);
    assert.equal(await evaluate("new URLSearchParams(location.search).get('scope')"), "fleet");
  }
  await change("current");
  await until(() => evaluate("document.getElementById('gateway-state').textContent === 'current'"), "Recovery render");
  await evaluate("history.back()");
  await until(() => evaluate("document.getElementById('gateway-state').textContent === 'empty'"), "Browser back restores prior filter");
  assert.equal(errors.length, 0, JSON.stringify(errors));
  assert.ok(requests.every((url) => url === "/economy" || url === "/favicon.ico" || url.startsWith("/static/") || url.startsWith("/api/v1/")));
  console.log(JSON.stringify({ result: "PASS", states: ["current", "stale", "partial", "empty", "401", "403", "429", "503"], responsive: [320, 390], staleResponseBlocked: true, noLegacyRequests: true, accessibilityTree: true, unknownCost: true, tableEquivalent: true, exceptions: errors.length }));
} finally {
  socket?.close();
  chrome.kill("SIGTERM");
  await new Promise((resolve) => { if (chrome.exitCode !== null) resolve(); else chrome.once("exit", resolve); });
  await new Promise((resolve) => server.close(resolve));
  fs.rmSync(profile, { recursive: true, force: true });
}
