(function () {
  "use strict";

  var account = document.createElement("aside");
  account.setAttribute("aria-label", "Account");
  account.style.cssText = "position:fixed;right:12px;top:10px;z-index:10000;font:13px system-ui;color:#e7ecff";
  account.innerHTML = '<details id="sk-auth-menu"><summary style="display:flex;align-items:center;gap:7px;min-height:32px;padding:2px 9px;border:1px solid #526089;border-radius:999px;background:#11182d;box-shadow:0 2px 10px #0006;cursor:pointer;list-style:none"><span aria-hidden="true" style="width:8px;height:8px;border-radius:50%;background:#8b96b8"></span><span id="sk-auth-summary">CapAuth</span></summary><div style="position:absolute;right:0;margin-top:6px;width:min(320px,calc(100vw - 24px));padding:12px;border:1px solid #526089;border-radius:10px;background:#11182d;box-shadow:0 8px 28px #000b"><strong>CapAuth account</strong><p id="sk-auth-state" style="margin:8px 0;color:#aab5d6;overflow-wrap:anywhere">Checking session...</p><a id="sk-auth-login" href="/auth/login" style="display:inline-block;color:#9ec5ff">Login with CapAuth</a><button id="sk-auth-logout" type="button" hidden style="padding:6px 10px;border:1px solid #526089;border-radius:6px;background:#1b2748;color:#fff;cursor:pointer">Logout</button></div></details>';
  document.body.appendChild(account);

  var menu = document.getElementById("sk-auth-menu");
  var summary = document.getElementById("sk-auth-summary");
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
      var compact = identity.length > 16 ? identity.slice(0, 8) + "..." : identity;
      summary.textContent = compact;
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
      summary.textContent = "Sign in";
      state.textContent = "Signed out";
      login.hidden = false;
      logout.hidden = true;
    });

  document.addEventListener("click", function (event) {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
}());
