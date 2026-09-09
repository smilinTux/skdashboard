#!/usr/bin/env node

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function stop(child) {
  if (child.exitCode !== null) return;
  const exited = new Promise((resolve) => child.once("exit", resolve));
  child.kill("SIGTERM");
  await Promise.race([exited, sleep(1000)]);
  if (child.exitCode === null) {
    const killed = new Promise((resolve) => child.once("exit", resolve));
    child.kill("SIGKILL");
    await Promise.race([killed, sleep(1000)]);
  }
}

async function waitFor(check, message) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (await check()) return;
    await sleep(50);
  }
  throw new Error(message);
}

async function qualify() {
  const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "skdash-live-cdp-"));
  const port = 17893;
  const server = spawn(process.env.PYTHON || "python", ["-m", "http.server", String(port), "--bind", "127.0.0.1"], { cwd: repo, stdio: "ignore" });
  const chrome = spawn(process.env.CHROME_PATH || "/usr/bin/google-chrome", ["--headless=new", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });

  try {
    await waitFor(() => fetch(`http://127.0.0.1:${port}/src/skdashboard/static/js/live_connection.js`).then((response) => response.ok).catch(() => false), "Static server did not start");
    const activePort = path.join(profile, "DevToolsActivePort");
    await waitFor(() => fs.existsSync(activePort), "Chrome did not publish DevToolsActivePort");
    const chromePort = fs.readFileSync(activePort, "utf8").trim().split("\n")[0];
    const targets = await fetch(`http://127.0.0.1:${chromePort}/json/list`).then((response) => response.json());
    const socket = new WebSocket(targets.find((candidate) => candidate.type === "page").webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
    let nextId = 0;
    const pending = new Map();
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (!message.id || !pending.has(message.id)) return;
      const handlers = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) handlers.reject(new Error(JSON.stringify(message.error)));
      else handlers.resolve(message.result);
    };
    const send = (method, params = {}) => new Promise((resolve, reject) => {
      nextId += 1;
      pending.set(nextId, { resolve, reject });
      socket.send(JSON.stringify({ id: nextId, method, params }));
    });
    await send("Page.enable");
    await send("Runtime.enable");
    await send("Page.navigate", { url: `http://127.0.0.1:${port}/` });
    await waitFor(async () => {
      const result = await send("Runtime.evaluate", { expression: "document.readyState", returnByValue: true });
      return result.result.value === "complete";
    }, "Browser page did not load");
    const expression = `(async () => {
      const { createLiveConnection } = await import('/src/skdashboard/static/js/live_connection.js');
      const text = { textContent: '' };
      const classes = new Set();
      const dot = { dataset: {}, classList: { toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name) } };
      class FakeEventSource {
        static OPEN = 1;
        static instances = [];
        constructor(url) { this.url = url; this.readyState = 0; this.listeners = {}; this.closed = false; FakeEventSource.instances.push(this); }
        addEventListener(name, callback) { this.listeners[name] = callback; }
        emit(name) { if (name === 'open') this.readyState = 1; this.listeners[name](); }
        close() { this.closed = true; this.readyState = 2; }
      }
      let signIns = 0;
      let refreshes = 0;
      const controller = createLiveConnection({ dot, text, refresh: () => { refreshes += 1; }, signIn: () => { signIns += 1; }, EventSourceClass: FakeEventSource, retryLimit: 3 });
      const states = [];
      controller.start(); states.push(text.textContent);
      const stream = FakeEventSource.instances[0];
      controller.pollSucceeded(); states.push(text.textContent);
      stream.emit('open'); states.push(text.textContent);
      stream.emit('error'); states.push(text.textContent);
      stream.emit('error'); stream.emit('error');
      controller.pollFailed({ status: 503 }); states.push(text.textContent);
      controller.pollFailed(new TypeError('network')); states.push(text.textContent);
      controller.pollFailed({ status: 401 }); states.push(text.textContent);
      return { states, endpoint: stream.url, closed: stream.closed, refreshes, signIns, dotState: dot.dataset.state, live: classes.has('on') };
    })()`;
    const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    assert.equal(result.exceptionDetails, undefined);
    assert.deepEqual(result.result.value.states, ["retrying", "polling fallback", "connected", "polling fallback", "retrying", "offline", "sign in required"]);
    assert.equal(result.result.value.endpoint, "/api/v1/events");
    assert.equal(result.result.value.closed, true);
    assert.equal(result.result.value.refreshes, 3);
    assert.equal(result.result.value.signIns, 1);
    assert.equal(result.result.value.dotState, "unauthorized");
    assert.equal(result.result.value.live, false);
    await send("Browser.close");
    socket.close();
    console.log(JSON.stringify({ result: "PASS", states: result.result.value.states, endpoint: result.result.value.endpoint, boundedRetry: result.result.value.closed }));
  } finally {
    await stop(server);
    await stop(chrome);
    await fs.promises.rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 50 });
  }
}

qualify().catch((error) => { console.error(error); process.exitCode = 1; });
