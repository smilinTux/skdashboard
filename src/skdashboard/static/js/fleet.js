// Fleet Drift view: install-profile drift per node (epic 3bbf39ea, card d1c6d605).
//
// The three grades stay three grades all the way to the pixels. Collapsing
// them into one "drifted" badge would put a manifest that has not caught up
// (info) next to a node running something it was told not to run (error), and
// the operator would learn to ignore both.
import { esc, getJSON, toast } from "./api.js";

const GRADES = ["error", "warn", "info"];
const GRADE_LABEL = { error: "Forbidden", warn: "Missing required", info: "Unexpected" };
// Why each skip happened, in the operator's language. A skipped node is an
// explicit state, never rendered as clean and never as broken.
const SKIP_LABEL = {
  no_role: "no role bound",
  no_profile: "role has no valid profile",
  no_inventory: "no inventory published",
  ungraded: "not graded",
};

async function load() {
  let d;
  try {
    d = await getJSON("/api/v1/fleet/drift");
  } catch (e) {
    document.getElementById("fl-nodes").innerHTML = `<div class="emptymsg">${esc(e.message)}</div>`;
    return;
  }
  renderErrors(d.errors || []);
  renderProvenance(d.provenance || {});
  renderKPI(d.summary || {});
  renderNodes(d.nodes || []);
  renderSkipped(d.skipped || []);
  renderWorkers(d.worker_runtime || {});
  renderInference(d.inference_runtime || {});
}

function renderProvenance(source) {
  document.getElementById("fl-provenance").textContent =
    `${source.owner || "SKCapstone Fleet"} · ${source.population || "published node inventory"} · ${source.truth_state || "unknown"} · ${source.coverage?.graded ?? 0} of ${source.coverage?.known ?? 0} graded`;
}

function renderWorkers(runtime) {
  const workers = runtime.workers || [];
  const summary = runtime.summary || {};
  const tile = (label, value) => `<div class="kpi"><div class="l">${esc(label)}</div><div class="n">${esc(value ?? 0)}</div></div>`;
  document.getElementById("fl-runtime-kpi").innerHTML =
    tile("Running", summary.running) + tile("Recently stale", summary.stale) +
    tile("Nodes reporting", `${summary.reporting_hosts ?? 0} / ${summary.known_hosts ?? 0}`) +
    tile("Old beats excluded", summary.omitted_old_beats);
  document.getElementById("fl-runtime-nodes").innerHTML = (runtime.nodes || []).map((node) =>
    `<div class="fl-stat-row"><strong>${esc(node.host)}</strong><span>${esc(node.running)} running · ${esc(node.stale)} stale</span><span>${esc(node.lanes.join(", ") || "idle")}</span><span>${node.latest_age_seconds == null ? "no recent beat" : `${esc(node.latest_age_seconds)}s ago`}</span></div>`
  ).join("") || `<div class="emptymsg">No fleet nodes configured.</div>`;
  document.getElementById("fl-runtime-lanes").innerHTML = (runtime.lanes || []).map((lane) =>
    `<div class="fl-stat-row"><strong>${esc(lane.lane)}</strong><span>${esc(lane.running)} running</span><span>${esc(lane.stale)} stale</span></div>`
  ).join("") || `<div class="emptymsg">No active lanes.</div>`;
  document.getElementById("fl-workers").innerHTML = workers.length
    ? workers.map((w) => `<div class="fl-node sev-${w.truth_state === "current" ? "ok" : "skip"}">
      <div class="fl-head"><span class="fl-name">${esc(w.name)}</span><span class="fl-role">${esc(w.host || "unknown host")}</span>
      <span class="fl-sev g-${w.truth_state === "current" ? "ok" : "skip"}">${esc(w.truth_state.toUpperCase())}</span></div>
      <div class="fl-reason">${esc(w.task_id)} · ${esc(w.task_title || "title unavailable")} · lane ${esc(w.lane)} · heartbeat ${esc(w.age_seconds)}s old · elapsed ${w.elapsed_seconds == null ? "unknown" : `${esc(w.elapsed_seconds)}s`}</div>
    </div>`).join("")
    : `<div class="emptymsg">No claimed fleet worker has reported.</div>`;
}

function renderInference(runtime) {
  const sources = runtime.sources || [];
  document.getElementById("fl-inference").innerHTML = sources.length
    ? sources.map((source) => {
      const summary = source.summary || {};
      const models = Array.isArray(source.models) ? source.models : Object.keys(source.models || {});
      const backends = Object.keys(source.backends || {});
      return `<div class="fl-node sev-ok"><div class="fl-head"><span class="fl-name">${esc(source.source)}</span>
        <span class="fl-role">${esc(source.truth_state || "unknown")}</span></div>
        <div class="fl-reason">models ${esc(models.join(", ") || "not attributed")} · backends ${esc(backends.join(", ") || "not attributed")} · running ${esc(summary.running ?? summary.activeRequests ?? 0)} · queued ${esc(summary.queued ?? 0)}</div></div>`;
    }).join("")
    : `<div class="emptymsg">Inference telemetry unavailable.</div>`;
}

function renderErrors(errors) {
  const el = document.getElementById("fl-errors");
  el.hidden = !errors.length;
  el.textContent = errors.join(" · ");
}

function renderKPI(s) {
  const tile = (label, n, cls) =>
    `<div class="kpi${n && cls ? " alert" : ""}"><div class="l">${label}</div>` +
    `<div class="n${cls ? " " + cls : ""}">${n || 0}</div></div>`;
  document.getElementById("fl-kpi").innerHTML =
    tile("Graded", s.graded) +
    tile("Forbidden", s.error, "g-error") +
    tile("Missing", s.warn, "g-warn") +
    tile("Unexpected", s.info, "g-info") +
    tile("Clean", s.ok, "g-ok") +
    tile("Not graded", s.skipped, "g-skip");
}

function renderNodes(nodes) {
  const body = document.getElementById("fl-nodes");
  if (!nodes.length) {
    body.innerHTML = `<div class="emptymsg">No node has both a role and a published inventory yet.</div>`;
    return;
  }
  body.innerHTML = nodes.map(nodeCard).join("");
}

function nodeCard(n) {
  const counts = n.counts || {};
  const chips = GRADES.filter((g) => counts[g])
    .map((g) => `<span class="fl-chip g-${g}">${counts[g]} ${esc(GRADE_LABEL[g].toLowerCase())}</span>`)
    .join("");
  const groups = GRADES.map((g) => {
    const rows = (n.findings || []).filter((f) => f.grade === g);
    if (!rows.length) return "";
    return `<div class="fl-group">
      <div class="fl-gh g-${g}">${esc(GRADE_LABEL[g])} <span class="fl-gc">${rows.length}</span></div>
      ${rows.map((f) => `<div class="fl-find"><span class="fl-cat">${esc(f.category)}</span><span class="mono">${esc(f.name)}</span></div>`).join("")}
    </div>`;
  }).join("");
  const clean = n.severity === "ok";
  return `<div class="fl-node sev-${esc(n.severity)}">
    <div class="fl-head">
      <span class="fl-name">${esc(n.node)}</span>
      <span class="fl-role">role ${esc(n.role || "?")}</span>
      <span class="fl-sev g-${esc(n.severity)}">${esc(n.severity.toUpperCase())}</span>
      <span class="fl-chips">${chips}</span>
    </div>
    ${clean ? `<div class="fl-clean">matches its profile</div>` : groups}
  </div>`;
}

function renderSkipped(skipped) {
  const body = document.getElementById("fl-skipped");
  if (!skipped.length) {
    body.innerHTML = `<div class="emptymsg">Every known node was graded.</div>`;
    return;
  }
  body.innerHTML = skipped
    .map(
      (s) => `<div class="fl-node sev-skip">
      <div class="fl-head">
        <span class="fl-name">${esc(s.node)}</span>
        <span class="fl-role">role ${esc(s.role || "none")}</span>
        <span class="fl-sev g-skip">SKIPPED</span>
        <span class="fl-chips"><span class="fl-chip g-skip">${esc(SKIP_LABEL[s.reason_code] || s.reason_code)}</span></span>
      </div>
      <div class="fl-reason">${esc(s.reason)}</div>
    </div>`
    )
    .join("");
}

document.getElementById("btn-refresh").addEventListener("click", () => {
  load().then(() => toast("fleet drift refreshed"));
});

load();
