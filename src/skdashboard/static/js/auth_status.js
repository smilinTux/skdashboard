(function () {
  "use strict";

  var bar = document.createElement("aside");
  bar.setAttribute("aria-label", "Authentication status");
  bar.style.cssText = "position:fixed;right:16px;top:12px;z-index:10000;display:flex;align-items:center;gap:10px;padding:9px 12px;border:1px solid #526089;border-radius:8px;background:#11182d;color:#e7ecff;font:14px system-ui;box-shadow:0 3px 16px #0008";
  bar.innerHTML = '<strong>CapAuth</strong><span id="sk-auth-state">Checking session...</span><a id="sk-auth-login" href="/auth/login" style="color:#9ec5ff">Login</a><button id="sk-auth-logout" type="button" hidden style="padding:5px 9px;border:1px solid #526089;border-radius:5px;background:#1b2748;color:#fff;cursor:pointer">Logout</button>';
  document.body.appendChild(bar);

  var state = document.getElementById("sk-auth-state");
  var login = document.getElementById("sk-auth-login");
  var logout = document.getElementById("sk-auth-logout");
  var returnTo = window.location.pathname + window.location.search;
  login.href = "/auth/login?return_to=" + encodeURIComponent(returnTo);

  fetch("/auth/session", { headers: { accept: "application/json" } })
    .then(function (response) {
      if (!response.ok) throw new Error("signed-out");
      return response.json();
    })
    .then(function (session) {
      var identity = session.subject || "identity unavailable, sign in again";
      state.textContent = "Signed in as " + identity;
      login.hidden = true;
      logout.hidden = false;
      logout.addEventListener("click", function () {
        logout.disabled = true;
        fetch("/auth/logout", {
          method: "POST",
          headers: { "X-CSRF-Token": session.csrf_token }
        }).then(function (response) {
          if (!response.ok) throw new Error("logout-failed");
          window.location.reload();
        }).catch(function () {
          state.textContent = "Logout failed. Session unchanged.";
          logout.disabled = false;
        });
      });
    })
    .catch(function () {
      state.textContent = "Signed out";
      login.hidden = false;
      logout.hidden = true;
    });
}());
