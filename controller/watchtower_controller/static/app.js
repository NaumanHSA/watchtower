// Watchtower dashboard. Plain ES module: no build step, no third-party code, works air-gapped.
// User-controlled data is only ever inserted with textContent. Static SVG markup (icons, art)
// is parsed with DOMParser from constants in this file.

const RANK = { viewer: 1, operator: 2, admin: 3 };
const TONE = {
  running: "ok", online: "ok",
  starting: "info", pending: "info",
  reconnecting: "warn", suspect: "warn",
  error: "bad", failed: "bad", offline: "bad",
  stopped: "idle",
};
const ISSUE = new Set(["error", "failed", "reconnecting"]);

const state = {
  token: null, me: null, streams: [], workers: [], stats: null, events: [], audit: [],
  route: null, ws: null, viewPc: null, livePcs: new Map(), filter: { q: "", status: "all" },
};
const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------------ icons (static markup)

const ICONS = {
  overview: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  camera: '<rect x="2" y="7" width="13" height="10" rx="2"/><path d="M15 11l6-3.5v9L15 13z"/>',
  live: '<circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19 5a10 10 0 0 1 0 14M5 19A10 10 0 0 1 5 5"/>',
  workers: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01M11 7.5h6M11 16.5h6"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  audit: '<path d="M9 4h6v3H9z"/><path d="M15 5h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h3"/><path d="M9 14l2 2 4-4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  play: '<path d="M8 5.5v13l11-6.5z" fill="currentColor"/>',
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M14 6l4 4"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  logout: '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="M10 17l-5-5 5-5M5 12h11"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  zap: '<path d="M13 3L4 14h7l-1 7 9-11h-7z"/>',
  gauge: '<path d="M12 14l4-4"/><path d="M3.5 18a9 9 0 1 1 17 0"/>',
  shield: '<path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
  drain: '<path d="M12 4v10M8 10l4 4 4-4"/><path d="M4 19h16"/>',
  undrain: '<path d="M12 18V8M8 12l4-4 4 4"/><path d="M4 4h16"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
};

const HERO_ART = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 170" fill="none">
  <defs><linearGradient id="beam" x1="0" x2="1"><stop offset="0" stop-color="#2dd4bf" stop-opacity=".55"/><stop offset="1" stop-color="#2dd4bf" stop-opacity="0"/></linearGradient></defs>
  <g stroke="#0d9488" stroke-opacity=".25" stroke-width="1.5"><circle cx="70" cy="62" r="34"/><circle cx="70" cy="62" r="54"/></g>
  <path d="M84 50 L200 18 L200 82 Z" fill="url(#beam)"/>
  <path d="M48 48 L70 34 L92 48 Z" fill="#0b1b34"/><rect x="52" y="50" width="36" height="14" rx="3" fill="#0b1b34"/>
  <rect x="58" y="54" width="24" height="6" rx="2" fill="#2dd4bf"/>
  <g stroke="#0b1b34" stroke-width="4" stroke-linecap="round"><path d="M58 66 L50 112"/><path d="M82 66 L90 112"/><path d="M56 82 L84 82"/><path d="M58 82 L88 104"/></g>
  <g stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="3 4"><path d="M100 120 C130 120 140 140 168 140"/><path d="M100 120 C135 116 150 100 180 96"/><path d="M100 120 C130 128 160 160 200 156"/></g>
  <g><rect x="166" y="130" width="34" height="20" rx="5" fill="#fff" stroke="#cbd5e1"/><circle cx="179" cy="140" r="5" fill="#2563eb"/>
  <rect x="178" y="86" width="34" height="20" rx="5" fill="#fff" stroke="#cbd5e1"/><circle cx="191" cy="96" r="5" fill="#0d9488"/>
  <rect x="198" y="146" width="34" height="20" rx="5" fill="#fff" stroke="#cbd5e1"/><circle cx="211" cy="156" r="5" fill="#f97316"/></g>
  <rect x="86" y="112" width="28" height="18" rx="5" fill="#0b1b34"/><path d="M93 121h14" stroke="#2dd4bf" stroke-width="2.5" stroke-linecap="round"/>
</svg>`;

const parser = new DOMParser();
function svgFrom(markup) {
  return document.importNode(parser.parseFromString(markup, "image/svg+xml").documentElement, true);
}
function icon(name) {
  return svgFrom(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`);
}

// ------------------------------------------------------------------ DOM helpers

function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key === "style") el.style.cssText = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const pill = (value) => h("span", { class: `pill ${TONE[value] || "idle"}`, text: value });
const bar = (fraction, tone = "") =>
  h("div", { class: `bar ${tone}` }, h("span", { style: `width:${Math.round(Math.min(1, Math.max(0, fraction)) * 100)}%` }));
const iconButton = (name, label, onclick, cls = "") =>
  h("button", { class: `icon-btn ${cls}`, title: label, "aria-label": label, onclick }, icon(name));
const button = (label, onclick, cls = "btn-ghost", iconName = null) =>
  h("button", { class: `btn ${cls}`, onclick }, iconName ? icon(iconName) : null, label);
const can = (role) => state.me && RANK[state.me.role] >= RANK[role];
const workerName = (id) => (state.workers.find((w) => w.worker_id === id) || {}).name || id;
const streamName = (id) => (state.streams.find((s) => s.stream_id === id) || {}).name;

function ago(iso) {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 10) return "just now";
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
const fmt = (n) => (n ?? 0).toLocaleString();

function toast(message, bad = false) {
  const el = h("div", { class: `toast${bad ? " bad" : ""}`, role: bad ? "alert" : "status" },
    icon(bad ? "alert" : "check"), h("div", { text: message }));
  $("toasts").append(el);
  setTimeout(() => el.remove(), bad ? 8000 : 4000);
}

function emptyState(iconName, title, text, action = null) {
  return h("div", { class: "empty" }, icon(iconName), h("strong", { text: title }), h("span", { text }), action);
}

function confirmAction(title, text, okLabel = "Confirm") {
  $("confirm-title").textContent = title;
  $("confirm-text").textContent = text;
  $("confirm-ok").textContent = okLabel;
  const dialog = $("confirm-dialog");
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true }));
}

// ------------------------------------------------------------------ API

async function api(path, { method = "GET", body, headers = {} } = {}) {
  const res = await fetch(`/api/v1${path}`, {
    method,
    headers: { Authorization: `Bearer ${state.token}`, ...(body ? { "Content-Type": "application/json" } : {}), ...headers },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) { signOut("Your session expired or the token was revoked."); throw new Error("unauthorized"); }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail
        : Array.isArray(data.detail) ? data.detail.map((d) => `${(d.loc || []).slice(-1)[0]}: ${d.msg}`).join("; ")
        : detail;
    } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

async function run(action, success) {
  try {
    await action();
    if (success) toast(success);
    scheduleRefresh();
  } catch (e) {
    if (e.message !== "unauthorized") toast(e.message, true);
  }
}

// ------------------------------------------------------------------ session

function storedToken() {
  try { return sessionStorage.getItem("wt-token") || localStorage.getItem("wt-token"); } catch { return null; }
}

function saveToken(token, remember) {
  try {
    sessionStorage.setItem("wt-token", token);
    if (remember) localStorage.setItem("wt-token", token); else localStorage.removeItem("wt-token");
  } catch { /* storage unavailable: the token lives in memory only */ }
}

async function signIn(token) {
  state.token = token;
  state.me = await api("/me");
  $("login").hidden = true;
  $("app").hidden = false;
  $("user-name").textContent = state.me.subject;
  $("user-role").textContent = state.me.role;
  $("avatar").textContent = initials(state.me.subject);
  connectEvents();
  await refresh();
  renderPage();
}

function initials(name) {
  const parts = String(name).replace(/[^A-Za-z0-9 ._@-]/g, "").split(/[ ._@-]+/).filter(Boolean);
  return ((parts[0] || "?")[0] + (parts[1] ? parts[1][0] : "")).toUpperCase();
}

function signOut(message) {
  try {
    sessionStorage.removeItem("wt-token"); localStorage.removeItem("wt-token"); sessionStorage.removeItem("wt-sso");
  } catch { /* ignore */ }
  clearTimeout(ssoTimer);
  stopAllLive();
  state.token = null;
  state.me = null;
  if (state.ws) { state.ws.onclose = null; state.ws.close(); state.ws = null; }
  $("app").hidden = true;
  $("login").hidden = false;
  $("login-error").textContent = message || "";
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const token = $("login-token").value.trim();
  if (!token) { $("login-error").textContent = "Paste a token, or use single sign-on."; return; }
  $("login-error").textContent = "";
  try {
    await signIn(token);
    saveToken(token, $("login-remember").checked);
    $("login-token").value = "";
  } catch (e) {
    $("login-error").textContent = e.message === "unauthorized" ? "That token was not accepted." : e.message;
  }
});

// ------------------------------------------------------------------ data

let refreshTimer = null;
function scheduleRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => { await refresh(); updatePage(); }, 300);
}

async function refresh() {
  if (!state.token) return;
  try {
    const [stats, streams, workers] = await Promise.all([api("/stats"), api("/streams"), api("/workers")]);
    Object.assign(state, { stats, streams, workers });
    if (state.route === "audit" && can("admin")) state.audit = await api("/audit?limit=200");
    renderNav();
  } catch (e) {
    if (e.message !== "unauthorized") toast(`Refresh failed: ${e.message}`, true);
  }
}

setInterval(async () => { if (state.token) { await refresh(); updatePage(); } }, 15000);

// ------------------------------------------------------------------ routing + shell

const PAGES = [
  { id: "overview", label: "Overview", icon: "overview", title: "Overview", sub: "Your camera fleet at a glance" },
  { id: "cameras", label: "Cameras", icon: "camera", title: "Cameras", sub: "Every camera Watchtower analyses" },
  { id: "live", label: "Live view", icon: "live", title: "Live view", sub: "Watch running cameras in real time" },
  { id: "workers", label: "Workers", icon: "workers", title: "Workers", sub: "The machines that run the analysis" },
  { id: "activity", label: "Activity", icon: "activity", title: "Activity", sub: "Live events since you opened the dashboard" },
  { id: "audit", label: "Audit log", icon: "audit", title: "Audit log", sub: "Who changed what, and when", role: "admin" },
];

function route() {
  const id = location.hash.replace(/^#\/?/, "") || "overview";
  const page = PAGES.find((p) => p.id === id && (!p.role || can(p.role)));
  return page ? page.id : "overview";
}

function renderNav() {
  const issues = state.streams.filter((s) => ISSUE.has(s.status)).length;
  const online = state.workers.filter((w) => w.state === "online").length;
  const counts = { cameras: issues ? [issues, "bad"] : [state.streams.length, ""], workers: [online, ""] };
  const link = (p) => h("a", { class: `nav-item${state.route === p.id ? " active" : ""}`, href: `#/${p.id}` },
    icon(p.icon), h("span", { text: p.label }),
    counts[p.id] ? h("span", { class: `count ${counts[p.id][1]}`, text: counts[p.id][0] }) : null);
  const visible = PAGES.filter((p) => !p.role || can(p.role));
  $("nav").replaceChildren(
    h("div", { class: "nav-label", text: "Monitor" }), ...visible.filter((p) => ["overview", "live", "activity"].includes(p.id)).map(link),
    h("div", { class: "nav-label", text: "Manage" }), ...visible.filter((p) => ["cameras", "workers", "audit"].includes(p.id)).map(link),
  );
}

function renderPage() {
  if (!state.token) return;
  const id = route();
  if (state.route === "live" && id !== "live") stopAllLive();
  state.route = id;
  const page = PAGES.find((p) => p.id === id);
  $("page-title").textContent = page.title;
  $("page-subtitle").textContent = page.sub;
  document.title = `${page.title} · Watchtower`;
  renderNav();
  const actions = [];
  if (id === "cameras" && can("operator")) actions.push(button("Add camera", () => openStreamDialog(), "btn-primary", "plus"));
  if (id === "live") actions.push(button("Play all", playAllLive, "btn-ghost", "play"), button("Stop all", stopAllLive, "btn-ghost", "stop"));
  $("top-actions").replaceChildren(...actions);
  const view = $("view");
  view.replaceChildren();
  VIEWS[id].mount(view);
  if (id === "audit") refresh().then(updatePage);
}

function updatePage() {
  if (!state.token || !state.route) return;
  VIEWS[state.route].update();
}

window.addEventListener("hashchange", () => { closeSidebar(); renderPage(); });
$("menu-btn").append(icon("menu"));
$("logout").append(icon("logout"));
$("logout").addEventListener("click", () => signOut());
$("menu-btn").addEventListener("click", () => { $("sidebar").classList.add("open"); $("scrim").hidden = false; });
$("scrim").addEventListener("click", closeSidebar);
function closeSidebar() { $("sidebar").classList.remove("open"); $("scrim").hidden = true; }
window.addEventListener("scroll", () => document.querySelector(".topbar").classList.toggle("scrolled", window.scrollY > 4));
for (const b of document.querySelectorAll("[data-close]")) {
  if (b.classList.contains("icon-btn")) b.append(icon("close"));
  b.addEventListener("click", () => b.closest("dialog").close());
}

// ------------------------------------------------------------------ views

const VIEWS = {};

function rebuildOn(build) {
  let root = null;
  return {
    mount(view) { root = h("div", { style: "display:grid;gap:22px" }); view.append(root); root.replaceChildren(...build()); },
    update() { if (root) root.replaceChildren(...build()); },
  };
}

// ---- overview

VIEWS.overview = rebuildOn(() => {
  const s = state.stats || { workers: {}, streams: {}, capacity: { used: 0, online: 0 } };
  const total = state.streams.length;
  const running = s.streams.running || 0;
  const issues = state.streams.filter((x) => ISSUE.has(x.status));
  const workersTotal = state.workers.length;
  const online = s.workers.online || 0;
  const draining = state.workers.filter((w) => w.draining).length;
  const delivered = state.workers.reduce((a, w) => a + (w.delivery?.sent || 0), 0);
  const dropped = state.workers.reduce((a, w) => a + (w.delivery?.dropped || 0), 0);
  const failed = state.workers.reduce((a, w) => a + (w.delivery?.failed || 0), 0);

  const hero = h("section", { class: "hero" },
    h("div", {},
      h("h2", {}, "Watchtower ", h("span", { text: "keeps an eye on every camera" })),
      h("p", { text: "Add cameras and Watchtower places each one on a worker, detects and tracks faces in real time, delivers every result to your application, and moves cameras to healthy workers when something fails." }),
      h("div", { class: "chips" },
        h("span", { class: "chip" }, icon("globe"), "Workers anywhere, pull-based"),
        h("span", { class: "chip" }, icon("shield"), "Self-healing fleet"),
        h("span", { class: "chip" }, icon("lock"), "Encrypted results"),
        h("span", { class: "chip" }, icon("live"), "Live WebRTC view")),
      h("div", { class: "hero-actions" },
        can("operator") ? button("Add camera", () => openStreamDialog(), "btn-primary", "plus") : null,
        button("Open live view", () => { location.hash = "#/live"; }, "btn-ghost", "live"))),
    h("div", { class: "hero-art", "aria-hidden": "true" }, svgFrom(HERO_ART)));

  const kpi = (iconName, tone, label, value, sub, extra = null) => h("div", { class: "card kpi" },
    h("div", { class: "kpi-top" }, h("div", { class: `kpi-icon ${tone}` }, icon(iconName)), label),
    h("div", { class: "kpi-value" }, ...value), extra, h("div", { class: "kpi-sub", text: sub }));
  const capacity = s.capacity || { used: 0, online: 0 };
  const kpis = h("section", { class: "kpis" },
    kpi("camera", "teal", "Cameras running", [String(running), h("small", { text: ` / ${total}` })],
      issues.length ? `${issues.length} ${issues.length === 1 ? "needs" : "need"} attention` : "All cameras healthy"),
    kpi("workers", "blue", "Workers online", [String(online), h("small", { text: ` / ${workersTotal}` })],
      draining ? `${draining} draining` : "Ready for new cameras"),
    kpi("gauge", "navy", "Capacity used", [String(capacity.used), h("small", { text: ` / ${capacity.online} slots` })],
      capacity.online ? `${Math.round((capacity.used / capacity.online) * 100)}% of online capacity` : "No online capacity",
      bar(capacity.online ? capacity.used / capacity.online : 0, capacity.online && capacity.used / capacity.online > 0.85 ? "warn" : "")),
    kpi("zap", "orange", "Results delivered", [fmt(delivered)],
      `${fmt(dropped)} dropped · ${fmt(failed)} failed, since workers started`));

  const attention = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", { text: "Needs attention" }), h("span", { class: "muted", text: "Reconnecting or failing cameras" })),
    issues.length
      ? h("div", { class: "list" }, ...issues.slice(0, 8).map((st) => h("div", { class: "list-row" },
        h("div", { class: "ev-icon bad" }, icon("alert")),
        h("div", { class: "grow" }, h("div", { class: "title", text: st.name }),
          h("div", { class: "sub", text: st.observed?.error || st.last_error || `On ${st.worker_id ? workerName(st.worker_id) : "no worker"}` })),
        pill(st.status),
        can("operator") && st.status === "failed" ? button("Retry", () => streamAction(st, "start"), "btn-ghost btn-sm") : null)))
      : emptyState("check", "Everything is running", "No camera needs attention right now."));

  const fleet = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", { text: "Fleet" }), h("a", { class: "spacer", href: "#/workers", text: "All workers" })),
    state.workers.length
      ? h("div", { class: "list" }, ...state.workers.slice(0, 6).map((w) => h("div", { class: "list-row" },
        h("div", { class: "ev-icon worker" }, icon("workers")),
        h("div", { class: "grow" }, h("div", { class: "title", text: w.name }),
          h("div", { class: "sub", text: `${w.assigned_streams} of ${w.capacity.max_streams} cameras · seen ${ago(w.last_seen)}` }),
          bar(w.capacity.max_streams ? w.assigned_streams / w.capacity.max_streams : 0, "blue")),
        pill(w.state))))
      : emptyState("workers", "No workers yet", "Start a worker with a worker token; it appears here within seconds."));

  const recent = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", { text: "Recent activity" }), h("a", { class: "spacer", href: "#/activity", text: "View all" })),
    eventList(state.events.slice(0, 6), "Nothing has happened since you opened the dashboard."));

  return [hero, kpis, h("section", { class: "cols" }, attention, fleet), recent];
});

// ---- cameras

VIEWS.cameras = (() => {
  let tableBody = null;
  let empty = null;
  const filters = [["all", "All"], ["running", "Running"], ["issues", "Issues"], ["stopped", "Stopped"]];
  function matches(s) {
    const q = state.filter.q.toLowerCase();
    const textOk = !q || [s.name, s.location, s.source_url, s.worker_id && workerName(s.worker_id)].some((v) => v && String(v).toLowerCase().includes(q));
    const f = state.filter.status;
    const statusOk = f === "all" || (f === "running" && s.status === "running") || (f === "issues" && ISSUE.has(s.status))
      || (f === "stopped" && s.status === "stopped");
    return textOk && statusOk;
  }
  function rows() {
    const list = state.streams.filter(matches);
    empty.hidden = list.length > 0;
    empty.replaceChildren(state.streams.length
      ? emptyState("search", "No cameras match", "Try another search or filter.")
      : emptyState("camera", "No cameras yet", "Add your first camera; it starts on a worker within seconds.",
        can("operator") ? button("Add camera", () => openStreamDialog(), "btn-primary", "plus") : null));
    tableBody.replaceChildren(...list.map((s) => {
      const actions = [];
      if (s.status === "running") actions.push(iconButton("live", "Watch live", () => openView(s)));
      if (can("operator")) {
        actions.push(iconButton("edit", "Edit", () => openStreamDialog(s)));
        if (s.desired_state === "running" && s.status !== "failed") actions.push(iconButton("stop", "Stop", () => streamAction(s, "stop")));
        else actions.push(iconButton("play", s.status === "failed" ? "Retry" : "Start", () => streamAction(s, "start")));
        actions.push(iconButton("trash", "Delete", () => deleteStream(s), "danger"));
      }
      const detail = s.observed?.error || s.last_error;
      return h("tr", {},
        h("td", {}, h("div", { class: "title", text: s.name }), h("div", { class: "sub", text: s.location || "No location" }),
          h("div", { class: "sub mono", text: s.source_url })),
        h("td", {}, pill(s.status), s.attempts ? h("div", { class: "sub", text: `Attempt ${s.attempts}` }) : null,
          detail ? h("div", { class: "err", text: detail }) : null),
        h("td", { text: s.worker_id ? workerName(s.worker_id) : "—" }),
        h("td", { class: "mono", text: s.observed?.fps != null ? s.observed.fps.toFixed(1) : "—" }),
        h("td", {}, h("span", { class: "tag", text: s.config.delivery_mode || "default" })),
        h("td", { class: "sub nowrap", text: ago(s.updated_at) }),
        h("td", { class: "actions" }, actions));
    }));
  }
  return {
    mount(view) {
      const search = h("input", { type: "search", placeholder: "Search cameras, locations, workers", value: state.filter.q,
        "aria-label": "Search cameras", oninput: (e) => { state.filter.q = e.target.value; rows(); } });
      const segmented = h("div", { class: "segmented", role: "group", "aria-label": "Filter by status" },
        ...filters.map(([value, label]) => h("button", {
          class: state.filter.status === value ? "on" : "", type: "button", text: label,
          onclick: (e) => { state.filter.status = value; for (const b of segmented.children) b.classList.toggle("on", b === e.target); rows(); },
        })));
      tableBody = h("tbody");
      empty = h("div", { hidden: true });
      view.append(
        h("div", { class: "toolbar" }, h("div", { class: "search" }, icon("search"), search), segmented),
        h("div", { class: "card" },
          h("div", { class: "table-wrap" }, h("table", {},
            h("thead", {}, h("tr", {}, ...["Camera", "Status", "Worker", "FPS", "Delivery", "Updated", ""].map((t) => h("th", { text: t })))),
            tableBody)),
          empty));
      rows();
    },
    update() { if (tableBody) rows(); },
  };
})();

// ---- live wall

VIEWS.live = (() => {
  let wall = null;
  let empty = null;
  const tiles = new Map();
  function tile(stream) {
    const video = h("video", { autoplay: true, playsinline: true });
    video.muted = true;
    const status = h("div", { class: "tile-status" });
    const play = h("button", { class: "play", "aria-label": `Play ${stream.name}`, onclick: () => startTile(stream.stream_id) }, icon("play"));
    const live = h("span", { class: "badge-live", text: "LIVE", hidden: true });
    const name = h("div", { class: "title" });
    const sub = h("div", { class: "sub" });
    const toggle = iconButton("play", "Play", () => (state.livePcs.has(stream.stream_id) ? stopTile(stream.stream_id) : startTile(stream.stream_id)));
    const el = h("div", { class: "card tile" },
      h("div", { class: "tile-video" }, video, play, live, status),
      h("div", { class: "tile-foot" }, h("div", { class: "grow" }, name, sub), toggle));
    return { el, video, status, play, live, name, sub, toggle };
  }
  function sync() {
    const running = state.streams.filter((s) => ["running", "reconnecting"].includes(s.status));
    for (const [id, t] of tiles) {
      if (!running.some((s) => s.stream_id === id)) { stopTile(id); t.el.remove(); tiles.delete(id); }
    }
    for (const s of running) {
      let t = tiles.get(s.stream_id);
      if (!t) { t = tile(s); tiles.set(s.stream_id, t); wall.append(t.el); }
      t.name.textContent = s.name;
      t.sub.textContent = `${s.location || "No location"} · ${workerName(s.worker_id)} · ${s.observed?.fps != null ? s.observed.fps.toFixed(1) : "–"} fps`;
    }
    empty.hidden = running.length > 0;
  }
  return {
    tiles,
    mount(view) {
      tiles.clear();
      wall = h("div", { class: "wall" });
      empty = h("div", { class: "card" }, emptyState("live", "No cameras are running", "Running cameras appear here; press play to watch."));
      view.append(wall, empty);
      sync();
    },
    update() { if (wall) sync(); },
  };
})();

async function startTile(id) {
  const t = VIEWS.live.tiles.get(id);
  if (!t || state.livePcs.has(id)) return;
  t.play.hidden = true;
  t.status.textContent = "Connecting…";
  try {
    const view = await api(`/streams/${id}/view`);
    if (!view.whep_url) throw new Error("Live view isn't available for this camera right now.");
    const pc = await playWhep(t.video, view.whep_url, (msg) => { t.status.textContent = msg; });
    state.livePcs.set(id, pc);
    t.live.hidden = false;
    t.status.textContent = "";
    t.toggle.replaceChildren(icon("stop"));
    t.toggle.title = "Stop";
  } catch (e) {
    t.play.hidden = false;
    t.status.textContent = e.message;
  }
}

function stopTile(id) {
  const pc = state.livePcs.get(id);
  if (pc) pc.close();
  state.livePcs.delete(id);
  const t = VIEWS.live.tiles.get(id);
  if (!t) return;
  t.video.srcObject = null;
  t.play.hidden = false;
  t.live.hidden = true;
  t.status.textContent = "";
  t.toggle.replaceChildren(icon("play"));
  t.toggle.title = "Play";
}

function playAllLive() { for (const id of VIEWS.live.tiles.keys()) startTile(id); }
function stopAllLive() { for (const id of [...state.livePcs.keys()]) stopTile(id); }

// ---- workers

VIEWS.workers = rebuildOn(() => {
  if (!state.workers.length) {
    return [h("div", { class: "card" }, emptyState("workers", "No workers have registered",
      "Start a worker with CONTROLLER_URL and a worker token; it registers itself and appears here."))];
  }
  return [h("div", { class: "grid-cards" }, ...state.workers.map((w) => {
    const slots = w.capacity.max_streams;
    const d = w.delivery || { sent: 0, dropped: 0, failed: 0, spooled: 0 };
    const metric = (label, value, fraction, tone) => h("div", { class: "metric" },
      h("div", { class: "metric-row" }, h("span", { text: label }), h("strong", { text: value })), bar(fraction, tone));
    const cpu = w.load.cpu_percent;
    const mem = w.load.memory_percent;
    const host = [w.host_info.hostname, w.host_info.cpus ? `${w.host_info.cpus} CPUs` : null].filter(Boolean).join(" · ");
    return h("div", { class: "card wcard" },
      h("div", { class: "wcard-head" },
        h("div", { class: "icon-wrap" }, icon("workers")),
        h("div", { class: "grow" }, h("h3", { text: w.name }), h("div", { class: "sub mono", text: `${w.worker_id} · v${w.version}` })),
        h("div", { style: "display:grid;gap:6px;justify-items:end" }, pill(w.state), w.draining ? h("span", { class: "pill warn", text: "draining" }) : null)),
      metric("Cameras", `${w.assigned_streams} / ${slots}`, slots ? w.assigned_streams / slots : 0, "blue"),
      metric("CPU", cpu != null ? `${Math.round(cpu)}%` : "–", (cpu || 0) / 100, cpu > 85 ? "bad" : cpu > 65 ? "warn" : ""),
      metric("Memory", mem != null ? `${Math.round(mem)}%` : "–", (mem || 0) / 100, mem > 85 ? "bad" : mem > 65 ? "warn" : ""),
      h("div", { class: "stats-row" },
        ...[["Sent", d.sent], ["Dropped", d.dropped], ["Failed", d.failed], ["Queued", d.spooled]]
          .map(([label, value]) => h("div", { class: "stat-mini" }, h("b", { text: fmt(value) }), h("span", { text: label })))),
      h("div", { class: "wcard-foot" },
        h("span", { class: "time", text: `${host || "Unknown host"} · seen ${ago(w.last_seen)}` }),
        can("operator") ? button(w.draining ? "Undrain" : "Drain", () => drainWorker(w, !w.draining), "btn-ghost btn-sm", w.draining ? "undrain" : "drain") : null,
        can("admin") && w.state !== "online" ? iconButton("trash", "Forget worker", () => deleteWorker(w), "danger") : null));
  }))];
});

// ---- activity

function describe(event) {
  const p = event.params || {};
  const name = p.name || streamName(p.stream_id) || "A camera";
  const worker = p.worker_id ? workerName(p.worker_id) : null;
  const map = {
    "stream.created": ["Camera added", name],
    "stream.updated": ["Camera settings changed", name],
    "stream.started": ["Camera started", name],
    "stream.stopped": ["Camera stopped", name],
    "stream.deleted": ["Camera deleted", p.stream_id],
    "stream.placed": ["Camera assigned", `${name} → ${worker}`],
    "stream.status": [`Camera ${p.status}`, p.error ? `${name}: ${p.error}` : `${name} on ${worker}`],
    "stream.released": ["Camera moved off a worker", `${name}: ${p.reason}`],
    "stream.failed": ["Camera failed", `${name}: ${p.error || "gave up after retries"}`],
    "worker.registered": ["Worker joined", p.name || worker],
    "worker.online": ["Worker online", worker],
    "worker.suspect": ["Worker missed syncs", worker],
    "worker.offline": ["Worker offline", `${worker}: its cameras move to other workers`],
    "worker.draining": ["Worker draining", worker],
    "worker.undrained": ["Worker accepting cameras", worker],
    "worker.deleted": ["Worker forgotten", worker],
  };
  const [title, sub] = map[event.event] || [event.event, ""];
  const bad = event.severity === "error" || event.severity === "warning";
  return { title, sub, cls: bad ? "bad" : event.category === "worker" ? "worker" : "stream",
    iconName: bad ? "alert" : event.category === "worker" ? "workers" : "camera" };
}

function eventList(events, emptyText) {
  if (!events.length) return emptyState("activity", "No events yet", emptyText);
  return h("div", { class: "list" }, ...events.map((e) => {
    const d = describe(e);
    return h("div", { class: "list-row" }, h("div", { class: `ev-icon ${d.cls}` }, icon(d.iconName)),
      h("div", { class: "grow" }, h("div", { class: "title", text: d.title }), h("div", { class: "sub", text: d.sub || "" })),
      h("span", { class: "time", text: ago(e.ts) }));
  }));
}

VIEWS.activity = rebuildOn(() => [h("div", { class: "card" },
  h("div", { class: "card-head" }, h("h3", { text: "Live events" }), h("span", { class: "muted", text: "Kept in this browser tab only" })),
  eventList(state.events, "Changes and status updates appear here as they happen."))]);

// ---- audit

VIEWS.audit = rebuildOn(() => {
  if (!state.audit.length) return [h("div", { class: "card" }, emptyState("audit", "Nothing recorded yet", "Every change made through the API is recorded here."))];
  return [h("div", { class: "card" }, h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, ...["When", "Who", "Action", "Target", "Details"].map((t) => h("th", { text: t })))),
    h("tbody", {}, ...state.audit.map((e) => h("tr", {},
      h("td", { class: "sub nowrap", title: e.ts, text: ago(e.ts) }),
      h("td", {}, h("div", { class: "title", text: e.subject }), h("div", { class: "sub", text: e.role || "" })),
      h("td", {}, h("span", { class: "tag", text: e.action })),
      h("td", { class: "mono sub", text: e.target_id || "" }),
      h("td", { class: "mono sub", text: Object.keys(e.details || {}).length ? JSON.stringify(e.details) : "" })))))))];
});

// ------------------------------------------------------------------ actions

const streamAction = (s, verb) => run(() => api(`/streams/${s.stream_id}/${verb}`, { method: "POST" }),
  verb === "stop" ? `Stopping ${s.name}` : `Starting ${s.name}`);

async function deleteStream(s) {
  if (!(await confirmAction(`Delete ${s.name}?`, "The camera stops and its settings are removed. Results already delivered are not affected.", "Delete camera"))) return;
  await run(() => api(`/streams/${s.stream_id}`, { method: "DELETE" }), `Deleted ${s.name}`);
}

const drainWorker = (w, draining) => run(() => api(`/workers/${w.worker_id}/drain`, { method: "POST", body: { draining } }),
  draining ? `Draining ${w.name}: its cameras move to other workers` : `${w.name} accepts cameras again`);

async function deleteWorker(w) {
  if (!(await confirmAction(`Forget ${w.name}?`, "Its cameras move to other workers. A worker with a valid token can register again; revoke its token to keep it out.", "Forget worker"))) return;
  await run(() => api(`/workers/${w.worker_id}`, { method: "DELETE" }), `Forgot ${w.name}`);
}

// ------------------------------------------------------------------ camera dialog

let editing = null;
const DEFAULTS = { fps: 8, detector_resolution: 320, detection_confidence: 0.9, results_interval_ms: 1000, snapshot: "best", include_frame: true, delivery_mode: "", results_url: "" };

function openStreamDialog(stream = null) {
  editing = stream;
  const form = $("stream-form");
  form.reset();
  $("stream-error").textContent = "";
  $("stream-dialog-title").textContent = stream ? `Edit ${stream.name}` : "Add camera";
  $("stream-submit").textContent = stream ? "Save changes" : "Add camera";
  const cfg = { ...DEFAULTS, ...(stream?.config || {}) };
  form.name.value = stream?.name || "";
  form.location.value = stream?.location || "";
  form.source_url.value = "";
  form.source_url.required = !stream;
  $("source-hint").textContent = stream ? `Leave empty to keep ${stream.source_url}` : "RTSP, RTMP, SRT or HTTP. The password is stored but never shown again.";
  for (const key of ["fps", "detector_resolution", "detection_confidence", "results_interval_ms", "snapshot"]) form[key].value = cfg[key] ?? "";
  form.delivery_mode.value = cfg.delivery_mode || "";
  form.results_url.value = cfg.results_url || "";
  form.include_frame.checked = cfg.include_frame !== false;
  form.metadata.value = JSON.stringify(stream?.metadata || {}, null, 2);
  $("stream-dialog").showModal();
}

$("stream-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  let metadata;
  try { metadata = JSON.parse(form.metadata.value || "{}"); } catch { $("stream-error").textContent = "Metadata must be valid JSON."; return; }
  const config = {
    fps: form.fps.value ? Number(form.fps.value) : null,
    detector_resolution: Number(form.detector_resolution.value),
    detection_confidence: Number(form.detection_confidence.value),
    results_interval_ms: Number(form.results_interval_ms.value),
    snapshot: form.snapshot.value,
    include_frame: form.include_frame.checked,
    delivery_mode: form.delivery_mode.value || null,
    results_url: form.results_url.value.trim() || null,
  };
  const body = { name: form.name.value.trim(), location: form.location.value.trim(), metadata, config };
  if (form.source_url.value.trim()) body.source_url = form.source_url.value.trim();
  $("stream-submit").disabled = true;
  try {
    if (editing) await api(`/streams/${editing.stream_id}`, { method: "PATCH", body });
    else await api("/streams", { method: "POST", body, headers: { "Idempotency-Key": crypto.randomUUID() } });
    $("stream-dialog").close();
    toast(editing ? "Camera saved" : "Camera added; it starts on a worker within seconds");
    scheduleRefresh();
  } catch (e) {
    $("stream-error").textContent = e.message;
  } finally {
    $("stream-submit").disabled = false;
  }
});

// ------------------------------------------------------------------ live view (WHEP)

async function openView(stream) {
  $("view-title").textContent = stream.name;
  $("view-sub").textContent = `${stream.location || "No location"} · ${workerName(stream.worker_id)}`;
  $("view-status").textContent = "Connecting…";
  $("view-dialog").showModal();
  try {
    const view = await api(`/streams/${stream.stream_id}/view`);
    if (!view.whep_url) throw new Error("Live view isn't available for this camera right now.");
    state.viewPc = await playWhep($("view-video"), view.whep_url, (msg) => { $("view-status").textContent = msg; });
    $("view-status").textContent = "";
  } catch (e) {
    $("view-status").textContent = e.message;
  }
}

$("view-dialog").addEventListener("close", () => {
  if (state.viewPc) { state.viewPc.close(); state.viewPc = null; }
  $("view-video").srcObject = null;
});

// MediaMTX lists its STUN/TURN servers as Link headers on OPTIONS (only to authorised viewers).
async function iceServers(url) {
  try {
    const res = await fetch(url, { method: "OPTIONS", headers: { Authorization: `Bearer ${state.token}` } });
    return parseIceLinks(res.ok ? res.headers.get("Link") : null);
  } catch {
    return [];  // no relay: direct connections still work on open networks
  }
}

function parseIceLinks(header) {
  if (!header) return [];
  return header.split(/,\s*(?=<)/).flatMap((link) => {
    const url = link.match(/^<([^>]+)>/);
    if (!url || !/rel="ice-server"/.test(link)) return [];
    const attr = (name) => (link.match(new RegExp(`${name}="([^"]*)"`)) || [])[1];
    const server = { urls: url[1] };
    if (attr("username") !== undefined) server.username = attr("username");
    if (attr("credential") !== undefined) server.credential = attr("credential");
    return [server];
  });
}

async function playWhep(video, url, onStatus) {
  const pc = new RTCPeerConnection({ iceServers: await iceServers(url) });
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.addTransceiver("audio", { direction: "recvonly" });
  pc.ontrack = (event) => {
    video.srcObject = event.streams[0];
    video.play().catch(() => { /* autoplay deferred until the tab is visible */ });
  };
  pc.onconnectionstatechange = () => {
    if (["failed", "disconnected"].includes(pc.connectionState)) onStatus(`Connection ${pc.connectionState}. Check that this browser can reach the worker's WebRTC ports.`);
  };
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise((resolve) => {
    if (pc.iceGatheringState === "complete") return resolve();
    pc.addEventListener("icegatheringstatechange", () => pc.iceGatheringState === "complete" && resolve());
    setTimeout(resolve, 2000);
  });
  // The token goes in a header, not the URL; MediaMTX passes it to the worker's auth hook.
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/sdp", Authorization: `Bearer ${state.token}` }, body: pc.localDescription.sdp });
  if (!res.ok) { pc.close(); throw new Error(res.status === 401 ? "You're not allowed to watch this camera." : `The worker answered ${res.status}.`); }
  await pc.setRemoteDescription({ type: "answer", sdp: await res.text() });
  return pc;
}

// ------------------------------------------------------------------ live events

let wsRetry = 0;
function connectEvents() {
  if (!state.token) return;
  const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/v1/events`;
  // Tokens made of RFC 7230 token characters (this includes JWTs) travel as a subprotocol,
  // out of URLs and logs; others (e.g. base64 with "/" or "=") fall back to ?token=.
  const safe = /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(state.token);
  const ws = safe ? new WebSocket(url, [`watchtower.bearer.${state.token}`]) : new WebSocket(`${url}?token=${encodeURIComponent(state.token)}`);
  state.ws = ws;
  ws.onopen = () => { wsRetry = 0; $("conn").classList.add("on"); $("conn-text").textContent = "Live updates on"; };
  ws.onmessage = (message) => {
    const event = JSON.parse(message.data);
    if (event.event === "ping") return;
    state.events.unshift(event);
    state.events.length = Math.min(state.events.length, 200);
    if (event.severity === "error") toast(`${describe(event).title}: ${describe(event).sub}`, true);
    scheduleRefresh();
  };
  ws.onclose = () => {
    $("conn").classList.remove("on");
    $("conn-text").textContent = "Reconnecting…";
    if (!state.token) return;
    wsRetry = Math.min(wsRetry + 1, 6);
    setTimeout(connectEvents, 500 * 2 ** wsRetry);
  };
}

// ------------------------------------------------------------------ single sign-on (OIDC, code + PKCE)

let ssoConfig = null;
let ssoTimer = null;
const redirectUri = () => `${location.origin}/ui/`;
const b64url = (bytes) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

async function discover() {
  const res = await fetch(`${ssoConfig.issuer.replace(/\/$/, "")}/.well-known/openid-configuration`);
  if (!res.ok) throw new Error("The identity provider is unreachable.");
  return res.json();
}

async function startSso() {
  const endpoints = await discover();
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = b64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
  const csrf = b64url(crypto.getRandomValues(new Uint8Array(16)));
  sessionStorage.setItem("wt-sso-pending", JSON.stringify({ verifier, state: csrf }));
  const url = new URL(endpoints.authorization_endpoint);
  url.search = new URLSearchParams({
    response_type: "code", client_id: ssoConfig.client_id, redirect_uri: redirectUri(), scope: ssoConfig.scope,
    state: csrf, code_challenge: challenge, code_challenge_method: "S256",
  }).toString();
  location.assign(url.toString());
}

async function tokenRequest(body) {
  const endpoints = await discover();
  const res = await fetch(endpoints.token_endpoint, {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: ssoConfig.client_id, ...body }),
  });
  if (!res.ok) throw new Error(`The identity provider refused the sign-in (${res.status}).`);
  const tokens = await res.json();
  sessionStorage.setItem("wt-token", tokens.access_token);
  if (tokens.refresh_token) {
    sessionStorage.setItem("wt-sso", JSON.stringify({ refresh_token: tokens.refresh_token }));
    clearTimeout(ssoTimer);  // renew quietly before the access token expires
    ssoTimer = setTimeout(refreshSso, Math.max(10, (tokens.expires_in || 300) - 30) * 1000);
  }
  return tokens.access_token;
}

async function finishSso(params) {
  const pending = JSON.parse(sessionStorage.getItem("wt-sso-pending") || "null");
  sessionStorage.removeItem("wt-sso-pending");
  history.replaceState(null, "", "/ui/");  // the code must not stay in the address bar
  if (params.get("error")) throw new Error(params.get("error_description") || params.get("error"));
  if (!pending || pending.state !== params.get("state")) throw new Error("Sign-in was interrupted; please try again.");
  return tokenRequest({ grant_type: "authorization_code", code: params.get("code"), redirect_uri: redirectUri(),
    code_verifier: pending.verifier });
}

async function refreshSso() {
  const sso = JSON.parse(sessionStorage.getItem("wt-sso") || "null");
  if (!sso || !ssoConfig) return;
  try {
    state.token = await tokenRequest({ grant_type: "refresh_token", refresh_token: sso.refresh_token });
  } catch {
    signOut("Your single sign-on session ended. Please sign in again.");
  }
}

$("sso").addEventListener("click", () => startSso().catch((e) => { $("login-error").textContent = e.message; }));

async function boot() {
  try { ssoConfig = (await (await fetch("/api/v1/auth/config")).json()).oidc; } catch { ssoConfig = null; }
  $("sso").hidden = $("sso-or").hidden = !ssoConfig;
  const params = new URLSearchParams(location.search);
  const returningFromSso = ssoConfig && (params.has("code") || params.has("error"));
  try {
    if (returningFromSso) return await signIn(await finishSso(params));
    if (ssoConfig && sessionStorage.getItem("wt-sso")) await refreshSso();
    const saved = storedToken();
    if (saved) return await signIn(saved);
    signOut();
  } catch (e) {
    if (e.message !== "unauthorized") signOut(e.message);
    // The identity provider let them in but the controller refused the token: usually no Watchtower role.
    else signOut(returningFromSso ? "You signed in, but Watchtower didn't accept your account. Ask an administrator to give you a Watchtower role." : "");
  }
}

boot();
