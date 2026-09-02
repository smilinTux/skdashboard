/* Fleet chat: a channel view over the skmail store.
 *
 * Design notes, because the shape is deliberate:
 *  - A CHANNEL is a recipient. #lumina is everything addressed to lumina.
 *    skmail already carries from/to/ts/re/priority, so no schema change was
 *    needed to render it as IRC; this is a projection, not a new store.
 *  - A worker identity encodes its lane and card (pi-qwen-chiap03-33375183),
 *    so each line can show WHO spoke, on WHICH lane, about WHICH card without
 *    any extra lookup.
 *  - Urgent is colour AND a marker, never colour alone.
 */
(function () {
  "use strict";
  var state = { messages: [], channel: null, filter: "", urgentOnly: false };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function clock(ts) {
    var t = String(ts || "");
    var d = t.slice(0, 10), h = t.slice(11, 19);
    var today = new Date().toISOString().slice(0, 10);
    return d === today ? h : (d.slice(5) + " " + h.slice(0, 5));
  }
  function laneOf(m) { return m.lane ? m.lane : (m.kind === "agent" ? "agent" : "sys"); }

  function visible() {
    var q = state.filter.trim().toLowerCase();
    return state.messages.filter(function (m) {
      if (state.urgentOnly && m.priority !== "urgent") return false;
      if (state.channel && (m.to || []).indexOf(state.channel) === -1) return false;
      if (!q) return true;
      return (m.from + " " + m.subject + " " + m.body + " " + (m.card || ""))
        .toLowerCase().indexOf(q) !== -1;
    });
  }

  function renderRail() {
    var ch = document.getElementById("fc-channels");
    var chans = {};
    state.messages.forEach(function (m) {
      (m.to.length ? m.to : ["unaddressed"]).forEach(function (t) {
        chans[t] = chans[t] || { n: 0, u: 0 };
        chans[t].n += 1;
        if (m.priority === "urgent") chans[t].u += 1;
      });
    });
    var names = Object.keys(chans).sort(function (a, b) { return chans[b].n - chans[a].n; });
    ch.innerHTML = ['<li><button class="fc-chan' + (state.channel ? "" : " on") +
      '" data-ch="">#all <span class="fc-n">' + state.messages.length + "</span></button></li>"]
      .concat(names.map(function (n) {
        return '<li><button class="fc-chan' + (state.channel === n ? " on" : "") +
          '" data-ch="' + esc(n) + '">#' + esc(n) +
          ' <span class="fc-n">' + chans[n].n + "</span>" +
          (chans[n].u ? ' <span class="fc-u">' + chans[n].u + "</span>" : "") + "</button></li>";
      })).join("");
    Array.prototype.forEach.call(ch.querySelectorAll(".fc-chan"), function (b) {
      b.addEventListener("click", function () {
        state.channel = b.getAttribute("data-ch") || null;
        renderRail(); renderLog();
      });
    });

    var sp = document.getElementById("fc-speakers");
    var who = {};
    state.messages.forEach(function (m) { who[m.from] = (who[m.from] || 0) + 1; });
    sp.innerHTML = Object.keys(who).sort(function (a, b) { return who[b] - who[a]; })
      .slice(0, 12).map(function (n) {
        return '<li><button class="fc-sp" data-sp="' + esc(n) + '">' +
          esc(n.length > 26 ? n.slice(0, 25) + "…" : n) +
          ' <span class="fc-n">' + who[n] + "</span></button></li>";
      }).join("");
    Array.prototype.forEach.call(sp.querySelectorAll(".fc-sp"), function (b) {
      b.addEventListener("click", function () {
        var f = document.getElementById("fc-filter");
        f.value = b.getAttribute("data-sp"); state.filter = f.value;
        renderLog();
      });
    });
  }

  function renderLog() {
    var rows = visible();
    var log = document.getElementById("fc-log");
    document.getElementById("fc-count").textContent =
      rows.length + " of " + state.messages.length;
    document.getElementById("fc-empty").hidden = rows.length !== 0;
    log.innerHTML = rows.map(function (m) {
      var to = (m.to || []).map(function (t) { return "@" + t; }).join(" ");
      return '<li class="fc-row fc-' + esc(m.priority) + '">' +
        '<span class="fc-ts">' + esc(clock(m.ts)) + "</span>" +
        '<span class="fc-lane fc-lane-' + esc(laneOf(m)) + '">' + esc(laneOf(m)) + "</span>" +
        '<span class="fc-from">' + esc(m.from) + "</span>" +
        '<span class="fc-to">' + esc(to) + "</span>" +
        (m.card ? '<span class="fc-card">' + esc(m.card) + "</span>" : "") +
        '<span class="fc-sub">' + esc(m.subject) + "</span>" +
        (m.body ? '<div class="fc-body">' + esc(m.body.slice(0, 600)) + "</div>" : "") +
        "</li>";
    }).join("");
    log.scrollTop = log.scrollHeight;
  }

  function load() {
    fetch("/api/v1/fleet-chat", { headers: { accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) {
        state.messages = d.messages || [];
        renderRail(); renderLog();
      })
      .catch(function (e) {
        document.getElementById("fc-log").innerHTML =
          '<li class="fc-row fc-urgent"><span class="fc-sub">Fleet chat unavailable: ' +
          esc(e.message) + "</span></li>";
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var f = document.getElementById("fc-filter");
    if (f) f.addEventListener("input", function () { state.filter = f.value; renderLog(); });
    var u = document.getElementById("fc-urgent");
    if (u) u.addEventListener("change", function () { state.urgentOnly = u.checked; renderLog(); });
    load();
    setInterval(load, 20000);
  });
})();
