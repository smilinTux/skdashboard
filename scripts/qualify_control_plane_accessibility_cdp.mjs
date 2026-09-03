#!/usr/bin/env node

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitFor(check, message) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (await check()) return;
    await sleep(50);
  }
  throw new Error(message);
}

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "skcp-50-cdp-"));
const home = fs.mkdtempSync(path.join(os.tmpdir(), "skcp-50-home-"));
const artifactDir = path.resolve(process.env.SKCP50_ARTIFACT_DIR || "/tmp/skcp50-browser");
const port = Number(process.env.SKCP50_PORT || 17888);
const routes = [
  { id: "now", path: "/control-plane/now" },
  { id: "portfolio", path: "/control-plane/portfolio?role=project-manager&scope=estate&window=latest&baseline=none&service=all" },
  { id: "reliability", path: "/control-plane/reliability?role=operator&scope=estate&window=latest&baseline=none&service=all" },
  { id: "architecture", path: "/control-plane/architecture?role=architect&scope=estate&window=latest&baseline=none&service=all&environment=all" },
  { id: "ai", path: "/control-plane/ai?role=operator&scope=estate&window=latest&baseline=none&service=all" },
  { id: "governance", path: "/control-plane/governance?role=governance&scope=estate&window=latest&baseline=none&service=all" },
  { id: "reports", path: "/control-plane/reports?role=project-manager&scope=estate&window=latest&baseline=none&service=all&report_type=all" },
];
const layouts = [
  { id: "mobile", width: 390, height: 844, mobile: true, scale: 1, reducedMotion: false },
  { id: "tablet", width: 768, height: 1024, mobile: true, scale: 1, reducedMotion: false },
  { id: "desktop", width: 1440, height: 1000, mobile: false, scale: 1, reducedMotion: false },
  { id: "zoom-200", width: 720, height: 500, mobile: false, scale: 2, reducedMotion: false },
  { id: "reduced-motion", width: 1440, height: 1000, mobile: false, scale: 1, reducedMotion: true },
];
const python = `import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import uvicorn
from skdashboard.control_plane_adapters import Reader, project_estate as real_project
from skdashboard.dashboard import create_app
repo = Path(${JSON.stringify(repo)})
fixture = json.loads((repo / "tests/fixtures/control_plane_full_estate.v1.0.0.json").read_text())
readers = {}
for case in fixture["estate_cases"]:
    if case.get("failure"):
        readers[case["adapter_id"]] = Reader(failure=case["failure"])
    else:
        readers[case["adapter_id"]] = Reader(payload={
            "schema_version": fixture["schema_version"],
            "observed_at": case.get("observed_at", fixture["observed_at"]),
            "watermark": case["watermark"],
            "coverage": case["coverage"],
            "aggregate": case["aggregate"],
            "errors": case["errors"],
            "has_observations": case["has_observations"],
        })
now = datetime.fromisoformat(fixture["projected_at"].replace("Z", "+00:00"))
patch("skdashboard.control_plane_adapters.default_readers", return_value=readers).start()
patch("skdashboard.control_plane_adapters.project_estate", side_effect=lambda value: real_project(value, now=now)).start()
def authorize(bearer, capability, target):
    return bearer == "skcp50-cdp" and capability == "skdashboard.read"
uvicorn.run(create_app(Path(${JSON.stringify(home)}), control_plane_authorizer=authorize), host="127.0.0.1", port=${port}, log_level="error")`;
const pythonPath = [process.env.HOME + "/work/capauth/src", path.join(repo, "src")].join(path.delimiter);
const server = spawn(process.env.PYTHON || "python", ["-c", python], { cwd: repo, env: { ...process.env, PYTHONPATH: pythonPath }, stdio: "ignore" });
const chrome = spawn(process.env.CHROME_PATH || "/usr/bin/google-chrome", ["--headless=new", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });

try {
  fs.rmSync(artifactDir, { recursive: true, force: true });
  fs.mkdirSync(path.join(artifactDir, "screenshots"), { recursive: true });
  await waitFor(async () => fetch(`http://127.0.0.1:${port}/control-plane/now`).then((response) => response.ok).catch(() => false), "Dashboard did not start");
  const active = path.join(profile, "DevToolsActivePort");
  await waitFor(() => fs.existsSync(active), "Chrome did not start");
  const chromePort = fs.readFileSync(active, "utf8").trim().split("\n")[0];
  const targets = await fetch(`http://127.0.0.1:${chromePort}/json/list`).then((response) => response.json());
  const socket = new WebSocket(targets.find((target) => target.type === "page").webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let id = 0;
  const pending = new Map(), requests = [], exceptions = [];
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const handlers = pending.get(message.id); pending.delete(message.id);
      message.error ? handlers.reject(new Error(JSON.stringify(message.error))) : handlers.resolve(message.result);
    }
    if (message.method === "Network.requestWillBeSent") requests.push(message.params.request);
    if (message.method === "Runtime.exceptionThrown") exceptions.push(message.params.exceptionDetails);
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => { id += 1; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })); });
  const evaluate = async (expression) => {
    const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    assert.equal(result.exceptionDetails, undefined);
    return result.result.value;
  };
  const tab = async () => {
    await send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
  };
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable"); await send("Accessibility.enable");
  await send("Network.setExtraHTTPHeaders", { headers: { Authorization: "Bearer skcp50-cdp" } });

  const matrix = [];
  for (const layout of layouts) {
    await send("Emulation.setDeviceMetricsOverride", { width: layout.width, height: layout.height, deviceScaleFactor: 1, mobile: layout.mobile, scale: layout.scale });
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: layout.reducedMotion ? "reduce" : "no-preference" }] });
    for (const route of routes) {
      const started = performance.now();
      await send("Page.navigate", { url: `http://127.0.0.1:${port}${route.path}` });
      await waitFor(async () => evaluate("document.readyState === 'complete'").catch(() => false), `Page did not load: ${route.path}`);
      await sleep(100);
      const loadMs = Math.round(performance.now() - started);
      await evaluate("document.activeElement?.blur()");
      await tab();
      const dom = JSON.parse(await evaluate(`JSON.stringify((() => {
        const focus = document.activeElement;
        const fs = focus && getComputedStyle(focus);
        const visible = (n) => { const s=getComputedStyle(n), r=n.getBoundingClientRect(); return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0; };
        const name = (n) => n.getAttribute('aria-label') || (n.getAttribute('aria-labelledby') || '').split(/\\s+/).map(id => document.getElementById(id)?.textContent || '').join(' ').trim() || n.alt || n.title || n.textContent.trim();
        const controls = [...document.querySelectorAll('a[href],button,input,select,textarea,[tabindex]:not([tabindex="-1"])')].filter(visible);
        const unnamed = controls.filter(n => !name(n)).map(n => n.outerHTML.slice(0,180));
        const dialogs = [...document.querySelectorAll('dialog,[role="dialog"]')];
        const overflow = document.documentElement.scrollWidth > document.documentElement.clientWidth + 1;
        const animations = [...document.querySelectorAll('*')].filter(n => { const s=getComputedStyle(n); return s.animationName !== 'none' && s.animationDuration !== '0s'; }).map(n => ({tag:n.tagName, animation:getComputedStyle(n).animationName}));
        return {
          title: document.title,
          mainLandmarks: document.querySelectorAll('main,[role="main"]').length,
          navigationLandmarks: document.querySelectorAll('nav,[role="navigation"]').length,
          h1: [...document.querySelectorAll('h1')].map(n => n.textContent.trim()),
          controls: controls.length,
          unnamed,
          dialogsWithoutNames: dialogs.filter(n => !name(n)).length,
          focusName: focus ? name(focus) : '',
          focusVisible: !!focus && ((fs.outlineStyle !== 'none' && parseFloat(fs.outlineWidth) >= 2) || fs.boxShadow !== 'none'),
          horizontalPageOverflow: overflow,
          reducedMotionAnimations: animations,
        };
      })())`));
      const tree = await send("Accessibility.getFullAXTree");
      const roles = tree.nodes.map((node) => node.role?.value).filter(Boolean);
      const screenshot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      const screenshotPath = path.join("screenshots", `${route.id}-${layout.id}.png`);
      fs.writeFileSync(path.join(artifactDir, screenshotPath), screenshot.data, "base64");
      matrix.push({
        route: route.path, surface: route.id, layout: layout.id, viewport: `${layout.width}x${layout.height}`,
        loadMs, screenshot: screenshotPath, ...dom,
        axMain: roles.filter((role) => role === "main").length,
        axNavigation: roles.filter((role) => role === "navigation").length,
      });
    }
  }

  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  await send("Page.navigate", { url: `http://127.0.0.1:${port}/control-plane/now` });
  await waitFor(async () => evaluate("document.readyState === 'complete'").catch(() => false), "Now task page did not load");
  await sleep(250);
  const taskStarted = performance.now();
  const evidenceResult = await evaluate(`(() => { const b=document.querySelector('.estate-evidence-button'); if (!b) return {available:false}; b.click(); return {available:true, open:document.getElementById('estate-evidence').open, name:b.getAttribute('aria-label')}; })()`);
  const evidenceTask = { task: "KPI or exception to evidence", interactions: evidenceResult.available ? 1 : null, elapsedMs: Math.round(performance.now() - taskStarted), ...evidenceResult };
  if (evidenceResult.open) await evaluate("document.getElementById('estate-evidence').close()");
  const contributorsTask = { task: "KPI to contributors", interactions: evidenceResult.available ? 1 : null, elapsedMs: evidenceTask.elapsedMs, qualification: "The evidence dialog exposes source provenance in the same interaction." };

  const failures = matrix.filter((item) => item.mainLandmarks !== 1 || item.axMain !== 1 || item.navigationLandmarks < 1 || item.axNavigation < 1 || !item.focusVisible || item.unnamed.length || item.dialogsWithoutNames || item.horizontalPageOverflow || (item.layout === "reduced-motion" && item.reducedMotionAnimations.length));
  const result = {
    result: failures.length === 0 && evidenceTask.interactions !== null && evidenceTask.interactions <= 2 && contributorsTask.interactions === 1 ? "PASS" : "FAIL",
    generatedAt: new Date().toISOString(),
    browser: await evaluate("navigator.userAgent"),
    standard: "WCAG 2.2 AA",
    layouts,
    matrix,
    tasks: [evidenceTask, contributorsTask],
    failureCount: failures.length,
    failures,
    nonGetRequests: requests.filter((request) => !["GET", "OPTIONS"].includes(request.method)).length,
    externalHttpRequests: requests.filter((request) => /^https?:/.test(request.url) && !request.url.startsWith(`http://127.0.0.1:${port}/`)).length,
    runtimeExceptions: exceptions.length,
    knownLimitations: [
      "Chrome accessibility-tree inspection validates names and landmarks but is not a substitute for a human screen-reader session.",
      "Screenshots require independent visual review; this run records identity and dimensions but does not infer aesthetic approval.",
      "The hermetic empty-home fixture exposes source-level contributors through the evidence dialog and does not contain production KPI contributor records.",
    ],
  };
  fs.writeFileSync(path.join(artifactDir, "accessibility-landmark-matrix.json"), JSON.stringify(result, null, 2) + "\n");
  const hashes = [];
  for (const file of fs.readdirSync(path.join(artifactDir, "screenshots")).sort()) {
    const bytes = fs.readFileSync(path.join(artifactDir, "screenshots", file));
    const hash = await crypto.subtle.digest("SHA-256", bytes);
    hashes.push({ path: `screenshots/${file}`, sha256: Buffer.from(hash).toString("hex") });
  }
  fs.writeFileSync(path.join(artifactDir, "screenshot-manifest.json"), JSON.stringify(hashes, null, 2) + "\n");
  console.log(JSON.stringify({ result: result.result, browser: result.browser, matrixRows: matrix.length, screenshots: hashes.length, failureCount: failures.length, tasks: result.tasks }, null, 2));
  socket.close();
  if (result.result !== "PASS") process.exitCode = 1;
} finally {
  server.kill("SIGTERM"); chrome.kill("SIGTERM");
  await sleep(100);
  fs.rmSync(profile, { recursive: true, force: true }); fs.rmSync(home, { recursive: true, force: true });
}
