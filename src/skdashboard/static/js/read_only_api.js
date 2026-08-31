export function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export async function getJSON(url) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (response.status === 401) void renderSignInAction();
  if (!response.ok) throw new Error(`${url} -> ${response.status}`);
  return response.json();
}

let sessionCheck;

export async function renderSignInAction() {
  if (
    typeof document === "undefined" ||
    typeof location === "undefined" ||
    !location.pathname.startsWith("/control-plane/") ||
    document.getElementById("session-sign-in")
  ) return;
  sessionCheck ||= fetch("/auth/session", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  }).then((response) => response.status === 401).catch(() => false);
  if (!(await sessionCheck) || document.getElementById("session-sign-in")) return;
  const navigation = document.querySelector(".topbar, .sidebar");
  if (!navigation) return;
  const link = document.createElement("a");
  link.id = "session-sign-in";
  link.className = "build-badge mono";
  link.href = `/auth/login?${new URLSearchParams({
    return_to: `${location.pathname}${location.search}`,
  })}`;
  link.textContent = "Sign in";
  link.setAttribute("aria-label", "Sign in to view protected control-plane data");
  attachBuildBadge(navigation, link);
}

export function avatarColor(name) {
  if (!name) return "var(--med)";
  let hash = 0;
  for (let index = 0; index < name.length; index += 1) {
    hash = (hash * 31 + name.charCodeAt(index)) & 0xffff;
  }
  return `hsl(${hash % 360} 45% 45%)`;
}

export function timeShort(timestamp) {
  if (!timestamp) return "";
  try {
    return new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch (_error) {
    return "";
  }
}

export function attachBuildBadge(navigation, badge) {
  const live = navigation.querySelector(".live");
  if (live) live.before(badge);
  else navigation.append(badge);
}

export async function renderBuildBadge() {
  const navigation = document.querySelector(".topbar, .sidebar");
  if (!navigation) return;
  const badge = document.createElement("span");
  badge.id = "build-version";
  badge.className = "build-badge mono";
  badge.setAttribute("role", "status");
  badge.textContent = "Version unavailable";
  attachBuildBadge(navigation, badge);
  try {
    const info = await getJSON("/api/v1/build-info");
    const fields = [info.package_version, info.source_commit, info.release_identifier];
    if (
      info.schema_version !== "skdashboard.build-info/v1" ||
      info.application !== "SKDashboard" ||
      fields.some((value) => typeof value !== "string" || !value || value === "unavailable")
    ) return;
    badge.textContent = `${info.application} ${info.package_version} | ${info.source_commit} | ${info.release_identifier}`;
    badge.setAttribute("aria-label", `Deployed ${badge.textContent}`);
  } catch (_error) {
    // The initialized fallback is deliberately honest when runtime metadata is unavailable.
  }
}

if (typeof document !== "undefined") void renderBuildBadge();
