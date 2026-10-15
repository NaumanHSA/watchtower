// Watchtower dashboard. Plain ES modules, no build step, no third-party code.
// Everything user-controlled is inserted with textContent, never innerHTML.

const RANK = { viewer: 1, operator: 2, admin: 3 };
const STATUS_TONE = {
  running: "ok", online: "ok",
  starting: "warn", pending: "warn", reconnecting: "warn", suspect: "warn",
  error: "bad", failed: "bad", offline: "bad",
  stopped: "idle",
};

const state = { token: null, me: null, streams: [], workers: [], stats: null, tab: "streams", ws: null, pc: null };
const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------------ helpers

function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "text") el.textContent = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const badge = (value) => h("span", { class: `badge ${STATUS_TONE[value] || "idle"}`, text: value });

function ago(iso) {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function toast(message, bad = false) {
  const el = h("div", { class: `toast${bad ? " bad" : ""}`, role: bad ? "alert" : "status", text: message });
  $("toasts").append(el);
  setTimeout(() => el.remove(), bad ? 8000 : 4000);
}

const can = (role) => state.me && RANK[state.me.role] >= RANK[role];

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

// ------------------------------------------------------------------ session

function storedToken() {
  try { return sessionStorage.getItem("wt-token") || localStorage.getItem("wt-token"); } catch { return null; }
}

function saveToken(token, remember) {
  try {
    sessionStorage.setItem("wt-token", token);
    if (remember) localStorage.setItem("wt-token", token); else localStorage.removeItem("wt-token");
  } catch { /* storage unavailable: token lives in memory only */ }
}

async function signIn(token) {
  state.token = token;
  state.me = await api("/me");
  $("login").hidden = true;
  $("app").hidden = false;
  $("whoami").textContent = `${state.me.subject} · ${state.me.role}`;
  for (const el of document.querySelectorAll("[data-min-role]")) el.hidden = !can(el.dataset.minRole);
  connectEvents();
  await refresh();
}

function signOut(message) {
  try {
    sessionStorage.removeItem("wt-token"); localStorage.removeItem("wt-token"); sessionStorage.removeItem("wt-sso");
  } catch { /* ignore */ }
  clearTimeout(ssoTimer);
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
  $("login-error").textContent = "";
  try {
    await signIn(token);
    saveToken(token, $("login-remember").checked);
    $("login-token").value = "";
  } catch (e) {
    if (e.message !== "unauthorized") $("login-error").textContent = e.message;
    else $("login-error").textContent = "That token was not accepted.";
  }
});
$("logout").addEventListener("click", () => signOut());

// ------------------------------------------------------------------ data + rendering

let refreshTimer = null;
function scheduleRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refresh, 300);
}

async function refresh() {
  if (!state.token) return;
  try {
    const [stats, streams, workers] = await Promise.all([api("/stats"), api("/streams"), api("/workers")]);
    Object.assign(state, { stats, streams, workers });
    render();
    if (state.tab === "audit" && can("admin")) await loadAudit();
  } catch (e) {
    if (e.message !== "unauthorized") toast(`Refresh failed: ${e.message}`, true);
  }
}

function render() {
  renderStats();
  renderStreams();
  renderWorkers();
}

function renderStats() {
  const { workers, streams, capacity } = state.stats;
  const stat = (value, label) => h("div", { class: "stat" }, h("div", { class: "value", text: value }), h("div", { class: "label", text: label }));
  const problems = (streams.error || 0) + (streams.failed || 0);
  $("stats").replaceChildren(
    stat(`${workers.online || 0} / ${Object.values(workers).reduce((a, b) => a + b, 0)}`, "workers online"),
    stat(`${streams.running || 0}`, "cameras running"),
    stat(`${(streams.pending || 0) + (streams.starting || 0) + (streams.reconnecting || 0)}`, "starting / reconnecting"),
    stat(`${problems}`, "with errors"),
    stat(`${capacity.used} / ${capacity.online}`, "slots used (online)"),
  );
}

function renderStreams() {
  const workerName = Object.fromEntries(state.workers.map((w) => [w.worker_id, w.name]));
  const rows = state.streams.map((s) => {
    const detail = s.observed?.error || s.last_error;
    const actions = [];
    if (["running", "reconnecting"].includes(s.status)) actions.push(h("button", { class: "small", onclick: () => openView(s) }, "Watch"));
    if (can("operator")) {
      actions.push(h("button", { class: "small", onclick: () => openStreamDialog(s) }, "Edit"));
      if (s.desired_state === "running" && s.status !== "failed") actions.push(h("button", { class: "small", onclick: () => streamAction(s, "stop") }, "Stop"));
      else actions.push(h("button", { class: "small", onclick: () => streamAction(s, "start") }, s.status === "failed" ? "Retry" : "Start"));
      actions.push(h("button", { class: "small danger", onclick: () => deleteStream(s) }, "Delete"));
    }
    return h("tr", {},
      h("td", {}, h("div", { text: s.name }), h("div", { class: "sub", text: s.location || "" })),
      h("td", {}, badge(s.status), s.attempts ? h("div", { class: "sub", text: `attempt ${s.attempts}` }) : null),
      h("td", { text: s.worker_id ? (workerName[s.worker_id] || s.worker_id) : "—" }),
      h("td", { class: "mono", text: s.observed?.fps != null ? s.observed.fps.toFixed(1) : "—" }),
      h("td", {}, h("div", { class: "mono sub", text: s.source_url }), detail ? h("div", { class: "error", text: detail }) : null),
      h("td", { class: "actions" }, actions),
    );
  });
  $("streams").replaceChildren(...rows);
  $("streams-empty").hidden = rows.length > 0;
}

function renderWorkers() {
  const rows = state.workers.map((w) => {
    const actions = [];
    if (can("operator")) {
      actions.push(h("button", { class: "small", onclick: () => drainWorker(w, !w.draining) }, w.draining ? "Undrain" : "Drain"));
    }
    if (can("admin") && w.state !== "online") {
      actions.push(h("button", { class: "small danger", onclick: () => deleteWorker(w) }, "Forget"));
    }
    const load = [w.load.cpu_percent != null ? `CPU ${Math.round(w.load.cpu_percent)}%` : null,
      w.load.memory_percent != null ? `RAM ${Math.round(w.load.memory_percent)}%` : null].filter(Boolean).join(" · ");
    const d = w.delivery;
    return h("tr", {},
      h("td", {}, h("div", { text: w.name }), h("div", { class: "sub mono", text: `${w.worker_id} · v${w.version}` })),
      h("td", {}, badge(w.state), w.draining ? h("div", { class: "sub", text: "draining" }) : null),
      h("td", { class: "mono", text: `${w.assigned_streams} / ${w.capacity.max_streams}` }),
      h("td", { class: "sub", text: load || "—" }),
      h("td", { class: "sub mono", text: d ? `${d.sent} sent · ${d.dropped} dropped · ${d.failed} failed${d.spooled ? ` · ${d.spooled} queued` : ""}` : "—" }),
      h("td", { class: "sub", text: ago(w.last_seen) }),
      h("td", { class: "actions" }, actions),
    );
  });
  $("workers").replaceChildren(...rows);
  $("workers-empty").hidden = rows.length > 0;
}

async function loadAudit() {
  const entries = await api("/audit?limit=200");
  const rows = entries.map((e) => h("tr", {},
    h("td", { class: "sub", title: e.ts, text: ago(e.ts) }),
    h("td", {}, h("div", { text: e.subject }), h("div", { class: "sub", text: e.role || "" })),
    h("td", { class: "mono", text: e.action }),
    h("td", { class: "mono sub", text: e.target_id || "" }),
    h("td", { class: "mono sub", text: Object.keys(e.details || {}).length ? JSON.stringify(e.details) : "" }),
  ));
  $("audit").replaceChildren(...rows);
  $("audit-empty").hidden = rows.length > 0;
}

// ------------------------------------------------------------------ actions

async function run(action, success) {
  try {
    await action();
    if (success) toast(success);
    scheduleRefresh();
  } catch (e) {
    if (e.message !== "unauthorized") toast(e.message, true);
  }
}

const streamAction = (s, verb) => run(() => api(`/streams/${s.stream_id}/${verb}`, { method: "POST" }),
  verb === "stop" ? `Stopping ${s.name}` : `Starting ${s.name}`);
const deleteStream = (s) => confirmInline(`Delete ${s.name}?`) && run(() => api(`/streams/${s.stream_id}`, { method: "DELETE" }), `Deleted ${s.name}`);
const drainWorker = (w, draining) => run(() => api(`/workers/${w.worker_id}/drain`, { method: "POST", body: { draining } }),
  draining ? `Draining ${w.name}: its cameras move to other workers` : `${w.name} accepts cameras again`);
const deleteWorker = (w) => confirmInline(`Forget worker ${w.name}?`) && run(() => api(`/workers/${w.worker_id}`, { method: "DELETE" }), `Forgot ${w.name}`);

function confirmInline(message) {
  // window.confirm keeps this dependency-free; destructive actions are rare.
  return window.confirm(message);
}

// ------------------------------------------------------------------ stream dialog

let editing = null;
const DEFAULTS = { fps: 8, detector_resolution: 320, detection_confidence: 0.9, results_interval_ms: 1000, snapshot: "best", include_frame: true, delivery_mode: "", results_url: "" };

function openStreamDialog(stream = null) {
  editing = stream;
  const form = $("stream-form");
  form.reset();
  $("stream-error").textContent = "";
  $("stream-dialog-title").textContent = stream ? `Edit ${stream.name}` : "Add camera";
  const cfg = { ...DEFAULTS, ...(stream?.config || {}) };
  form.name.value = stream?.name || "";
  form.location.value = stream?.location || "";
  form.source_url.value = "";
  form.source_url.required = !stream;
  $("source-hint").textContent = stream ? `Leave empty to keep ${stream.source_url}` : "";
  for (const key of ["fps", "detector_resolution", "detection_confidence", "results_interval_ms", "snapshot"]) form[key].value = cfg[key] ?? "";
  form.delivery_mode.value = cfg.delivery_mode || "";
  form.results_url.value = cfg.results_url || "";
  form.include_frame.checked = cfg.include_frame !== false;
  form.metadata.value = JSON.stringify(stream?.metadata || {}, null, 2);
  $("stream-dialog").showModal();
}

$("add-stream").addEventListener("click", () => openStreamDialog());

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
    toast(editing ? "Saved" : "Camera added; it starts on a worker within seconds");
    scheduleRefresh();
  } catch (e) {
    $("stream-error").textContent = e.message;
  } finally {
    $("stream-submit").disabled = false;
  }
});

for (const button of document.querySelectorAll("[data-close]")) {
  button.addEventListener("click", () => button.closest("dialog").close());
}

// ------------------------------------------------------------------ live view (WHEP)

async function openView(stream) {
  $("view-title").textContent = stream.name;
  $("view-status").textContent = "Connecting…";
  $("view-dialog").showModal();
  try {
    const view = await api(`/streams/${stream.stream_id}/view`);
    if (!view.whep_url) throw new Error("Live view is not available for this camera right now.");
    state.pc = await playWhep($("view-video"), view.whep_url);
    $("view-status").textContent = "";
  } catch (e) {
    $("view-status").textContent = e.message;
  }
}

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

async function playWhep(video, url) {
  const pc = new RTCPeerConnection({ iceServers: await iceServers(url) });
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.addTransceiver("audio", { direction: "recvonly" });
  pc.ontrack = (event) => {
    video.srcObject = event.streams[0];
    video.play().catch(() => { /* autoplay deferred until the tab is visible */ });
  };
  pc.onconnectionstatechange = () => {
    if (["failed", "disconnected"].includes(pc.connectionState)) $("view-status").textContent = `Connection ${pc.connectionState}. Check that this browser can reach the worker's WebRTC ports.`;
  };
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise((resolve) => {
    if (pc.iceGatheringState === "complete") return resolve();
    pc.addEventListener("icegatheringstatechange", () => pc.iceGatheringState === "complete" && resolve());
    setTimeout(resolve, 2000);
  });
  // The token goes in a header, not the URL; MediaMTX passes it to the worker's auth hook.
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/sdp", Authorization: `Bearer ${state.token}` }, body: pc.localDescription.sdp });
  if (!res.ok) { pc.close(); throw new Error(res.status === 401 ? "Not allowed to watch this camera." : `The worker answered ${res.status}.`); }
  await pc.setRemoteDescription({ type: "answer", sdp: await res.text() });
  return pc;
}

$("view-dialog").addEventListener("close", () => {
  if (state.pc) { state.pc.close(); state.pc = null; }
  $("view-video").srcObject = null;
});

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
  ws.onopen = () => { wsRetry = 0; $("live").textContent = "live"; $("live").classList.add("on"); };
  ws.onmessage = (message) => {
    const event = JSON.parse(message.data);
    if (event.event === "ping") return;
    if (event.severity === "error") toast(`${event.event.replace(".", " ")}: ${event.params?.error || event.params?.stream_id || ""}`, true);
    scheduleRefresh();
  };
  ws.onclose = () => {
    $("live").textContent = "offline";
    $("live").classList.remove("on");
    if (!state.token) return;
    wsRetry = Math.min(wsRetry + 1, 6);
    setTimeout(connectEvents, 500 * 2 ** wsRetry);
    scheduleRefresh();
  };
}

// Relative times drift; a slow poll also covers missed events.
setInterval(() => state.token && refresh(), 15000);

// ------------------------------------------------------------------ tabs + boot

for (const tab of document.querySelectorAll("[data-tab]")) {
  tab.addEventListener("click", () => {
    state.tab = tab.dataset.tab;
    for (const t of document.querySelectorAll("[data-tab]")) t.setAttribute("aria-selected", String(t === tab));
    $("tab-streams").hidden = state.tab !== "streams";
    $("tab-workers").hidden = state.tab !== "workers";
    $("tab-audit").hidden = state.tab !== "audit";
    if (state.tab === "audit") loadAudit().catch((e) => toast(e.message, true));
  });
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
  try {
    if (ssoConfig && (params.has("code") || params.has("error"))) return await signIn(await finishSso(params));
    if (ssoConfig && sessionStorage.getItem("wt-sso")) await refreshSso();
    const saved = storedToken();
    if (saved) return await signIn(saved);
    signOut();
  } catch (e) {
    signOut(e.message === "unauthorized" ? "" : e.message);
  }
}

boot();
