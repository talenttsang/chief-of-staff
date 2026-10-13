// Talent Solutions AI: board, knowledge base, interview, access, and the chat window. Voice lives in voice.js.
"use strict";

const STATUS_ORDER = ["todo", "in_progress", "done"];
const TYPE_LABEL = { system_change: "更改" };
const STATUS_LABEL = { done: "已完成", suggested: "等待中", failed: "失敗", dismissed: "已略過" };
const SYSTEM_GROUPS = [
  ["requested", "助理要求的"], ["needed", "需要的"], ["available", "已有憑證，尚未連接"],
  ["no_access", "Secret Manager 有金鑰，但應用程式無法讀取"], ["error", "找到金鑰，但系統拒絕了"],
  ["connected", "已連接"], ["not_used", "不使用"],
];
// Google permissions in plain words, for the Reconnect banner.
const SCOPE_LABEL = {
  "calendar.events": "你的日曆",
  "gmail.readonly": "讀取 Gmail", "gmail.compose": "草擬電郵", "drive.readonly": "讀取 Drive",
  "spreadsheets.readonly": "讀取 Sheets", "analytics.readonly": "Analytics", "webmasters.readonly": "Search Console",
};
const PROJECT_STATUS = { active: "進行中", blocked: "受阻", paused: "暫停", done: "已完成", unknown: "未知" };
const COMPANY_LABEL = { helperconnects: "HelperConnects", helperxpress: "HelperXpress", both: "兩者" };
const SUGGESTIONS = [
  "訪問我：你最需要知道甚麼？", "到目前為止你對兩間公司知道甚麼？",
  "你最先需要哪些系統的權限？我要怎樣連接？", "我這星期的日曆有甚麼？",
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
const plural = (n, word) => `${n} ${word}`;  // Chinese has no plural: "3 個任務"
const where = (t) => (t.source_from && t.source_from !== t.space_label ? `${t.space_label} · ${t.source_from}` : t.space_label);

async function api(method, path, body) {
  const resp = await fetch(path, {
    method, credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Chat-Assistant": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (resp.status === 401) { location.reload(); throw new Error("已登出"); }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `請求失敗（${resp.status}）`);
  return data;
}

function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.remove("show"), 3800);
}

function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso.split("#")[0]).getTime()) / 1000;
  if (s < 60) return "剛剛";
  if (s < 3600) return `${Math.round(s / 60)} 分鐘前`;
  if (s < 86400) return `${Math.round(s / 3600)} 小時前`;
  return new Date(iso.split("#")[0]).toLocaleDateString("zh-HK", { month: "short", day: "numeric" });
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
    el("span", {}, el("b", {}, "新版本已準備好。"), "完成手上的工作後重新載入即可使用。"),
    el("button", { class: "btn primary", onclick: () => location.reload() }, "重新載入")));
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
  if (!run) { $("lastrun").textContent = "尚未執行。它每小時處理自己的任務，或按「立即執行」。"; return; }
  if (run.error) {
    $("lastrun").textContent = `上次執行（${ago(run.started_at)}）失敗：${run.error}${run.trace ? "（詳情見 活動紀錄 → 執行紀錄）" : ""}`;
    return;
  }
  $("lastrun").textContent = `上次執行 ${ago(run.finished_at || run.started_at)} · `
    + `處理了 ${plural((run.tasks_worked || []).length, "個任務")} · 已連接 ${plural(run.systems_connected || 0, "個系統")}`;
}

function renderBanners() {
  const box = $("banners"); box.replaceChildren();
  if (!state.connected) {
    box.append(el("div", { class: "banner" },
      el("span", {}, el("b", {}, "連接 Google。"), `Talent Solutions AI 以 ${state.google_account || "你的 Google 帳戶"} 讀取 Gmail、日曆和 Drive。請按「連接」，並以該帳戶登入 Google。`),
      el("a", { class: "btn primary", href: "/connect/google" }, "連接")));
  } else if (state.status.connection_error) {
    box.append(el("div", { class: "banner bad" },
      el("span", {}, el("b", {}, "助理無法連接 Google。"), state.status.connection_error),
      el("a", { class: "btn primary", href: "/connect/google" }, "Reconnect")));
  }
  if ((state.status.missing_scopes || []).length) {
    const what = state.status.missing_scopes.map((s) => SCOPE_LABEL[s] || s);
    box.append(el("div", { class: "banner" },
      el("span", {}, el("b", {}, "Talent Solutions AI 需要你在 Google 批准："), what.join("、"),
        "。請按「重新連接」，並允許 Google 要求的所有權限。"),
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
  $("startVoice").title = info.available ? `與 ${info.label} 即時通話` : info.detail || `${info.label} 暫時無法使用`;
  $("message").placeholder = `傳訊息給 ${info.label}…`;
}

function renderReadAloud() {
  const on = !!(speaker() && speaker().auto);
  $("readAloud").setAttribute("aria-pressed", String(on));
  $("readAloud").title = on ? "正在朗讀新回覆，按一下停止" : "朗讀新回覆";
}
$("readAloud").onclick = () => {
  if (!speaker()) return;
  speaker().auto = !speaker().auto;
  renderReadAloud();
  toast(speaker().auto ? "會朗讀新回覆" : "不會朗讀回覆");
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
    ? el("button", { class: "spk", title: "朗讀", "aria-label": "朗讀",
        onclick: (e) => speaker() && speaker().toggle(t.text, who, e.currentTarget) }, "🔊")
    : null;
  return el("div", { class: `msg ${t.role}${live ? " live" : ""}` }, t.text,
    tools.length ? el("div", { class: "tools" }, `使用了：${[...new Set(tools)].join("、")}`) : null,
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
  renderThread([el("div", { class: "msg assistant live" }, el("span", { class: "spinner" }), " 思考中…")]);
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
  if (!confirm(`清除與 ${partnerInfo(partner).label} 的對話？它學到的東西會保留在知識庫。`)) return;
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
  const label = onCall ? "通話中 · Talent Solutions AI" : "Talent Solutions AI";
  $("chatLauncher").replaceChildren(...[onCall ? el("span", { class: "pulse", "aria-hidden": "true" }) : el("span", { "aria-hidden": "true" }, "💬"),
    label, unread && !onCall ? el("span", { class: "badge", title: "新回覆" }) : null].filter(Boolean));
  $("chatLauncher").setAttribute("aria-label", `${label}${unread ? "（有新回覆）" : ""}。開啟對話`);
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
  if (c.kind === "paste") return el("button", { class: "btn primary", onclick: () => openConnect(s) }, "連接");
  const href = c.kind === "google" ? "/connect/google?back=access" : `/connect/setup?then=${encodeURIComponent(s.id)}`;
  return el("a", { class: "btn primary", href }, "連接");
}

let connecting = null;
function openConnect(s) {
  connecting = s;
  $("connectTitle").textContent = `連接 ${s.name}`;
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
  $("saveConnect").disabled = true; $("saveConnect").textContent = "連接中…";
  try {
    const resp = await fetch(`/api/connect/${encodeURIComponent(s.connect.integration)}`, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Chat-Assistant": "1" }, body: JSON.stringify({ values }),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.status === 409 && data.needs_setup) {  // the one owner approval hasn't happened yet
      toast("請先在 Google 批准一次，然後再貼上。");
      location.href = data.url;
      return;
    }
    if (!resp.ok) throw new Error(data.error || `無法連接（${resp.status}）`);
    $("connectDialog").close();
    toast(data.result && data.result.ok ? `${s.name} 已連接` : `已儲存，但 ${s.name} 回覆：${(data.result || {}).detail || "沒有回應"}`);
    refreshKnowledge();
  } catch (err) { toast(err.message); }
  finally { $("saveConnect").disabled = false; $("saveConnect").textContent = "連接"; }
};

// Try each system with its key (the server does it; keys never reach the page). Right after the owner approval,
// Google can take a minute to apply new access, so "can't read" results are retried a few times.
async function checkConnections(retries = 0) {
  const button = $("checkSystems");
  button.disabled = true; button.textContent = "檢查中…";
  try {
    const { results } = await api("POST", "/api/integrations/check");
    const all = Object.values(results), ok = all.filter((r) => r.ok).length;
    const waiting = all.filter((r) => !r.ok && /無法讀取/.test(r.detail || "")).length;
    refreshKnowledge();
    // Every system that isn't connected, with what it said, so the reason is on screen (and in a screenshot).
    const why = (r) => /無法讀取 \S+ 金鑰/.test(r.detail || "") && /貼上|paste/i.test(r.paste || "") ? "等待金鑰：按「連接」並貼上"
      : /Google 尚未連接|insufficient|scope/i.test(r.detail || "") ? "按「連接」把它加入你的 Google 登入"
      : (r.detail || "沒有回應").slice(0, 160);
    const failing = all.filter((r) => !r.ok).map((r) => `• ${r.system}: ${why(r)}`);
    if (waiting && retries > 0) {
      showSetupBanner(`${all.length} 個系統中 ${ok} 個已連接。Google 仍在為 ${waiting} 個系統套用權限，稍後再檢查…`);
      setTimeout(() => checkConnections(retries - 1), 30000);  // Google can take minutes to apply new access
    } else {
      showSetupBanner(failing.length ? `${all.length} 個系統中 ${ok} 個已連接。尚未連接：\n${failing.join("\n")}` : `全部 ${all.length} 個系統已連接。`);
      toast(`${all.length} 個系統中 ${ok} 個已連接`);
    }
  } catch (e) { toast(e.message); }
  finally { button.disabled = false; button.textContent = "檢查連接"; }
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
    showSetupBanner(errors ? `權限已設定，但有 ${errors} 個問題：${report.errors.join("；")}` : "權限已設定，正在測試每個系統…");
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
  meta.append(el("span", { class: `chip ${t.owner_is_me ? "me" : ""}` }, t.owner_is_me ? "你" : (t.owner || "未指派")));
  if (t.due) {
    const today = todayStr();
    const open = t.status !== "done";
    const cls = open && t.due < today ? "overdue" : open && t.due === today ? "today" : "";
    meta.append(el("span", { class: `chip ${cls}` }, t.due === today ? "今天到期" : `${t.due} 到期`));
  }
  if (t.origin === "assistant") meta.append(el("span", { class: "chip", title: "Added by the assistant" }, "AI"));
  if (working(t)) meta.append(el("span", { class: "chip working" }, el("span", { class: "spinner" }), " 處理中…"));
  else if (t.result) meta.append(el("span", { class: "chip", title: "The assistant has reported on this task" }, "🤖 report"));
  const next = STATUS_ORDER[STATUS_ORDER.indexOf(t.status) + 1];
  const card = el("div", { class: "card", tabindex: "0", role: "button",
      onclick: () => openTask(t), onkeydown: (e) => { if (e.key === "Enter") openTask(t); } },
    el("div", { class: "title" }, t.title), meta,
    t.space_label ? el("div", { class: "src", title: t.source_excerpt || "" }, where(t)) : null);
  if (next) card.append(el("button", { class: "advance", title: next === "done" ? "標示為完成" : "開始", "aria-label": next === "done" ? "標示為完成" : "開始",
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
      status === "done" && !showDone ? "勾選「顯示已完成」查看已完成的任務" : "沒有任務"));
  }
}

// The assistant is on it: marked when work starts, cleared when it reports (15 min cap, as on the server).
const working = (t) => !!t.working_since && Date.now() - new Date(t.working_since).getTime() < 15 * 60 * 1000;

function showTaskReport(t) {
  const box = $("taskResult");
  box.hidden = !(t && (t.result || t.work_error || working(t)));
  if (box.hidden) return;
  if (working(t)) { box.replaceChildren(el("b", {}, "助理正在處理…")); return; }
  box.replaceChildren(el("b", {}, `助理的報告（${ago(t.worked_at)}）`),
    t.work_error ? `出錯停止了：${t.work_error}` : t.result,
    t.work_tools && t.work_tools.length ? el("div", { class: "tools" }, `使用了：${t.work_tools.join("、")}`) : null);
}

$("workTask").onclick = async () => {
  if (!editing) { toast("請先儲存任務，再交給 Talent Solutions AI。"); return; }
  const t = editing;
  $("taskDialog").close();
  state.tasks = state.tasks.map((x) => (x.id === t.id ? { ...x, owner: "Assistant", owner_is_me: false, working_since: new Date().toISOString() } : x));
  renderBoard();
  try {
    await api("POST", `/api/tasks/${encodeURIComponent(t.id)}/work`);
    toast(`Talent Solutions AI 開始處理「${t.title}」。可在「工作進度」看每一步，停下來時會通知你。`);
    pollSteps();
  } catch (e) {
    toast(e.message);
    load().catch(() => {});
  }
  renderBoard();
};

function openTask(t) {
  editing = t || null;
  const f = $("taskForm");
  $("taskDialogTitle").textContent = t ? "編輯任務" : "新任務";
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
    src.append(`來自 ${where(t)}${t.source_excerpt ? `：「${t.source_excerpt}」 ` : " "}`);
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
  if (!editing || !confirm("刪除此任務？")) return;
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
      : "尚未有項目。助理聽到你的回答後會加入，你亦可以自行新增。"));
    return;
  }
  box.replaceChildren(...items.map((p) => el("div", { class: "card", tabindex: "0", role: "button",
      onclick: () => openProject(p), onkeydown: (e) => { if (e.key === "Enter") openProject(p); } },
    el("div", { class: "title" }, p.name),
    el("div", { class: "meta" },
      el("span", { class: `chip ${p.status || ""}` }, PROJECT_STATUS[p.status] || p.status || "未知"),
      p.company ? el("span", { class: "chip" }, COMPANY_LABEL[p.company] || p.company) : null,
      p.owner ? el("span", { class: "chip" }, p.owner) : null,
      p.deadline ? el("span", { class: "chip" }, `${p.deadline} 到期`) : null,
      openTasks(p.id) ? el("span", { class: "chip" }, plural(openTasks(p.id), "open task")) : null,
      null),
    (p.goal || p.summary) ? el("div", { class: "body" }, p.goal || p.summary) : null,
    p.next_steps ? el("div", { class: "body" }, `下一步：${p.next_steps}`) : null)));
}

function openProject(p) {
  editingProject = p || null;
  const f = $("projectForm");
  $("projectDialogTitle").textContent = p ? "編輯項目" : "新項目";
  for (const k of ["name", "owner", "deadline", "goal", "summary", "next_steps"]) f[k].value = p ? p[k] || "" : "";
  f.company.value = p ? p.company || "" : "";
  f.status.value = p ? p.status || "active" : "active";
  $("deleteProject").hidden = !p;
  $("projectSource").textContent = p && p.source ? `來源：${p.source} · 更新於 ${ago(p.updated_at)}` : "";
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
  if (!editingProject || !confirm("刪除此項目？它的任務會留在工作板上。")) return;
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
    q.digest ? el("div", { class: "why" }, `助理：${q.digest}`) : null,
    el("div", { class: "why" }, `回答於 ${ago(q.answered_at)}`))) : [el("div", { class: "empty" }, "尚未有已回答的問題")]));
}

function questionItem(q) {
  const box = el("textarea", { placeholder: "你的答案（簡短亦可）", "aria-label": "答案" });
  const answerBtn = el("button", { class: "btn primary" }, "回答");
  answerBtn.onclick = async () => {
    const answer = box.value.trim();
    if (!answer) return;
    answerBtn.disabled = true; answerBtn.replaceChildren(el("span", { class: "spinner" }), " 儲存並學習中…");
    try {
      const { note } = await api("POST", `/api/questions/${encodeURIComponent(q.id)}/answer`, { answer });
      toast(note || "已儲存");
      await Promise.all([refreshKnowledge(), load()]);
    } catch (e) { toast(e.message); answerBtn.disabled = false; answerBtn.textContent = "回答"; }
  };
  return el("div", { class: "item" },
    el("div", { class: "top" }, el("span", { class: `pri ${q.priority === 1 ? "high" : q.priority === 3 ? "low" : "medium"}` }),
      el("span", {}, q.priority === 1 ? "重要" : q.priority === 3 ? "有空時" : "一般"),
      q.source ? el("span", {}, `· ${q.source}`) : null),
    el("div", { class: "q" }, q.question),
    q.why ? el("div", { class: "why" }, `原因：${q.why}`) : null,
    box,
    el("div", { class: "actions" }, answerBtn,
      el("button", { class: "btn", onclick: () => askInChat(`我們來談談這個問題 [${q.id}]：${q.question}`) }, "在對話中討論"),
      el("button", { class: "btn", onclick: async () => {
        await api("POST", `/api/questions/${encodeURIComponent(q.id)}/dismiss`).catch((e) => toast(e.message));
        refreshKnowledge();
      } }, "略過")));
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
      const select = el("select", { "aria-label": `${s.name} 的狀態` },
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
        s.why ? el("div", { class: "why" }, status === "no_access" || status === "error" ? s.why : `助理要求的原因：${s.why}`) : null,
        status !== "connected" && status !== "not_used" ? el("div", { class: "actions" },
          s.connect ? connectButton(s) : el("button", { class: "btn",
            onclick: () => askInChat(`我要怎樣給你 ${s.name} 的權限？請一步步教我。`) },
          "如何連接？")) : null);
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
  const why = [a.reason, a.error ? `錯誤：${a.error}` : ""].filter(Boolean).join(" · ");
  if (why) body.push(el("div", { class: "why" }, why));
  return el("div", { class: "item" }, body);
}

function renderActivity() {
  const recent = state.actions.slice(0, 60);
  $("recent").replaceChildren(...(recent.length ? recent.map(actionItem) : [el("div", { class: "empty" }, "尚未有更改")]));
  $("runs").replaceChildren(...(state.runs.length ? state.runs.map((r) => el("tr", {},
    el("td", {}, new Date(r.started_at).toLocaleString("zh-HK", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })),
    el("td", {}, r.trigger === "schedule" ? "每小時" : "手動"),
    el("td", {}, r.error ? "–" : (r.tasks_worked || []).length),
    el("td", {}, r.error ? "–" : r.systems_connected || 0),
    el("td", { class: (r.error || (r.errors || []).length) ? "err" : "" },
      r.error || (r.errors || []).map((e) => `${e.space}: ${e.error}`).join("; ") || "–",
      r.trace ? el("details", {}, el("summary", {}, "技術詳情"),
        el("pre", { style: "white-space:pre-wrap;font-size:11px;max-height:260px;overflow:auto" }, r.trace)) : null)))
    : [el("tr", {}, el("td", { colspan: "5", class: "empty" }, "尚未有執行紀錄"))]));
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
        } }, v === "auto" ? "自動" : "詢問")));
    tiers.append(el("span", {}, label), seg);
  }
  $("partnersEnabled").checked = state.settings.partners_enabled !== false;
  $("openaiModel").value = state.settings.openai_model || "";
}

$("openSettings").onclick = () => { renderSettings(); $("partnerResults").textContent = ""; $("settingsDialog").showModal(); };
$("changePassword").onclick = async () => {
  const value = $("newPassword").value.trim();
  if (!value) { $("newPassword").focus(); return; }
  if (!confirm("更改密碼？其他人都會被登出，需要用新密碼登入。")) return;
  try {
    await api("POST", "/api/password", { password: value });
    $("newPassword").value = "";
    toast("密碼已更改，其他人已被登出。");
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
  const out = $("partnerResults"); out.replaceChildren(el("span", { class: "spinner" }), " 測試中…");
  try {
    const { results } = await api("POST", "/api/partners/test");
    out.textContent = kb.partners.map((p) => [p, results[p.name]]).filter(([, r]) => r)
      .map(([p, r]) => `${p.label}：${r.ok ? "正常" : r.detail}`).join(" · ");
    refreshKnowledge();  // the test also updates the partners' rows on the Access tab
  } catch (e) { out.textContent = e.message; }
};

$("autoAct").addEventListener("change", async (e) => {
  try {
    const { settings } = await api("PATCH", "/api/settings", { auto_act: e.target.checked });
    state.settings = settings;
    toast(settings.auto_act ? "助理會自行執行（在你的自主設定範圍內）" : "已暫停：一切都會等你批准");
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
    el("span", { class: "eta" }, p.trigger === "schedule" ? "每小時執行" : ""));
  const bar = el("div", { class: "bar indeterminate", role: "progressbar", "aria-label": "執行進度" }, el("div", {}));
  box.replaceChildren(head, bar);
  box.hidden = false;
}

function setRunning(on) {
  const btn = $("runNow"); btn.disabled = on; btn.textContent = on ? "執行中…" : "立即執行";
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
      if (p.finished_at) toast(p.error ? `執行失敗：${p.error}` : "執行完成");
    }
  }
}

$("runNow").onclick = async () => {
  ownRun = true; setRunning(true);
  if (!pollTimer) pollTimer = setInterval(pollProgress, 2000);
  setTimeout(pollProgress, 700);
  try {
    const r = await api("POST", "/api/run");
    toast(`執行完成：處理了 ${plural((r.tasks_worked || []).length, "個任務")}`);
  } catch (e) {
    toast(/已有一次執行/.test(e.message) ? "已有一次執行正在進行，正在顯示進度。" : e.message);
  }
  await pollProgress();  // hides the panel once the run is over
  ownRun = false;
  setRunning(!!pollTimer);
  load().catch((e) => toast(e.message));
};

$("logout").onclick = async () => { await api("POST", "/logout").catch(() => {}); location.reload(); };

// -- Browser: watch the AI's browser ---------------------------------------------------------
const BROWSER_STEP = { open: "開啟", goto: "前往", click: "點擊", type: "輸入", press: "按鍵", select: "選擇", scroll: "捲動",
  back: "上一頁", forward: "下一頁", reload: "重新載入", screenshot: "截圖", close: "關閉", sign_in: "登入" };
const LIVE_MS = 30 * 1000;  // a screen this fresh means the AI is at it now
let browserViews = [];
const isLiveView = (v) => !v.closed && Date.now() - new Date(v.at).getTime() < LIVE_MS;

function renderBrowser() {
  $("browserLive").hidden = !browserViews.some(isLiveView);
  const box = $("screens");
  if (!browserViews.length) {
    box.replaceChildren(el("div", { class: "empty" }, "AI 尚未使用瀏覽器。請它檢查一個網頁，然後在這裡觀看。"));
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
        /\((task|voice)\)/.test(v.caller || "") ? el("span", {}, /task/.test(v.caller)
          ? `· 處理任務${(state?.tasks || []).find((t) => t.id === v.task_id)?.title ? `：${state.tasks.find((t) => t.id === v.task_id).title}` : "中"}`
          : "· 通話中") : null,
        isLiveView(v) ? el("span", { class: "live" }, "即時") : el("span", {}, v.closed ? "已關閉" : "閒置"),
        el("span", {}, `· ${BROWSER_STEP[v.action] || v.action}`),
        el("span", { class: "when" }, ago(v.at)),
        link ? el("a", { href: link, target: "_blank", rel: "noopener" }, v.title || v.url) : el("span", {}, v.title || "")),
      v.image ? el("img", { src: `data:image/jpeg;base64,${v.image}`, alt: `${v.title || v.url || "網頁"} 的截圖` }) : null,
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

// -- Progress: the AI's work log, step by step ---------------------------------------------------
const STEP_LABEL = {
  task_started: "開始處理任務", task_done: "完成任務", task_paused: "暫停任務", task_failed: "任務出錯",
  task_report: "進度報告（繼續處理）", task_needs_you: "需要你回答", task_interrupted: "工作被中斷",
  browser: "瀏覽器", web_search: "搜尋網頁", read_webpage: "閱讀網頁", call_api: "查詢系統", query_database: "查詢資料庫",
  commit_code: "提交程式碼", search_code: "搜尋程式碼", read_code: "閱讀程式碼", start_task: "交給自己處理", create_task: "新增任務", update_task: "更新任務",
  delete_task: "刪除任務", list_tasks: "查看工作板", save_project: "儲存項目", delete_project: "刪除項目",
  record_fact: "記錄資料", delete_fact: "刪除資料", ask_owner: "提出問題", answer_question: "記錄答案",
  delete_question: "刪除問題", list_questions: "查看問題", search_knowledge: "搜尋知識庫", company_overview: "查看公司概覽",
  list_projects: "查看項目", list_systems: "查看系統", request_access: "要求權限", update_system: "更新系統",
  delete_system: "刪除系統", list_integrations: "查看已連接系統", list_calendar: "查看日曆", consult_partner: "請教 AI 夥伴",
  assistant_status: "查看狀態", set_autopilot: "設定自動執行",
};
const MARKS = new Set(["task_started", "task_done", "task_paused", "task_failed", "task_report", "task_needs_you",
  "task_interrupted"]);
const STOPS = new Set(["task_done", "task_paused", "task_failed", "task_needs_you", "task_interrupted"]);  // a task stopped: tell people
let stepsData = { steps: [], working: [] };

function stepWho(caller) {
  const [name, how] = [(caller || "").split(" ")[0], (caller || "").match(/\((task|voice)\)/)];
  const label = partnerInfo(name).label || caller || "";
  return how ? `${label} · ${how[1] === "task" ? "處理任務中" : "通話中"}` : label;
}

function renderSteps() {
  const { steps, working } = stepsData;
  $("stepsLive").hidden = !working.length;
  const tasks = new Map((state?.tasks || []).map((t) => [t.id, t.title]));
  $("working").replaceChildren(...(working.length ? [el("div", { class: "working" },
    ...working.map((w) => el("div", { class: "row" }, el("span", { class: "live" }, "工作中"),
      el("b", {}, w.title), el("span", { class: "detail" }, `· 由 ${ago(w.since)} 開始`))))] : []));
  if (!steps.length) {
    $("steps").replaceChildren(el("div", { class: "empty" }, "尚未有工作紀錄。把任務交給 Talent Solutions AI，就會在這裡看到每一步。"));
    return;
  }
  $("steps").replaceChildren(...steps.map((s) => {
    const task = !MARKS.has(s.tool) && s.task_id && tasks.get(s.task_id);
    return el("div", { class: `step${s.ok === false ? " bad" : ""}${MARKS.has(s.tool) ? " mark" : ""}` },
      el("span", { class: "when", title: new Date(s.at).toLocaleString("zh-HK") },
        new Date(s.at).toLocaleTimeString("zh-HK", { hour: "2-digit", minute: "2-digit", second: "2-digit" })),
      el("div", {},
        el("div", { class: "what" }, el("b", {}, STEP_LABEL[s.tool] || s.tool),
          s.detail ? el("span", { class: "detail" }, ` · ${s.detail}`) : null),
        el("div", { class: "who" }, stepWho(s.caller), task ? ` · 任務：${task}` : ""),
        s.error ? el("div", { class: "err" }, s.error) : null));
  }));
}

// -- telling people when a task stops: a toast, a count on the tab, and a desktop notification if allowed --------
let seenStep = null;   // the newest stop this browser has told about
let unseenStops = 0;
try { seenStep = localStorage.getItem("seenStep"); } catch { /* storage unavailable */ }

function stopText(s) {
  const head = { task_done: "✅ 完成", task_needs_you: "❓ 需要你", task_paused: "⏸ 暫停", task_failed: "⚠️ 出錯",
    task_interrupted: "🔄 中斷" }[s.tool];
  return `${head}：${s.detail || ""}`.slice(0, 220);
}

function announceStops(steps) {
  const stops = steps.filter((s) => STOPS.has(s.tool));
  if (!stops.length) return;
  const newest = stops[0].at;
  if (seenStep === null) { seenStep = newest; }  // first visit: nothing old to announce
  const fresh = stops.filter((s) => s.at > seenStep);
  if (!fresh.length) return;
  seenStep = newest;
  try { localStorage.setItem("seenStep", newest); } catch { /* storage unavailable */ }
  if ($("view-steps").hidden) unseenStops += fresh.length;
  $("stepsCount").textContent = unseenStops; $("stepsCount").hidden = !unseenStops;
  toast(fresh.length === 1 ? stopText(fresh[0]) : `${fresh.length} 個任務已停下來，請看「工作進度」。`);
  if ("Notification" in window && Notification.permission === "granted" && document.visibilityState !== "visible") {
    for (const s of fresh.slice(0, 3)) new Notification("Talent Solutions AI", { body: stopText(s), tag: s.id });
  }
  load().catch(() => {});  // the board shows the task's new status and report
  if (partner === "assistant") loadTalk().then(() => { if (!chatOpen) { unread = true; renderLauncher(); } }).catch(() => {});
}

function renderNotifyButton() {
  $("enableNotify").hidden = !("Notification" in window) || Notification.permission !== "default";
}
$("enableNotify").onclick = async () => { await Notification.requestPermission(); renderNotifyButton(); };
renderNotifyButton();

let stepsTimer = null;
async function pollSteps() {
  clearTimeout(stepsTimer);
  try { stepsData = await api("GET", "/api/steps"); renderSteps(); announceStops(stepsData.steps); } catch { /* next time */ }
  const watching = !$("view-steps").hidden && document.visibilityState === "visible";
  stepsTimer = setTimeout(pollSteps, watching ? 3000 : 20000);  // every 3 s while watched; else just the badge
}

// -- tabs ---------------------------------------------------------------------------------
const TABS = ["board", "projects", "questions", "access", "steps", "browser", "activity"];  // the chat isn't a tab: it floats over all of them
function switchTab(name) {
  if (!TABS.includes(name)) name = "board";  // e.g. "talk", remembered from before the chat window
  for (const t of document.querySelectorAll(".tab")) t.classList.toggle("active", t.dataset.tab === name);
  for (const v of TABS) $(`view-${v}`).hidden = v !== name;
  if (name === "browser") pollBrowser();
  if (name === "steps") { pollSteps(); unseenStops = 0; $("stepsCount").hidden = true; }
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
  if (!stepsTimer) pollSteps();
})();
// Every minute: notice an hourly run starting, and refresh unless a dialog is open or a reply is pending.
setInterval(() => {
  if (!pollTimer) pollProgress();
  if (!document.querySelector("dialog[open]") && !talkBusy) load().catch(() => {});
}, 60000);
