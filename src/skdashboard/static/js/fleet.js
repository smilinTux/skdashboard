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
    const [drift, gateway] = await Promise.allSettled([
      getJSON("/api/fleet/drift"),
      getJSON("/api/v1/gateway/summary"),
    ]);
    if (drift.status !== "fulfilled") throw drift.reason;
    d = drift.value;
    renderGateway(gateway.status === "fulfilled" ? gateway.value : null, gateway.reason);
  } catch (e) {
    document.getElementById("fl-nodes").innerHTML = `<div class="emptymsg">${esc(e.message)}</div>`;
    return;
  }
  renderErrors(d.errors || []);
  renderKPI(d.summary || {});
  renderNodes(d.nodes || []);
  renderSkipped(d.skipped || []);
}

function text(value) {
  if (value == null || value === "") return "Unknown";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function age(value) {
  if (value == null) return "Unknown";
  const seconds = Math.max(0, Number(value));
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function renderGateway(snapshot, error) {
  const body = document.getElementById("fl-gateway");
  const summary = document.getElementById("fl-gateway-summary");
  if (!snapshot) {
    summary.textContent = "Gateway totals unavailable";
    body.innerHTML = `<div class="emptymsg">Protected gateway observations are unavailable${error ? `: ${esc(error.message)}` : "."} No node is assumed healthy.</div>`;
    return;
  }
  const nodes = snapshot.nodes || [];
  const totals = snapshot.node_totals || {};
  summary.textContent = `${text(totals.named)} named | ${text(totals.current)} current | ${text(totals.stale)} stale | ${text(totals.missing)} missing`;
  if (!nodes.length) {
    body.innerHTML = `<div class="emptymsg">No named gateway node is present in the protected snapshot. Snapshot state: ${esc(text(snapshot.state))}.</div>`;
    return;
  }
  body.innerHTML = `<div class="fl-table-wrap"><table class="fl-gateway-table">
    <caption>Per-node gateway telemetry freshness and version truth</caption>
    <thead><tr><th scope="col">Node</th><th scope="col">Telemetry</th><th scope="col">Last observation</th><th scope="col">Backend and model</th><th scope="col">Profile</th><th scope="col">Runtime</th><th scope="col">Config drift</th></tr></thead>
    <tbody>${nodes.map((node) => `<tr>
      <th scope="row">${esc(node.node_id)}</th>
      <td><span class="fl-sev gateway-${esc(node.telemetry_state)}">${esc(node.telemetry_state)}</span><small>${esc(age(node.age_seconds))} / TTL ${esc(age(node.ttl_seconds))}</small></td>
      <td>${esc(text(node.observed_at))}</td>
      <td>${esc(text(node.backend))}<small>${esc(text(node.served_model))}</small></td>
      <td>${esc(text(node.transport_profile))}<small>version ${esc(text(node.version))}</small></td>
      <td class="mono">${esc(text(node.runtime_revision))}</td>
      <td>${esc(text(node.configuration_drift))}</td>
    </tr>`).join("")}</tbody>
  </table></div>`;
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
