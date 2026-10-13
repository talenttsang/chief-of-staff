// Talent Solutions AI: board, knowledge base, interview, access, and the chat window. Voice lives in voice.js.
"use strict";

const STATUS_ORDER = ["todo", "in_progress", "done"];
const TYPE_LABEL = { system_change: "Change" };
const STATUS_LABEL = { done: "Done", suggested: "Waiting", failed: "Failed", dismissed: "Dismissed" };
const SYSTEM_GROUPS = [
  ["requested", "Requested by the assistant"], ["needed", "Needed"], ["available", "Credentials exist, not connected yet"],
  ["no_access", "Key in Secret Manager, but the app can't read it"], ["error", "Key found, but the system refused it"],
  ["connected", "Connected"], ["not_used", "Not used"],
];
// Google permissions in plain words, for the Reconnect banner.
const SCOPE_LABEL = {
  "calendar.events": "your calendar",
  "gmail.readonly": "reading Gmail", "gmail.compose": "drafting emails", "drive.readonly": "reading Drive",
  "spreadsheets.readonly": "reading Sheets", "analytics.readonly": "Analytics", "webmasters.readonly": "Search Console",
  "content": "Merchant Center",
};
const COMPANY_LABEL = { helperconnects: "HelperConnects", helperxpress: "HelperXpress", both: "Both" };
const SUGGESTIONS = [
  "Interview me: what do you most need to know?", "What do you know so far about the companies?",
  "Which systems do you need access to first, and how do I connect them?", "What's on my calendar this week?",
];

let state = null;      // /api/state: tasks, actions, runs, settings, status
let kb = null;         // /api/knowledge: projects, questions, systems, facts, partners
let partner = "assistant";
let talkTurns = [];
let editing = null;
let editingProject = null;
let talkBusy = false;

const $ = (id) => document.getElementById(id);

// Builds DOM nodes; text always goes in as text nodes, never as HTML.
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

const safeUrl = (u) => (typeof u === "string" && u.startsWith("https://") ? u : null);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const where = (t) => (t.source_from && t.source_from !== t.space_label ? `${t.space_label} · ${t.source_from}` : t.space_label);

async function api(method, path, body) {
  const resp = await fetch(path, {
    method, credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Chat-Assistant": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (resp.status === 401) { location.reload(); throw new Error("Signed out"); }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `Request failed (${resp.status})`);
  return data;
}

function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.remove("show"), 3800);
}

function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso.split("#")[0]).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso.split("#")[0]).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function todayStr() {
  const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 10);
}

// -- loading ---------------------------------------------------------------------------
async function load() {
  [state, kb] = await Promise.all([api("GET", "/api/state"), api("GET", "/api/knowledge")]);
  render();
  checkVersion();
}

// A new deploy changes the server's version; an open tab would otherwise keep running the old
// scripts. Reload when nothing is in progress, or say so when something is.
function checkVersion() {
  const mine = document.body.dataset.version;
  if (!state.version || !mine || state.version === mine) return;
  const busy = talkBusy || !$("voicebar").hidden || document.querySelector("dialog[open]") || $("message").value.trim();
  let tried = null;
  try { tried = sessionStorage.getItem("reloadedFor"); } catch { /* storage unavailable */ }
  if (!busy && tried !== state.version) {   // once per version, so a stuck mismatch can't loop
    try { sessionStorage.setItem("reloadedFor", state.version); } catch { /* storage unavailable */ }
    location.reload(); return;
  }
  if ($("updateBanner")) return;
  $("banners").append(el("div", { class: "banner", id: "updateBanner" },
    el("span", {}, el("b", {}, "A new version is ready. "), "Reload when you're done to get it."),
    el("button", { class: "btn primary", onclick: () => location.reload() }, "Reload")));
}

function render() {
  renderHeader();
  renderBanners();
  renderPartners();
  renderBoard();
  renderProjects();
  renderQuestions();
  renderSystems();
  renderActivity();
}

function renderHeader() {
  const run = state.status.last_run;
  $("dot").classList.toggle("on", state.connected && !state.status.connection_error);
  $("autoAct").checked = !!state.settings.auto_act;
  if (!run) { $("lastrun").textContent = "No runs yet. It works its own tasks every hour, or press Run now."; return; }
  if (run.error) {
    $("lastrun").textContent = `Last run ${ago(run.started_at)} failed: ${run.error}${run.trace ? " (details in Activity → Runs)" : ""}`;
    return;
  }
  $("lastrun").textContent = `Last run ${ago(run.finished_at || run.started_at)} · `
    + `${plural((run.tasks_worked || []).length, "task")} worked · ${plural(run.systems_connected || 0, "system")} connected`;
}

function renderBanners() {
  const box = $("banners"); box.replaceChildren();
  if (!state.connected) {
    box.append(el("div", { class: "banner" },
      el("span", {}, el("b", {}, "Connect Google. "), `Talent Solutions AI reads Gmail, Calendar and Drive as ${state.google_account || "your Google account"}. Press Connect and sign in to Google as that account.`),
      el("a", { class: "btn primary", href: "/connect/google" }, "Connect")));
  } else if (state.status.connection_error) {
    box.append(el("div", { class: "banner bad" },
      el("span", {}, el("b", {}, "The assistant can't reach Google. "), state.status.connection_error),
      el("a", { class: "btn primary", href: "/connect/google" }, "Reconnect")));
  }
  if ((state.status.missing_scopes || []).length) {
    const what = state.status.missing_scopes.map((s) => SCOPE_LABEL[s] || s);
    box.append(el("div", { class: "banner" },
      el("span", {}, el("b", {}, "Talent Solutions AI needs your OK in Google for: "), what.join(", "),
        ". Press Reconnect and allow everything Google asks."),
      el("a", { class: "btn primary", href: "/connect/google" }, "Reconnect")));
  }
}

// -- Talk ------------------------------------------------------------------------------
const partnerInfo = (name) => kb.partners.find((p) => p.name === name) || { label: name, available: false };

function renderPartners() {
  $("partners").replaceChildren(...kb.partners.map((p) => el("button", {
    class: `partner${p.name === partner ? " active" : ""}`, title: p.available ? p.models || "" : p.detail, "data-partner": p.name,
    "aria-pressed": String(p.name === partner), onclick: () => selectPartner(p.name),
  }, el("span", { class: `pdot${p.available ? "" : " off"}` }), p.label)));
  renderReadAloud();
  const info = partnerInfo(partner);
  $("startVoice").disabled = !$("voicebar").hidden || !info.available;  // one call at a time
  $("startVoice").title = info.available ? `Talk live with ${info.label}` : info.detail || `${info.label} isn't available`;
  $("message").placeholder = `Message ${info.label}…`;
}

function renderReadAloud() {
  const on = !!(speaker() && speaker().auto);
  $("readAloud").setAttribute("aria-pressed", String(on));
  $("readAloud").title = on ? "Reading new replies aloud. Click to stop" : "Read new replies aloud";
}
$("readAloud").onclick = () => {
  if (!speaker()) return;
  speaker().auto = !speaker().auto;
  renderReadAloud();
  toast(speaker().auto ? "New replies will be read aloud" : "Replies won't be read aloud");
};

async function selectPartner(name) {
  if (name !== partner && window.companyAssistant.endVoice) window.companyAssistant.endVoice();
  if (name !== partner && speaker()) speaker().stop();
  partner = name;
  renderPartners();
  await loadTalk();
}

async function loadTalk() {
  try { talkTurns = (await api("GET", `/api/talk/${partner}`)).turns; } catch (e) { toast(e.message); talkTurns = []; }
  renderThread();
}

const speaker = () => window.companySpeaker;  // static/speaker.js

function turnNode(t, live = false) {
  const tools = (t.tools || []).map((x) => x.tool);
  const who = partner;  // the thread on screen belongs to the selected partner
  const readAloud = t.role === "assistant" && !live && t.text
    ? el("button", { class: "spk", title: "Read aloud", "aria-label": "Read aloud",
        onclick: (e) => speaker() && speaker().toggle(t.text, who, e.currentTarget) }, "🔊")
    : null;
  return el("div", { class: `msg ${t.role}${live ? " live" : ""}` }, t.text,
    tools.length ? el("div", { class: "tools" }, `Used: ${[...new Set(tools)].join(", ")}`) : null,
    t.at ? el("div", { class: "when" }, `${t.voice ? "🎙 " : ""}${ago(t.at)}`, readAloud) : readAloud);
}

function renderThread(extra = []) {
  const box = $("thread");
  const nodes = talkTurns.map((t) => turnNode(t)).concat(extra);
  if (!nodes.length) {
    box.replaceChildren(el("div", { class: "suggestions" }, SUGGESTIONS.map((s) =>
      el("button", { onclick: () => { $("message").value = s; sendMessage(); } }, s))));
    return;
  }
  box.replaceChildren(...nodes);
  box.scrollTop = box.scrollHeight;
}

async function sendMessage() {
  const text = $("message").value.trim();
  if (!text || talkBusy) return;
  talkBusy = true; $("send").disabled = true;
  $("message").value = "";
  const pending = { role: "user", text, at: new Date().toISOString() };
  talkTurns.push(pending);
  renderThread([el("div", { class: "msg assistant live" }, el("span", { class: "spinner" }), " thinking…")]);
  try {
    const asked = partner;
    const { reply } = await api("POST", `/api/talk/${asked}`, { message: text });
    talkTurns.push(reply);
    renderThread();
    refreshKnowledge();
    if (!chatOpen) { unread = true; renderLauncher(); }
    if (speaker() && speaker().auto && asked === partner) {
      const buttons = $("thread").querySelectorAll(".msg.assistant .spk");
      speaker().play(reply.text, asked, buttons[buttons.length - 1]);
    }
  } catch (e) {
    talkTurns.pop(); renderThread();
    $("message").value = text;
    toast(e.message);
  } finally {
    talkBusy = false; $("send").disabled = false; $("message").focus();
  }
}

async function clearTalk() {
  if (!confirm(`Clear your conversation with ${partnerInfo(partner).label}? What it learned stays in the knowledge base.`)) return;
  await api("DELETE", `/api/talk/${partner}`).catch((e) => toast(e.message));
  talkTurns = []; renderThread();
}
$("clearTalk").onclick = clearTalk;

// -- the chat window ----------------------------------------------------------------------
// The chat floats at the bottom right over every tab and minimizes to a button. A call keeps going while
// it's minimized; the button says so, and shows a dot when a reply arrives unseen.
let chatOpen = true;
let unread = false;

function setChat(open, focus = true) {
  chatOpen = open;
  $("chat").hidden = !open;
  $("chatLauncher").hidden = open;
  $("chatLauncher").setAttribute("aria-expanded", String(open));
  try { localStorage.setItem("chatOpen", open ? "1" : "0"); } catch { /* storage unavailable */ }
  if (open) {
    unread = false;
    $("thread").scrollTop = $("thread").scrollHeight;
    if (focus) $("message").focus();
  } else if (focus) $("chatLauncher").focus();
  renderLauncher();
}

function renderLauncher() {
  const onCall = !$("voicebar").hidden;
  const label = onCall ? "On a call · Talent Solutions AI" : "Talent Solutions AI";
  $("chatLauncher").replaceChildren(...[onCall ? el("span", { class: "pulse", "aria-hidden": "true" }) : el("span", { "aria-hidden": "true" }, "💬"),
    label, unread && !onCall ? el("span", { class: "badge", title: "New reply" }) : null].filter(Boolean));
  $("chatLauncher").setAttribute("aria-label", `${label}${unread ? " (new reply)" : ""}. Open the chat`);
}

// Opens the chat with Talent Solutions AI and a message ready to send (from Questions and Access).
function askInChat(text) {
  setChat(true);
  if (partner !== "assistant") selectPartner("assistant");
  $("message").value = text;
  $("message").focus();
}

$("minimizeChat").onclick = () => setChat(false);
$("chatLauncher").onclick = () => setChat(true);
// Escape minimizes, from inside the chat or from nowhere in particular (sending a message leaves focus on the page).
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !chatOpen || document.querySelector("dialog[open]")) return;
  if (!$("chat").contains(document.activeElement) && document.activeElement !== document.body) return;
  e.preventDefault(); setChat(false);
});
new MutationObserver(renderLauncher).observe($("voicebar"), { attributes: true, attributeFilter: ["hidden"] });
{
  // Remembered per browser; the first time it starts open on a computer, minimized on a phone.
  let saved = null;
  try { saved = localStorage.getItem("chatOpen"); } catch { /* storage unavailable */ }
  setChat(saved === null ? matchMedia("(min-width: 601px)").matches : saved === "1", false);
}

async function refreshKnowledge() {
  try { kb = await api("GET", "/api/knowledge"); renderProjects(); renderQuestions(); renderSystems(); renderPartners(); }
  catch { /* next full load will catch up */ }
}

$("send").onclick = sendMessage;
$("message").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

// Connecting for real: a stored key needs the one owner approval (/connect/setup), a missing key is pasted
// here and saved straight into Secret Manager, a Google tool is part of the Google sign-in.
function connectButton(s) {
  const c = s.connect;
  if (c.kind === "paste") return el("button", { class: "btn primary", onclick: () => openConnect(s) }, "Connect");
  const href = c.kind === "google" ? "/connect/google?back=access" : `/connect/setup?then=${encodeURIComponent(s.id)}`;
  return el("a", { class: "btn primary", href }, "Connect");
}

let connecting = null;
function openConnect(s) {
  connecting = s;
  $("connectTitle").textContent = `Connect ${s.name}`;
  $("connectHelp").textContent = s.connect.help || "";
  $("connectFields").replaceChildren(...s.connect.fields.map((f) => el("label", {}, f.label,
    el("input", { name: f.secret, type: f.hidden ? "password" : "text", placeholder: f.placeholder || "",
      autocomplete: "off", required: true, spellcheck: "false" }))));
  $("connectDialog").showModal();
}
$("cancelConnect").onclick = () => $("connectDialog").close();
$("connectForm").onsubmit = async (e) => {
  e.preventDefault();
  const s = connecting;
  const values = Object.fromEntries([...$("connectFields").querySelectorAll("input")].map((i) => [i.name, i.value.trim()]));
  $("saveConnect").disabled = true; $("saveConnect").textContent = "Connecting…";
  try {
    const resp = await fetch(`/api/connect/${encodeURIComponent(s.connect.integration)}`, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Chat-Assistant": "1" }, body: JSON.stringify({ values }),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.status === 409 && data.needs_setup) {  // the one owner approval hasn't happened yet
      toast("One Google approval first; then paste it again.");
      location.href = data.url;
      return;
    }
    if (!resp.ok) throw new Error(data.error || `Couldn't connect (${resp.status})`);
    $("connectDialog").close();
    toast(data.result && data.result.ok ? `${s.name} is connected` : `Saved, but ${s.name} said: ${(data.result || {}).detail || "no answer"}`);
    refreshKnowledge();
  } catch (err) { toast(err.message); }
  finally { $("saveConnect").disabled = false; $("saveConnect").textContent = "Connect"; }
};

// Try each system with its key (the server does it; keys never reach the page). Right after the owner approval,
// Google can take a minute to apply new access, so "can't read" results are retried a few times.
async function checkConnections(retries = 0) {
  const button = $("checkSystems");
  button.disabled = true; button.textContent = "Checking…";
  try {
    const { results } = await api("POST", "/api/integrations/check");
    const all = Object.values(results), ok = all.filter((r) => r.ok).length;
    const waiting = all.filter((r) => !r.ok && /can't read/.test(r.detail || "")).length;
    refreshKnowledge();
    // Every system that isn't connected, with what it said, so the reason is on screen (and in a screenshot).
    const why = (r) => /can't read the (HUBSPOT|REAMAZE)/.test(r.detail || "") ? "waiting for its key: press Connect and paste it"
      : /Google isn't connected|insufficient|scope/i.test(r.detail || "") ? "press Connect to add it to your Google sign-in"
      : (r.detail || "no answer").slice(0, 160);
    const failing = all.filter((r) => !r.ok).map((r) => `• ${r.system}: ${why(r)}`);
    if (waiting && retries > 0) {
      showSetupBanner(`${ok} of ${all.length} systems connected. Google is still applying access for ${waiting}; checking again shortly…`);
      setTimeout(() => checkConnections(retries - 1), 30000);  // Google can take minutes to apply new access
    } else {
      showSetupBanner(failing.length ? `${ok} of ${all.length} systems connected. Not yet:\n${failing.join("\n")}` : `All ${all.length} systems connected.`);
      toast(`${ok} of ${all.length} systems connected`);
    }
  } catch (e) { toast(e.message); }
  finally { button.disabled = false; button.textContent = "Check connections"; }
}
function showSetupBanner(text) { $("setupBanner").hidden = !text; $("setupBanner").textContent = text; }
$("checkSystems").onclick = () => checkConnections();

// Back from the owner approval (/connect/setup) or a Google reconnect: test everything, open a pending paste.
function afterConnectRedirect() {
  const params = new URLSearchParams(location.search);
  if (params.get("tab") !== "access") return;
  switchTab("access");
  if (params.get("setup") === "done") {
    const report = kb.setup || {};
    const errors = (report.errors || []).length;
    showSetupBanner(errors ? `Access set up with ${errors} problem(s): ${report.errors.join("; ")}` : "Access set up. Testing every system…");
    checkConnections(12);  // up to six minutes
  }
  const open = params.get("open");
  const system = open && kb.systems.find((s) => s.id === open && s.connect && s.connect.kind === "paste");
  if (system) openConnect(system);
  history.replaceState(null, "", "/");
}

// -- Board -----------------------------------------------------------------------------
function visibleTasks() {
  const q = $("q").value.trim().toLowerCase();
  const owner = $("ownerFilter").value;
  return state.tasks.filter((t) => {
    if (owner === "me" && !t.owner_is_me) return false;
    if (owner === "others" && t.owner_is_me) return false;
    if (q && !`${t.title} ${t.detail || ""} ${t.owner || ""} ${t.space_label || ""}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

function taskCard(t) {
  const meta = el("div", { class: "meta" }, el("span", { class: `pri ${t.priority || "medium"}`, title: `${t.priority || "medium"} priority` }));
  meta.append(el("span", { class: `chip ${t.owner_is_me ? "me" : ""}` }, t.owner_is_me ? "You" : (t.owner || "Unassigned")));
  if (t.due) {
    const today = todayStr();
    const open = t.status !== "done";
    const cls = open && t.due < today ? "overdue" : open && t.due === today ? "today" : "";
    meta.append(el("span", { class: `chip ${cls}` }, t.due === today ? "Due today" : `Due ${t.due}`));
  }
  if (t.origin === "assistant") meta.append(el("span", { class: "chip", title: "Added by the assistant" }, "AI"));
  if (working(t)) meta.append(el("span", { class: "chip working" }, el("span", { class: "spinner" }), " working…"));
  else if (t.result) meta.append(el("span", { class: "chip", title: "The assistant has reported on this task" }, "🤖 report"));
  const next = STATUS_ORDER[STATUS_ORDER.indexOf(t.status) + 1];
  const card = el("div", { class: "card", tabindex: "0", role: "button",
      onclick: () => openTask(t), onkeydown: (e) => { if (e.key === "Enter") openTask(t); } },
    el("div", { class: "title" }, t.title), meta,
    t.space_label ? el("div", { class: "src", title: t.source_excerpt || "" }, where(t)) : null);
  if (next) card.append(el("button", { class: "advance", title: next === "done" ? "Mark done" : "Start", "aria-label": next === "done" ? "Mark done" : "Start",
    onclick: (e) => { e.stopPropagation(); updateTask(t.id, { status: next }).catch((err) => toast(err.message)); } }, next === "done" ? "✓" : "→"));
  return card;
}

function renderBoard() {
  const tasks = visibleTasks();
  const showDone = $("showDone").checked;
  $("boardCount").textContent = state.tasks.filter((t) => t.status !== "done").length;
  for (const col of document.querySelectorAll(".col")) {
    const status = col.dataset.status;
    let items = tasks.filter((t) => t.status === status);
    col.querySelector(".count").textContent = items.length;
    if (status === "done" && !showDone) items = [];
    items.sort((a, b) => (a.due || "9999").localeCompare(b.due || "9999") || (b.created_at || "").localeCompare(a.created_at || ""));
    const box = col.querySelector(".cards");
    box.replaceChildren(...items.map(taskCard));
    if (!items.length) box.append(el("div", { class: "empty" },
      status === "done" && !showDone ? "Tick “Show done” to see finished tasks" : "Nothing here"));
  }
}

// The assistant is on it: marked when work starts, cleared when it reports (15 min cap, as on the server).
const working = (t) => !!t.working_since && Date.now() - new Date(t.working_since).getTime() < 15 * 60 * 1000;

function showTaskReport(t) {
  const box = $("taskResult");
  box.hidden = !(t && (t.result || t.work_error || working(t)));
  if (box.hidden) return;
  if (working(t)) { box.replaceChildren(el("b", {}, "The assistant is working on this…")); return; }
  box.replaceChildren(el("b", {}, `The assistant's report (${ago(t.worked_at)})`),
    t.work_error ? `It stopped with an error: ${t.work_error}` : t.result,
    t.work_tools && t.work_tools.length ? el("div", { class: "tools" }, `Used: ${t.work_tools.join(", ")}`) : null);
}

$("workTask").onclick = async () => {
  if (!editing) { toast("Save the task first, then give it to Talent Solutions AI."); return; }
  const t = editing;
  $("taskDialog").close();
  state.tasks = state.tasks.map((x) => (x.id === t.id ? { ...x, owner: "Assistant", owner_is_me: false, working_since: new Date().toISOString() } : x));
  renderBoard();
  toast(`Talent Solutions AI is working on “${t.title}”. Its report will appear on the task and in the chat.`);
  try {
    const { task, done } = await api("POST", `/api/tasks/${encodeURIComponent(t.id)}/work`);
    state.tasks = state.tasks.map((x) => (x.id === t.id ? task : x));
    toast(done ? `Done: “${t.title}”` : `Report ready on “${t.title}”`);
    if (partner === "assistant") { await loadTalk(); if (!chatOpen) { unread = true; renderLauncher(); } }
  } catch (e) {
    toast(e.message);
    load().catch(() => {});
  }
  renderBoard();
};

function openTask(t) {
  editing = t || null;
  const f = $("taskForm");
  $("taskDialogTitle").textContent = t ? "Edit task" : "New task";
  f.title.value = t ? t.title : "";
  f.detail.value = t ? t.detail || "" : "";
  f.owner.value = t ? t.owner || "" : "";
  f.due.value = t ? t.due || "" : "";
  f.status.value = t ? t.status : "todo";
  f.priority.value = t ? t.priority || "medium" : "medium";
  f.owner_is_me.checked = t ? !!t.owner_is_me : true;
  $("deleteTask").hidden = !t;
  $("workTask").hidden = !t || t.status === "done";
  showTaskReport(t);
  const src = $("taskSource"); src.replaceChildren();
  if (t && t.space_label) {
    src.append(`From ${where(t)}${t.source_excerpt ? `: “${t.source_excerpt}” ` : " "}`);
  }
  $("taskDialog").showModal();
}

async function updateTask(id, fields) {
  const { task } = await api("PATCH", `/api/tasks/${encodeURIComponent(id)}`, fields);
  state.tasks = state.tasks.map((t) => (t.id === id ? task : t));
  renderBoard();
}

$("taskForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const fields = { title: f.title.value, detail: f.detail.value, owner: f.owner.value, due: f.due.value,
    status: f.status.value, priority: f.priority.value, owner_is_me: f.owner_is_me.checked };
  try {
    if (editing) await updateTask(editing.id, fields);
    else { const { task } = await api("POST", "/api/tasks", fields); state.tasks.unshift(task); renderBoard(); }
    $("taskDialog").close();
  } catch (err) { toast(err.message); }
});
$("cancelTask").onclick = () => $("taskDialog").close();
$("deleteTask").onclick = async () => {
  if (!editing || !confirm("Delete this task?")) return;
  try {
    await api("DELETE", `/api/tasks/${encodeURIComponent(editing.id)}`);
    state.tasks = state.tasks.filter((t) => t.id !== editing.id);
    $("taskDialog").close(); renderBoard();
  } catch (err) { toast(err.message); }
};
$("newTask").onclick = () => openTask(null);
for (const id of ["q", "ownerFilter", "showDone"]) $(id).addEventListener("input", renderBoard);

// -- Projects --------------------------------------------------------------------------
function renderProjects() {
  const company = $("companyFilter").value;
  const status = $("projectStatusFilter").value;
  const items = kb.projects.filter((p) => (!company || p.company === company || p.company === "both") && (!status || p.status === status));
  $("projectCount").textContent = kb.projects.filter((p) => p.status !== "done").length;
  const openTasks = (pid) => state.tasks.filter((t) => t.project_id === pid && t.status !== "done").length;
  const box = $("projects");
  if (!items.length) {
    box.replaceChildren(el("div", { class: "empty" }, kb.projects.length ? "No projects match the filters"
      : "No projects yet. They appear as the assistant hears your answers, or add one."));
    return;
  }
  box.replaceChildren(...items.map((p) => el("div", { class: "card", tabindex: "0", role: "button",
      onclick: () => openProject(p), onkeydown: (e) => { if (e.key === "Enter") openProject(p); } },
    el("div", { class: "title" }, p.name),
    el("div", { class: "meta" },
      el("span", { class: `chip ${p.status || ""}` }, p.status || "unknown"),
      p.company ? el("span", { class: "chip" }, COMPANY_LABEL[p.company] || p.company) : null,
      p.owner ? el("span", { class: "chip" }, p.owner) : null,
      p.deadline ? el("span", { class: "chip" }, `Due ${p.deadline}`) : null,
      openTasks(p.id) ? el("span", { class: "chip" }, plural(openTasks(p.id), "open task")) : null,
      null),
    (p.goal || p.summary) ? el("div", { class: "body" }, p.goal || p.summary) : null,
    p.next_steps ? el("div", { class: "body" }, `Next: ${p.next_steps}`) : null)));
}

function openProject(p) {
  editingProject = p || null;
  const f = $("projectForm");
  $("projectDialogTitle").textContent = p ? "Edit project" : "New project";
  for (const k of ["name", "owner", "deadline", "goal", "summary", "next_steps"]) f[k].value = p ? p[k] || "" : "";
  f.company.value = p ? p.company || "" : "";
  f.status.value = p ? p.status || "active" : "active";
  $("deleteProject").hidden = !p;
  $("projectSource").textContent = p && p.source ? `Added from ${p.source} · updated ${ago(p.updated_at)}` : "";
  $("projectDialog").showModal();
}

$("projectForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const fields = {};
  for (const k of ["name", "company", "status", "owner", "deadline", "goal", "summary", "next_steps"]) fields[k] = f[k].value;
  try {
    if (editingProject) await api("PATCH", `/api/projects/${encodeURIComponent(editingProject.id)}`, fields);
    else await api("POST", "/api/projects", fields);
    $("projectDialog").close(); await refreshKnowledge();
  } catch (err) { toast(err.message); }
});
$("cancelProject").onclick = () => $("projectDialog").close();
$("deleteProject").onclick = async () => {
  if (!editingProject || !confirm("Delete this project? Its tasks stay on the board.")) return;
  try { await api("DELETE", `/api/projects/${encodeURIComponent(editingProject.id)}`); $("projectDialog").close(); await refreshKnowledge(); }
  catch (err) { toast(err.message); }
};
$("newProject").onclick = () => openProject(null);
for (const id of ["companyFilter", "projectStatusFilter"]) $(id).addEventListener("input", renderProjects);

// -- Questions (the interview) ------------------------------------------------------------
function renderQuestions() {
  const open = kb.questions.filter((q) => q.status === "open")
    .sort((a, b) => (a.priority || 2) - (b.priority || 2) || (a.created_at || "").localeCompare(b.created_at || ""));
  const answered = kb.questions.filter((q) => q.status === "answered").slice(0, 40);
  const qc = $("questionCount"); qc.textContent = open.length; qc.classList.toggle("hot", open.length > 0);
  $("openQuestions").replaceChildren(...(open.length ? open.map(questionItem) : [el("div", { class: "empty" }, "No open questions. The assistant adds more as it learns.")]));
  $("answeredQuestions").replaceChildren(...(answered.length ? answered.map((q) => el("div", { class: "item" },
    el("div", { class: "q" }, q.question),
    el("div", { class: "reply" }, q.answer || ""),
    q.digest ? el("div", { class: "why" }, `Assistant: ${q.digest}`) : null,
    el("div", { class: "why" }, `Answered ${ago(q.answered_at)}`))) : [el("div", { class: "empty" }, "Nothing answered yet")]));
}

function questionItem(q) {
  const box = el("textarea", { placeholder: "Your answer (short is fine)", "aria-label": "Answer" });
  const answerBtn = el("button", { class: "btn primary" }, "Answer");
  answerBtn.onclick = async () => {
    const answer = box.value.trim();
    if (!answer) return;
    answerBtn.disabled = true; answerBtn.replaceChildren(el("span", { class: "spinner" }), " Saving and learning…");
    try {
      const { note } = await api("POST", `/api/questions/${encodeURIComponent(q.id)}/answer`, { answer });
      toast(note || "Saved");
      await Promise.all([refreshKnowledge(), load()]);
    } catch (e) { toast(e.message); answerBtn.disabled = false; answerBtn.textContent = "Answer"; }
  };
  return el("div", { class: "item" },
    el("div", { class: "top" }, el("span", { class: `pri ${q.priority === 1 ? "high" : q.priority === 3 ? "low" : "medium"}` }),
      el("span", {}, q.priority === 1 ? "Important" : q.priority === 3 ? "When you have time" : "Normal"),
      q.source ? el("span", {}, `· ${q.source}`) : null),
    el("div", { class: "q" }, q.question),
    q.why ? el("div", { class: "why" }, `Why: ${q.why}`) : null,
    box,
    el("div", { class: "actions" }, answerBtn,
      el("button", { class: "btn", onclick: () => askInChat(`Let's talk about this question [${q.id}]: ${q.question}`) }, "Discuss in chat"),
      el("button", { class: "btn", onclick: async () => {
        await api("POST", `/api/questions/${encodeURIComponent(q.id)}/dismiss`).catch((e) => toast(e.message));
        refreshKnowledge();
      } }, "Skip")));
}

// -- Access -----------------------------------------------------------------------------
function renderSystems() {
  const pending = kb.systems.filter((s) => s.status === "requested" || s.status === "needed").length;
  $("accessCount").textContent = pending;
  const box = $("systems");
  box.replaceChildren(...SYSTEM_GROUPS.map(([status, title]) => {
    const items = kb.systems.filter((s) => (s.status || "needed") === status);
    if (!items.length) return null;
    return el("div", {}, el("h3", {}, `${title} (${items.length})`), ...items.map((s) => {
      const select = el("select", { "aria-label": `Status of ${s.name}` },
        ...SYSTEM_GROUPS.map(([v, label]) => el("option", { value: v }, label.split(" (")[0])));
      select.value = s.status || "needed";
      select.onchange = async () => {
        try { await api("PATCH", `/api/systems/${encodeURIComponent(s.id)}`, { status: select.value }); refreshKnowledge(); }
        catch (e) { toast(e.message); }
      };
      return el("div", { class: "item" },
        el("div", { class: "top" }, el("b", {}, s.name), s.category ? el("span", { class: "chip" }, s.category) : null,
          el("span", { class: "spacer", style: "flex:1" }), select),
        s.unlocks ? el("div", { class: "reply" }, s.unlocks) : null,
        s.why ? el("div", { class: "why" }, status === "no_access" || status === "error" ? s.why : `Why the assistant asked: ${s.why}`) : null,
        status !== "connected" && status !== "not_used" ? el("div", { class: "actions" },
          s.connect ? connectButton(s) : el("button", { class: "btn",
            onclick: () => askInChat(`How do I give you access to ${s.name}? Walk me through it step by step.`) },
          "How do I connect this?")) : null);
    }));
  }).filter(Boolean));
}

// -- Activity -----------------------------------------------------------------------------
function actionItem(a) {
  const top = el("div", { class: "top" },
    el("span", { class: `status ${a.status}` }, STATUS_LABEL[a.status] || a.status),
    el("b", {}, TYPE_LABEL[a.type] || a.type),
    el("span", {}, "in"),
    el("span", {}, a.space_label || ""),
    el("span", {}, `· ${ago(a.executed_at || a.created_at)}`));
  const body = [top];
  if (a.reply_text) body.push(el("div", { class: "reply" }, a.reply_text));
  const why = [a.reason, a.error ? `Error: ${a.error}` : ""].filter(Boolean).join(" · ");
  if (why) body.push(el("div", { class: "why" }, why));
  return el("div", { class: "item" }, body);
}

function renderActivity() {
  const recent = state.actions.slice(0, 60);
  $("recent").replaceChildren(...(recent.length ? recent.map(actionItem) : [el("div", { class: "empty" }, "No changes yet")]));
  $("runs").replaceChildren(...(state.runs.length ? state.runs.map((r) => el("tr", {},
    el("td", {}, new Date(r.started_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })),
    el("td", {}, r.trigger === "schedule" ? "Hourly" : "Manual"),
    el("td", {}, r.error ? "–" : (r.tasks_worked || []).length),
    el("td", {}, r.error ? "–" : r.systems_connected || 0),
    el("td", { class: (r.error || (r.errors || []).length) ? "err" : "" },
      r.error || (r.errors || []).map((e) => `${e.space}: ${e.error}`).join("; ") || "–",
      r.trace ? el("details", {}, el("summary", {}, "Technical details"),
        el("pre", { style: "white-space:pre-wrap;font-size:11px;max-height:260px;overflow:auto" }, r.trace)) : null)))
    : [el("tr", {}, el("td", { colspan: "5", class: "empty" }, "No runs yet"))]));
}

// -- Settings -----------------------------------------------------------------------------
function renderSettings() {
  const tiers = $("tiers"); tiers.replaceChildren();
  for (const [key, label] of kb.autonomy_categories) {
    const current = state.settings.autonomy[key] || "ask";
    const seg = el("div", { class: "seg", role: "group", "aria-label": label },
      ...["auto", "ask"].map((v) => el("button", { type: "button", class: v === current ? "on" : "",
        onclick: async () => {
          try {
            state.settings = (await api("PATCH", "/api/settings", { autonomy: { [key]: v } })).settings;
            renderSettings();
          } catch (e) { toast(e.message); }
        } }, v === "auto" ? "Auto" : "Ask")));
    tiers.append(el("span", {}, label), seg);
  }
  $("partnersEnabled").checked = state.settings.partners_enabled !== false;
  $("openaiModel").value = state.settings.openai_model || "";
}

$("openSettings").onclick = () => { renderSettings(); $("partnerResults").textContent = ""; $("settingsDialog").showModal(); };
$("changePassword").onclick = async () => {
  const value = $("newPassword").value.trim();
  if (!value) { $("newPassword").focus(); return; }
  if (!confirm("Change the password? Everyone else is signed out and needs the new one.")) return;
  try {
    await api("POST", "/api/password", { password: value });
    $("newPassword").value = "";
    toast("Password changed. Everyone else is signed out.");
  } catch (e) { toast(e.message); }
};
$("closeSettings").onclick = () => $("settingsDialog").close();
$("partnersEnabled").addEventListener("change", async (e) => {
  try { state.settings = (await api("PATCH", "/api/settings", { partners_enabled: e.target.checked })).settings; refreshKnowledge(); }
  catch (err) { e.target.checked = !e.target.checked; toast(err.message); }
});
$("openaiModel").addEventListener("change", async (e) => {
  try { state.settings = (await api("PATCH", "/api/settings", { openai_model: e.target.value })).settings; toast("Saved"); }
  catch (err) { toast(err.message); }
});
$("testPartners").onclick = async () => {
  const out = $("partnerResults"); out.replaceChildren(el("span", { class: "spinner" }), " testing…");
  try {
    const { results } = await api("POST", "/api/partners/test");
    out.textContent = kb.partners.map((p) => [p, results[p.name]]).filter(([, r]) => r)
      .map(([p, r]) => `${p.label}: ${r.ok ? "working" : r.detail}`).join(" · ");
    refreshKnowledge();  // the test also updates the partners' rows on the Access tab
  } catch (e) { out.textContent = e.message; }
};

$("autoAct").addEventListener("change", async (e) => {
  try {
    const { settings } = await api("PATCH", "/api/settings", { auto_act: e.target.checked });
    state.settings = settings;
    toast(settings.auto_act ? "The assistant will act on its own (within your autonomy settings)" : "Paused: everything waits for your approval");
  } catch (err) { e.target.checked = !e.target.checked; toast(err.message); }
});

// -- live progress of a run (manual or hourly) ---------------------------------------
const STALE_MS = 10 * 60 * 1000;  // a "running" record this old means the run died
let pollTimer = null;
let ownRun = false;               // true while this tab's Run now request is open

const isLive = (p) => !!(p && p.running && p.updated_at && Date.now() - new Date(p.updated_at).getTime() < STALE_MS);

function renderProgress(p) {
  const box = $("progress");
  if (!isLive(p)) { box.hidden = true; return; }
  const head = el("div", { class: "line1" },
    el("span", { class: "spinner", "aria-hidden": "true" }),
    el("span", { class: "phase" }, `${p.phase}…`),
    el("span", { class: "eta" }, p.trigger === "schedule" ? "hourly run" : ""));
  const bar = el("div", { class: "bar indeterminate", role: "progressbar", "aria-label": "Run progress" }, el("div", {}));
  box.replaceChildren(head, bar);
  box.hidden = false;
}

function setRunning(on) {
  const btn = $("runNow"); btn.disabled = on; btn.textContent = on ? "Running…" : "Run now";
}

async function pollProgress() {
  let p;
  try { p = await api("GET", "/api/progress"); } catch { return; }
  renderProgress(p);
  const live = isLive(p);
  setRunning(live || ownRun);
  if (live && !pollTimer) pollTimer = setInterval(pollProgress, 2000);
  if (!live && pollTimer) {
    clearInterval(pollTimer); pollTimer = null;
    if (!ownRun) {  // an hourly run (or another tab's) just ended: show its results
      await load().catch(() => {});
      if (p.finished_at) toast(p.error ? `Run failed: ${p.error}` : "Run finished");
    }
  }
}

$("runNow").onclick = async () => {
  ownRun = true; setRunning(true);
  if (!pollTimer) pollTimer = setInterval(pollProgress, 2000);
  setTimeout(pollProgress, 700);
  try {
    const r = await api("POST", "/api/run");
    toast(`Run finished: ${plural((r.tasks_worked || []).length, "task")} worked`);
  } catch (e) {
    toast(/already in progress/.test(e.message) ? "A run is already going. Showing its progress." : e.message);
  }
  await pollProgress();  // hides the panel once the run is over
  ownRun = false;
  setRunning(!!pollTimer);
  load().catch((e) => toast(e.message));
};

$("logout").onclick = async () => { await api("POST", "/logout").catch(() => {}); location.reload(); };

// -- Browser: watch the AI's browser ---------------------------------------------------------
const LIVE_MS = 30 * 1000;  // a screen this fresh means the AI is at it now
let browserViews = [];
const isLiveView = (v) => !v.closed && Date.now() - new Date(v.at).getTime() < LIVE_MS;

function renderBrowser() {
  $("browserLive").hidden = !browserViews.some(isLiveView);
  const box = $("screens");
  if (!browserViews.length) {
    box.replaceChildren(el("div", { class: "empty" }, "The AI hasn't used its browser yet. Ask it to check a page, and watch here."));
    return;
  }
  const keep = new Map([...box.querySelectorAll(".screen")].map((n) => [n.dataset.id, n]));
  box.replaceChildren(...browserViews.map((v) => {
    const old = keep.get(v.id);
    if (old && old.dataset.at === v.at) { old.querySelector(".when").textContent = ago(v.at); return old; }  // no flicker
    const link = safeUrl(v.url);
    return el("div", { class: `screen${v.device === "mobile" ? " phone" : ""}`, "data-id": v.id, "data-at": v.at },
      el("div", { class: "top" },
        el("b", {}, partnerInfo((v.caller || "").split(" ")[0]).label || v.caller),
        /\((task|voice)\)/.test(v.caller || "") ? el("span", {}, /task/.test(v.caller) ? "· working a task" : "· on a call") : null,
        isLiveView(v) ? el("span", { class: "live" }, "live") : el("span", {}, v.closed ? "closed" : "idle"),
        el("span", {}, `· ${v.action}`),
        el("span", { class: "when" }, ago(v.at)),
        link ? el("a", { href: link, target: "_blank", rel: "noopener" }, v.title || v.url) : el("span", {}, v.title || "")),
      v.image ? el("img", { src: `data:image/jpeg;base64,${v.image}`, alt: `Screenshot of ${v.title || v.url || "the page"}` }) : null,
      v.error ? el("div", { class: "err" }, v.error) : null);
  }));
}

let browserTimer = null;
async function pollBrowser() {
  clearTimeout(browserTimer);
  try { browserViews = (await api("GET", "/api/browser")).views || []; renderBrowser(); } catch { /* next time */ }
  const watching = !$("view-browser").hidden && document.visibilityState === "visible";
  browserTimer = setTimeout(pollBrowser, watching ? 2000 : 20000);  // every 2 s while watched; else just the badge
}

// -- tabs ---------------------------------------------------------------------------------
const TABS = ["board", "projects", "questions", "access", "browser", "activity"];  // the chat isn't a tab: it floats over all of them
function switchTab(name) {
  if (!TABS.includes(name)) name = "board";  // e.g. "talk", remembered from before the chat window
  for (const t of document.querySelectorAll(".tab")) t.classList.toggle("active", t.dataset.tab === name);
  for (const v of TABS) $(`view-${v}`).hidden = v !== name;
  if (name === "browser") pollBrowser();
  try { localStorage.setItem("tab", name); } catch { /* storage unavailable */ }
}
for (const tab of document.querySelectorAll(".tab")) tab.onclick = () => switchTab(tab.dataset.tab);

// Exposed for voice.js
window.companyAssistant = { el, toast, api, renderThread, turnNode, loadTalk, refreshKnowledge, partnerInfo, get partner() { return partner; } };

(async function start() {
  let tab = "board";
  try { tab = localStorage.getItem("tab") || "board"; } catch { /* storage unavailable */ }
  switchTab(tab);
  try { await load(); afterConnectRedirect(); await loadTalk(); await pollProgress(); } catch (e) { $("lastrun").textContent = e.message; }
  if (!browserTimer) pollBrowser();
})();
// Every minute: notice an hourly run starting, and refresh unless a dialog is open or a reply is pending.
setInterval(() => {
  if (!pollTimer) pollProgress();
  if (!document.querySelector("dialog[open]") && !talkBusy) load().catch(() => {});
}, 60000);
