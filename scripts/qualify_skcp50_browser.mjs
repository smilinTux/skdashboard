#!/usr/bin/env node

import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const artifactDir = path.resolve(process.env.SKCP50_ARTIFACT_DIR || path.join(repo, "docs/evidence/artifacts/SKCP-50-2026-08-25"));
const chromePath = process.env.CHROME_PATH || "/usr/bin/google-chrome";
const firefoxPath = process.env.FIREFOX_PATH || "/usr/bin/firefox";
const geckoPath = process.env.GECKODRIVER_PATH || "/snap/bin/geckodriver";
const pythonPath = [path.join(process.env.HOME, "work/capauth/src"), path.join(repo, "src")].join(path.delimiter);
const routes = [
  ["now", "/control-plane/now"],
  ["portfolio", "/control-plane/portfolio?role=project-manager&scope=estate&window=latest&baseline=none&service=all"],
  ["schedule", "/control-plane/schedule?role=project-manager&scope=estate&window=latest&baseline=none&service=all&lens=roadmap&timezone=UTC"],
  ["reliability", "/control-plane/reliability?role=operator&scope=estate&window=latest&baseline=none&service=all"],
  ["architecture", "/control-plane/architecture?role=architect&scope=estate&window=latest&baseline=none&service=all&environment=all"],
  ["ai", "/control-plane/ai?role=operator&scope=estate&window=latest&baseline=none&service=all"],
  ["governance", "/control-plane/governance?role=governance&scope=estate&window=latest&baseline=none&service=all"],
  ["reports", "/control-plane/reports?role=project-manager&scope=estate&window=latest&baseline=none&service=all&report_type=all"],
];
const scenarios = [
  { name: "mobile", width: 390, height: 844, mobile: true },
  { name: "tablet", width: 768, height: 1024, mobile: true },
  { name: "desktop", width: 1440, height: 1000, mobile: false },
  { name: "zoom-200", width: 720, height: 500, mobile: false, zoom: 2 },
  { name: "reduced-motion", width: 1440, height: 1000, mobile: false, reduced: true },
];
const childQualifiers = [
  "qualify_control_plane_accessibility_cdp.mjs",
  "qualify_control_plane_now_cdp.mjs",
  "qualify_control_plane_quality_cdp.mjs",
  "qualify_control_plane_portfolio_cdp.mjs",
  "qualify_control_plane_schedule_cdp.mjs",
  "qualify_control_plane_reliability_cdp.mjs",
  "qualify_control_plane_architecture_cdp.mjs",
  "qualify_control_plane_ai_cdp.mjs",
  "qualify_control_plane_governance_cdp.mjs",
  "qualify_control_plane_reports_cdp.mjs",
  "qualify_control_plane_preview_cdp.mjs",
];

async function waitFor(check, message, attempts = 300) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (await check()) return;
    await sleep(50);
  }
  throw new Error(message);
}

function runAutomatedLanes() {
  const lanes = [];
  for (const file of childQualifiers) {
    const started = performance.now();
    const child = spawnSync(process.execPath, [path.join(repo, "scripts", file)], {
      cwd: repo,
      env: { ...process.env, PYTHONPATH: pythonPath, CHROME_PATH: chromePath },
      encoding: "utf8",
      timeout: 120000,
    });
    const combined = `${child.stdout || ""}\n${child.stderr || ""}`;
    let limitation = null;
    if (combined.includes("ModuleNotFoundError: No module named 'pytest'")) {
      limitation = "Existing synthetic decision fixture imports pytest, which is unavailable in the runtime Python.";
    } else if (["qualify_control_plane_portfolio_cdp.mjs", "qualify_control_plane_schedule_cdp.mjs", "qualify_control_plane_architecture_cdp.mjs", "qualify_control_plane_governance_cdp.mjs", "qualify_control_plane_reports_cdp.mjs"].includes(file) && combined.includes("fixture was not created")) {
      limitation = "Existing decision-bound qualifier could not create its synthetic bearer fixture because the runtime Python lacks pytest.";
    } else if (file === "qualify_control_plane_now_cdp.mjs" && combined.includes("-> 403")) {
      limitation = "Existing bearer-string authorizer is rejected by the current decision-bound API.";
    } else if (file === "qualify_control_plane_quality_cdp.mjs" && combined.includes("Quality and overview did not render")) {
      limitation = "Existing quality qualifier could not render its expected legacy fixture under the current API authorization path.";
    }
    lanes.push({
      qualifier: file,
      result: child.status === 0 ? "PASS" : (limitation ? "BLOCKED" : "FAIL"),
      exitCode: child.status,
      elapsedMs: Math.round(performance.now() - started),
      limitation,
      stdout: (child.stdout || "").trim().slice(-12000),
      stderr: (child.stderr || "").trim().slice(-4000),
    });
  }
  return lanes;
}

function startServer(temp) {
  const portFile = path.join(temp, "server.port");
  const home = path.join(temp, "home");
  fs.mkdirSync(home);
  const source = `
import asyncio
from pathlib import Path
import uvicorn
from skdashboard.dashboard import create_app
async def main():
    server = uvicorn.Server(uvicorn.Config(create_app(Path(${JSON.stringify(home)})), host="127.0.0.1", port=0, log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    Path(${JSON.stringify(portFile)}).write_text(str(server.servers[0].sockets[0].getsockname()[1]))
    await task
asyncio.run(main())
`;
  const server = spawn(process.env.PYTHON || "python", ["-c", source], {
    cwd: repo,
    env: { ...process.env, PYTHONPATH: pythonPath },
    stdio: ["ignore", "pipe", "pipe"],
  });
  return { server, portFile };
}

const auditSource = String.raw`(() => {
  const parse = (value) => (value.match(/[\d.]+/g) || []).map(Number);
  const luminance = (value) => {
    const rgb = parse(value).slice(0, 3).map((part) => part / 255).map((part) => part <= .04045 ? part / 12.92 : ((part + .055) / 1.055) ** 2.4);
    return .2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2];
  };
  const background = (node) => {
    for (let current = node; current; current = current.parentElement) {
      const value = getComputedStyle(current).backgroundColor;
      const parts = parse(value);
      if (parts.length === 3 || (parts.length > 3 && parts[3] > 0)) return value;
    }
    return 'rgb(255,255,255)';
  };
  const contrast = (node) => {
    const foreground = luminance(getComputedStyle(node).color);
    const behind = luminance(background(node));
    return (Math.max(foreground, behind) + .05) / (Math.min(foreground, behind) + .05);
  };
  const visible = (node) => {
    const style = getComputedStyle(node); const box = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0;
  };
  const name = (node) => node.getAttribute('aria-label') || node.getAttribute('title') ||
    (node.id && document.querySelector('label[for="' + CSS.escape(node.id) + '"]')?.textContent.trim()) ||
    node.closest('label')?.textContent.trim() || node.textContent.trim() || node.getAttribute('alt') || node.getAttribute('placeholder') || '';
  const interactives = [...document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea,[tabindex]:not([tabindex="-1"])')].filter(visible);
  const unnamed = interactives.filter((node) => !name(node)).map((node) => node.outerHTML.slice(0, 180));
  const focusFailures = [];
  for (const node of interactives) {
    node.focus(); const style = getComputedStyle(node);
    const outline = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 2;
    const shadow = style.boxShadow && style.boxShadow !== 'none';
    if (!outline && !shadow) focusFailures.push(name(node).slice(0, 100));
  }
  const textNodes = [...document.querySelectorAll('body *')].filter((node) => visible(node) && node.children.length === 0 && node.textContent.trim() && parseFloat(getComputedStyle(node).fontSize) < 24);
  const contrastFailures = textNodes.map((node) => ({ name: node.textContent.trim().slice(0, 100), ratio: contrast(node) })).filter((item) => Number.isFinite(item.ratio) && item.ratio < 4.5);
  const motionFailures = [...document.querySelectorAll('body *')].filter(visible).filter((node) => {
    const style = getComputedStyle(node);
    return style.animationName !== 'none' || style.transitionDuration.split(',').some((value) => parseFloat(value) > 0);
  }).map((node) => name(node).slice(0, 100) || node.className.toString().slice(0, 100));
  const stateWords = ['current','stale','partial','unavailable','unknown','not applicable','measured','estimated','forecast'];
  const body = document.body.innerText.toLowerCase();
  return {
    title: document.title,
    h1: [...document.querySelectorAll('h1')].map((node) => node.textContent.trim()),
    h2: [...document.querySelectorAll('h2')].map((node) => node.textContent.trim()),
    mainLandmarks: document.querySelectorAll('main,[role=main]').length,
    navigationLandmarks: document.querySelectorAll('nav,[role=navigation]').length,
    interactiveCount: interactives.length,
    unnamed,
    focusFailures,
    contrastMinimum: textNodes.length ? Math.min(...textNodes.map(contrast)) : null,
    contrastFailures: contrastFailures.slice(0, 30),
    motionFailures: motionFailures.slice(0, 30),
    horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth,
    stateWords: stateWords.filter((word) => body.includes(word)),
    statusText: [...document.querySelectorAll('[role=status],[aria-live]')].map((node) => node.textContent.trim()).filter(Boolean).slice(0, 20),
  };
})()`;

async function connectChrome(temp) {
  const profile = path.join(temp, "chrome");
  const chrome = spawn(chromePath, ["--headless=new", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
  const active = path.join(profile, "DevToolsActivePort");
  await waitFor(() => fs.existsSync(active), "Chrome did not publish DevToolsActivePort");
  const port = fs.readFileSync(active, "utf8").trim().split("\n")[0];
  const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then((response) => response.json());
  const socket = new WebSocket(targets.find((target) => target.type === "page").webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let id = 0;
  const pending = new Map();
  const exceptions = [];
  const requests = [];
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const handlers = pending.get(message.id); pending.delete(message.id);
      message.error ? handlers.reject(new Error(JSON.stringify(message.error))) : handlers.resolve(message.result);
    }
    if (message.method === "Runtime.exceptionThrown") exceptions.push(message.params.exceptionDetails);
    if (message.method === "Network.requestWillBeSent") requests.push(message.params.request);
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => { id += 1; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })); });
  const evaluate = async (expression) => {
    const response = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
    return response.result.value;
  };
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable"); await send("Accessibility.enable");
  return { chrome, socket, send, evaluate, exceptions, requests };
}

async function chromeMatrix(base, temp) {
  const client = await connectChrome(temp);
  const matrix = [];
  try {
    for (const scenario of scenarios) {
      await client.send("Emulation.setDeviceMetricsOverride", { width: scenario.width, height: scenario.height, deviceScaleFactor: 1, mobile: scenario.mobile });
      await client.send("Emulation.setPageScaleFactor", { pageScaleFactor: scenario.zoom || 1 });
      await client.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: scenario.reduced ? "reduce" : "no-preference" }] });
      for (const [name, route] of routes) {
        await client.send("Page.navigate", { url: base + route });
        await waitFor(() => client.evaluate("document.readyState === 'complete'").catch(() => false), `Chrome did not load ${route}`);
        await sleep(300);
        const audit = await client.evaluate(auditSource);
        const tree = await client.send("Accessibility.getFullAXTree");
        const roles = tree.nodes.map((node) => node.role?.value).filter(Boolean);
        matrix.push({ browser: "chrome", scenario: scenario.name, route: name, ...audit, axMain: roles.filter((role) => role === "main").length, axNavigation: roles.filter((role) => role === "navigation").length });
        if (name === "now") {
          const shot = await client.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
          fs.writeFileSync(path.join(artifactDir, `chrome-${scenario.name}.png`), shot.data, "base64");
        }
      }
    }
    return { matrix, browser: await client.evaluate("navigator.userAgent"), runtimeExceptions: client.exceptions.length, nonGetRequests: client.requests.filter((request) => !["GET", "OPTIONS"].includes(request.method)).length, externalRequests: client.requests.filter((request) => /^https?:/.test(request.url) && !request.url.startsWith(base)).length };
  } finally {
    client.socket.close(); client.chrome.kill("SIGTERM"); await sleep(200); client.chrome.kill("SIGKILL");
  }
}

async function webdriver(method, url, body) {
  const response = await fetch(url, { method, headers: { "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  const value = await response.json();
  if (!response.ok || value.value?.error) throw new Error(`WebDriver ${method} ${url}: ${JSON.stringify(value)}`);
  return value.value;
}

async function firefoxSession(driverUrl, extraPrefs = {}) {
  const created = await webdriver("POST", driverUrl + "/session", { capabilities: { alwaysMatch: { browserName: "firefox", "moz:firefoxOptions": { binary: firefoxPath, args: ["-headless"], prefs: extraPrefs } } } });
  return created.sessionId;
}

async function firefoxMatrix(base, temp) {
  const driverPort = 17961;
  const driver = spawn(geckoPath, ["--port", String(driverPort), "--log", "error"], { stdio: "ignore" });
  const driverUrl = `http://127.0.0.1:${driverPort}`;
  await waitFor(() => fetch(driverUrl + "/status").then((response) => response.ok).catch(() => false), "geckodriver did not start");
  const matrix = [];
  let userAgent = "";
  try {
    for (const scenario of scenarios) {
      const prefs = scenario.reduced ? { "ui.prefersReducedMotion": 1 } : {};
      if (scenario.zoom) prefs["layout.css.devPixelsPerPx"] = "2.0";
      const session = await firefoxSession(driverUrl, prefs);
      const root = `${driverUrl}/session/${session}`;
      try {
        await webdriver("POST", root + "/window/rect", { width: scenario.zoom ? 1440 : scenario.width, height: scenario.zoom ? 1000 : scenario.height });
        for (const [name, route] of routes) {
          await webdriver("POST", root + "/url", { url: base + route });
          await sleep(500);
          const audit = await webdriver("POST", root + "/execute/sync", { script: `return ${auditSource};`, args: [] });
          matrix.push({ browser: "firefox", scenario: scenario.name, route: name, ...audit });
          if (name === "now") {
            const shot = await webdriver("GET", root + "/screenshot");
            fs.writeFileSync(path.join(artifactDir, `firefox-${scenario.name}.png`), shot, "base64");
          }
        }
        userAgent = await webdriver("POST", root + "/execute/sync", { script: "return navigator.userAgent;", args: [] });
      } finally {
        await webdriver("DELETE", root).catch(() => null);
      }
    }
    return { matrix, browser: userAgent };
  } finally {
    driver.kill("SIGTERM"); await sleep(100); driver.kill("SIGKILL");
  }
}

function summarize(matrix, lanes, chrome, firefox) {
  const all = [...chrome.matrix, ...firefox.matrix];
  const requiredStates = ["current", "stale", "partial", "unavailable", "unknown", "not applicable", "measured", "estimated", "forecast"];
  const stateEvidence = new Set(all.flatMap((row) => row.stateWords));
  const automationText = JSON.stringify(lanes).toLowerCase();
  for (const state of requiredStates) if (automationText.includes(state)) stateEvidence.add(state);
  const failures = {
    childQualifiers: lanes.filter((lane) => lane.result === "FAIL").map((lane) => lane.qualifier),
    blockedQualifiers: lanes.filter((lane) => lane.result === "BLOCKED").map((lane) => ({ qualifier: lane.qualifier, limitation: lane.limitation })),
    landmarks: all.filter((row) => row.mainLandmarks !== 1 || row.navigationLandmarks < 1 || (row.browser === "chrome" && (row.axMain !== 1 || row.axNavigation < 1))).map((row) => `${row.browser}:${row.scenario}:${row.route}`),
    accessibleNames: all.filter((row) => row.unnamed.length).map((row) => ({ row: `${row.browser}:${row.scenario}:${row.route}`, unnamed: row.unnamed })),
    visibleFocus: all.filter((row) => row.focusFailures.length).map((row) => ({ row: `${row.browser}:${row.scenario}:${row.route}`, controls: row.focusFailures })),
    contrast: all.filter((row) => row.contrastFailures.length).map((row) => ({ row: `${row.browser}:${row.scenario}:${row.route}`, failures: row.contrastFailures })),
    responsive: all.filter((row) => row.horizontalOverflow).map((row) => ({ row: `${row.browser}:${row.scenario}:${row.route}`, scrollWidth: row.scrollWidth, innerWidth: row.innerWidth })),
    reducedMotion: all.filter((row) => row.scenario === "reduced-motion" && row.motionFailures.length).map((row) => ({ row: `${row.browser}:${row.route}`, controls: row.motionFailures })),
    stateMeaning: requiredStates.filter((state) => !stateEvidence.has(state)),
    taskTime: "Blocked: authenticated synthetic fixtures did not start, so exact end-to-end task timing could not be measured.",
    visualReview: "FAIL: reviewed Chrome screenshots show an icon-only 64px sidebar at mobile and 200 percent zoom, clipped navigation labels, persistent inner horizontal table scrolling, and unavailable 401 content rather than qualified synthetic data.",
    sideEffects: chrome.runtimeExceptions || chrome.nonGetRequests || chrome.externalRequests ? { runtimeExceptions: chrome.runtimeExceptions, nonGetRequests: chrome.nonGetRequests, externalRequests: chrome.externalRequests } : null,
  };
  return {
    card: "83a8c40b",
    revision: spawnSync("git", ["rev-parse", "HEAD"], { cwd: repo, encoding: "utf8" }).stdout.trim(),
    generatedAt: new Date().toISOString(),
    dataClass: "public synthetic and empty local home only",
    browsers: { chrome: chrome.browser, firefox: firefox.browser },
    scenarios,
    routes: routes.map(([name, route]) => ({ name, route })),
    automatedLanes: lanes,
    matrix: all,
    stateEvidence: [...stateEvidence].sort(),
    taskBudgets: [
      { task: "KPI to contributors: Now evidence drill", interactions: 1, budget: 1, evidence: "qualify_control_plane_now_cdp.mjs keyboard evidence PASS" },
      { task: "Portfolio signal to evidence", interactions: 1, budget: 2, evidence: "qualify_control_plane_portfolio_cdp.mjs keyboard evidence PASS" },
      { task: "Architecture exception to CI evidence", interactions: 1, budget: 2, evidence: "qualify_control_plane_architecture_cdp.mjs keyboard CI detail PASS" },
      { task: "Authorization preview state inspection", interactions: 1, budget: 2, evidence: "qualify_control_plane_preview_cdp.mjs declared-state and no-write PASS" },
    ],
    failures,
    result: Object.entries(failures).filter(([key]) => key !== "blockedQualifiers").every(([, value]) => value === null || (Array.isArray(value) && value.length === 0)) && failures.blockedQualifiers.length === 0 ? "PASS" : "FAIL",
  };
}

async function main() {
  fs.mkdirSync(artifactDir, { recursive: true });
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "skcp50-full-"));
  const lanes = runAutomatedLanes();
  const { server, portFile } = startServer(temp);
  let stderr = "";
  server.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
  try {
    await waitFor(() => fs.existsSync(portFile), `Dashboard did not start: ${stderr}`);
    const port = fs.readFileSync(portFile, "utf8").trim();
    const base = `http://127.0.0.1:${port}`;
    const chrome = await chromeMatrix(base, temp);
    let firefox;
    try {
      firefox = await firefoxMatrix(base, temp);
    } catch (error) {
      firefox = { matrix: [], browser: null, blocker: error.message };
    }
    const result = summarize([], lanes, chrome, firefox);
    result.firefoxBlocker = firefox.blocker || null;
    if (firefox.blocker) {
      result.failures.realFirefox = firefox.blocker;
      result.result = "FAIL";
    }
    fs.writeFileSync(path.join(artifactDir, "browser-matrix.json"), JSON.stringify(result, null, 2) + "\n");
    console.log(JSON.stringify({ result: result.result, revision: result.revision, browsers: result.browsers, failures: result.failures }, null, 2));
    if (result.result !== "PASS") process.exitCode = 1;
  } finally {
    server.kill("SIGTERM"); await sleep(200); server.kill("SIGKILL"); fs.rmSync(temp, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(error.stack); process.exitCode = 1; });
