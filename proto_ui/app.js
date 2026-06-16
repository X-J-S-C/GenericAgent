/**
 * GenericAgent — proto_ui app.js
 * Data-driven SPA connecting to FastAPI backend.
 * Views: chat | conductor | hive | morph | memory | settings
 */

"use strict";

// ─── App State ─────────────────────────────────────────────────────────────────
const state = {
  currentView: "chat",
  ws: null,
  wsTaskId: null,
  wsStatus: "offline",
  currentTaskId: null,
  isAgentRunning: false,
  messages: [],
  llmList: [],
  activeLlmNo: 0,
  conductorAgents: [],
  hiveNotes: [],
  // 新增：记忆/会话/设置缓存
  memoryCache: null,
  settingsCache: null,
  sessionsCache: [],
  memoryTab: "L1",
};

// ─── API Helpers ────────────────────────────────────────────────────────────────
const API_BASE = "";

async function apiGET(path) {
  const r = await fetch(API_BASE + path);
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}`);
  return r.json();
}

async function apiPOST(path, body) {
  const r = await fetch(API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`POST ${path} → ${r.status}: ${txt}`);
  }
  return r.json();
}

async function apiPUT(path, body) {
  const r = await fetch(API_BASE + path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`PUT ${path} → ${r.status}: ${txt}`);
  }
  return r.json();
}

async function apiDELETE(path) {
  const r = await fetch(API_BASE + path, { method: "DELETE" });
  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`DELETE ${path} → ${r.status}: ${txt}`);
  }
  return r.json();
}

// ─── WebSocket ─────────────────────────────────────────────────────────────────
function wsConnect(taskId) {
  if (state.ws) { state.ws.close(); state.ws = null; }

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${location.host}/ws/${taskId}`;
  const ws = new WebSocket(url);
  state.ws = ws;
  state.wsTaskId = taskId;
  setWsStatus("connecting");

  ws.addEventListener("open", () => {
    setWsStatus("online");
    ws.send(JSON.stringify({ type: "subscribe" }));
  });

  ws.addEventListener("message", (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    handleWsMessage(msg);
  });

  ws.addEventListener("close", () => {
    setWsStatus("offline");
    state.ws = null;
    state.wsTaskId = null;
  });

  ws.addEventListener("error", () => {
    setWsStatus("offline");
  });

  const pingInterval = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "ping" }));
    } else {
      clearInterval(pingInterval);
    }
  }, 15000);
}

function wsSend(obj) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(obj));
  }
}

function handleWsMessage(msg) {
  switch (msg.type) {
    case "subscribed": break;
    case "next":
      appendAgentChunk(msg.content, "agent");
      break;
    case "tool_start":
      markLastToolStart(msg.tool_call);
      break;
    case "tool_end":
      markLastToolEnd(msg.tool_call, msg.success);
      break;
    case "done":
      finalizeAgentResponse(msg.content, msg.source || "agent");
      setAgentRunning(false);
      break;
    case "error":
      appendAgentMessage(`**错误**: ${msg.message}`, "system");
      setAgentRunning(false);
      break;
    case "abort_ack":
      setAgentRunning(false);
      break;
    case "pong": break;
  }
}

// ─── Chat Core ─────────────────────────────────────────────────────────────────
async function sendMessage(text) {
  if (!text.trim() || state.isAgentRunning) return;
  const input = document.getElementById("chat-input");
  input.value = "";
  autoResizeInput(input);
  appendUserMessage(text);
  setAgentRunning(true);
  try {
    const { task_id } = await apiPOST("/api/chat", { message: text, source: "user" });
    state.currentTaskId = task_id;
    wsConnect(task_id);
  } catch (err) {
    appendAgentMessage(`**网络错误**: ${err.message}`, "system");
    setAgentRunning(false);
  }
}

function appendUserMessage(text) {
  const id = genId();
  const time = Date.now();
  state.messages.push({ id, role: "user", content: text, time });
  renderMessages();
  scrollChatToBottom();
}

function appendAgentChunk(text, source) {
  const last = state.messages[state.messages.length - 1];
  if (last && last.role === "agent" && !last.done) {
    last.content += text;
  } else {
    state.messages.push({ id: genId(), role: "agent", content: text, time: Date.now(), done: false, toolCalls: [] });
  }
  renderMessages();
  scrollChatToBottom();
}

function appendAgentMessage(text, source) {
  state.messages.push({ id: genId(), role: source || "agent", content: text, time: Date.now(), done: true, toolCalls: [] });
  renderMessages();
  scrollChatToBottom();
}

function finalizeAgentResponse(content, source) {
  const last = state.messages[state.messages.length - 1];
  if (last && last.role === "agent" && !last.done) {
    last.content = content;
    last.done = true;
  } else {
    state.messages.push({ id: genId(), role: "agent", content, time: Date.now(), done: true, toolCalls: [] });
  }
  setAgentRunning(false);
  renderMessages();
  scrollChatToBottom();
}

function markLastToolStart(toolCall) {
  const last = state.messages[state.messages.length - 1];
  if (last && last.role === "agent") {
    last.toolCalls = last.toolCalls || [];
    last.toolCalls.push({ ...toolCall, status: "running" });
  }
}

function markLastToolEnd(toolCall, success) {
  const last = state.messages[state.messages.length - 1];
  if (last && last.toolCalls) {
    const tc = last.toolCalls.find(t => t.name === toolCall.name);
    if (tc) { tc.status = success ? "done" : "error"; tc.result = toolCall.result; }
  }
}

// ─── Rendering ──────────────────────────────────────────────────────────────────
function renderMessages() {
  const container = document.getElementById("chat-messages");
  const empty = document.getElementById("chat-empty");
  if (state.messages.length === 0) {
    empty.style.display = "";
    Array.from(container.children).forEach(el => {
      if (el.id !== "chat-empty") el.remove();
    });
    return;
  }
  empty.style.display = "none";
  const existingIds = new Set(
    Array.from(container.querySelectorAll(".msg")).map(el => el.dataset.id)
  );
  const currentIds = new Set(state.messages.map(m => m.id));
  if (existingIds.size !== currentIds.size) {
    rebuildMessageList(container);
    return;
  }
  state.messages.forEach(m => {
    const el = container.querySelector(`[data-id="${m.id}"]`);
    if (!el) return;
    const contentEl = el.querySelector(".msg-content");
    if (contentEl) contentEl.textContent = m.content;
    renderToolBadges(el, m.toolCalls);
  });
}

function rebuildMessageList(container) {
  Array.from(container.children).forEach(el => { if (el.id !== "chat-empty") el.remove(); });
  state.messages.forEach(m => {
    container.appendChild(buildMessageEl(m));
  });
}

function buildMessageEl(m) {
  const div = document.createElement("div");
  div.className = `msg ${m.role}`;
  div.dataset.id = m.id;
  const avatar = document.createElement("div");
  avatar.className = "msg-avatar";
  avatar.textContent = m.role === "user" ? "U" : "GA";
  const body = document.createElement("div");
  body.className = "msg-body";
  const content = document.createElement("div");
  content.className = "msg-content";
  content.textContent = m.content;
  const time = document.createElement("div");
  time.className = "msg-time";
  time.textContent = formatTime(m.time);
  body.appendChild(content);
  if (m.toolCalls && m.toolCalls.length > 0) renderToolBadges(body, m.toolCalls);
  body.appendChild(time);
  div.appendChild(avatar);
  div.appendChild(body);
  return div;
}

function renderToolBadges(container, toolCalls) {
  container.querySelectorAll(".tool-call-badge").forEach(b => b.remove());
  if (!toolCalls || toolCalls.length === 0) return;
  toolCalls.forEach(tc => {
    const badge = document.createElement("span");
    badge.className = "tool-call-badge";
    badge.innerHTML = `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M22 14h-2V7a2 2 0 0 0-2-2h-6l-2-3H6a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2z"/></svg> ${tc.name} ${tc.status === "running" ? "⟳" : tc.status === "done" ? "✓" : "✗"}`;
    badge.title = JSON.stringify(tc.args, null, 2);
    badge.addEventListener("click", () => showToolCard(tc));
    container.appendChild(badge);
  });
}

function showToolCard(tc) {
  const card = document.getElementById("tool-card");
  document.getElementById("tool-name").textContent = tc.name;
  document.getElementById("tool-args").textContent = JSON.stringify(tc.args, null, 2);
  card.classList.remove("hidden");
}

function scrollChatToBottom() {
  const container = document.getElementById("chat-messages");
  container.scrollTop = container.scrollHeight;
}

// ─── View Switching ────────────────────────────────────────────────────────────
function switchView(name) {
  state.currentView = name;
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  document.getElementById(`view-${name}`)?.classList.add("active");
  document.querySelectorAll(".view-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.view === name);
  });
  if (name === "chat") {
    document.getElementById("chat-input").focus();
  }
  if (name === "memory") loadMemory();
  if (name === "settings") loadSettingsView();
}

// ─── Memory Panel Tabs ─────────────────────────────────────────────────────────
function switchMemoryTab(tabName) {
  state.memoryTab = tabName;
  document.querySelectorAll(".memory-tab").forEach(t => {
    t.classList.toggle("active", t.dataset.mtab === tabName);
  });
  document.querySelectorAll(".memory-panel").forEach(p => {
    p.classList.toggle("active", p.dataset.mpanel === tabName);
  });
}

// ─── Memory: Load & Render ─────────────────────────────────────────────────────
async function loadMemory() {
  try {
    const data = await apiGET("/api/memory");
    state.memoryCache = data;
    renderMemoryAll(data);
  } catch (err) {
    console.error("loadMemory error:", err);
    const el = document.getElementById("mem-L2");
    if (el) el.value = `加载失败: ${err.message}`;
  }
}

function renderMemoryAll(data) {
  // L1 — 工作记忆 (key_info)
  const l1El = document.getElementById("mem-L1");
  if (l1El) {
    l1El.value = (data.working && data.working.key_info) || "";
  }

  // L2 — 全局记忆 (global_mem.txt)
  const l2El = document.getElementById("mem-L2");
  if (l2El) {
    l2El.value = (data.global && data.global.content) || "";
  }

  // insights — 洞察
  const insEl = document.getElementById("mem-insights");
  if (insEl) {
    insEl.value = (data.insights && data.insights.content) || "";
  }

  // L3 — 技能库（列表）
  renderSkillList(data.skills || []);
  const skillsMeta = document.getElementById("skills-meta");
  if (skillsMeta) skillsMeta.textContent = `共 ${data.skills ? data.skills.length : 0} 个 SOP / 脚本文件`;

  // sessions — L0 会话列表
  renderSessionList(data.sessions || []);
  const sessionsMeta = document.getElementById("sessions-meta");
  if (sessionsMeta) sessionsMeta.textContent = `共 ${data.sessions ? data.sessions.length : 0} 个历史会话文件`;
}

// ─── L3 Skill List ─────────────────────────────────────────────────────────────
function renderSkillList(skills) {
  const container = document.getElementById("mem-L3");
  if (!container) return;
  if (!skills || skills.length === 0) {
    container.innerHTML = `<div class="empty-sub" style="padding:16px;">暂无技能文件</div>`;
    return;
  }
  container.innerHTML = "";
  skills.forEach(sk => {
    const item = document.createElement("div");
    item.className = "skill-item";
    item.dataset.path = sk.path || "";
    const title = document.createElement("div");
    title.className = "skill-item-title";
    title.textContent = sk.title || "(untitled)";
    const meta = document.createElement("div");
    meta.className = "skill-item-meta";
    const metaParts = [];
    if (sk.size != null) metaParts.push(`${(sk.size / 1024).toFixed(1)} KB`);
    if (sk.layer) metaParts.push(`层级: ${sk.layer}`);
    meta.textContent = metaParts.join(" · ");
    const actions = document.createElement("div");
    actions.className = "skill-item-actions";
    const previewBtn = document.createElement("button");
    previewBtn.className = "btn btn-ghost btn-sm";
    previewBtn.textContent = "查看";
    previewBtn.addEventListener("click", () => showSkillPreview(sk));
    const delBtn = document.createElement("button");
    delBtn.className = "btn btn-ghost btn-sm";
    delBtn.style.color = "var(--danger)";
    delBtn.textContent = "删除";
    delBtn.addEventListener("click", () => confirmDeleteMemory(sk.path, sk.title));
    actions.appendChild(previewBtn);
    actions.appendChild(delBtn);
    item.appendChild(title);
    item.appendChild(meta);
    item.appendChild(actions);
    container.appendChild(item);
  });
}

function showSkillPreview(sk) {
  const title = sk.title || "";
  const preview = sk.preview || "(无法预览，文件内容过长或二进制)";
  alert(`【${title}】\n\n路径: ${sk.path}\n\n预览:\n${preview}`);
}

// ─── Session List ───────────────────────────────────────────────────────────────
function renderSessionList(sessions) {
  const container = document.getElementById("mem-L0");
  if (!container) return;
  if (!sessions || sessions.length === 0) {
    container.innerHTML = `<div class="empty-sub" style="padding:16px;">暂无会话记录（来源: memory/L4_raw_sessions/）</div>`;
    return;
  }
  container.innerHTML = "";
  sessions.forEach(sess => {
    const item = document.createElement("div");
    item.className = "session-item";
    const title = document.createElement("div");
    title.className = "session-item-title";
    title.textContent = sess.session_id || "(unnamed)";
    const meta = document.createElement("div");
    meta.className = "session-item-meta";
    const parts = [];
    if (sess.size != null) parts.push(`${(sess.size / 1024).toFixed(1)} KB`);
    if (sess.mtime_iso) parts.push(sess.mtime_iso);
    meta.textContent = parts.join(" · ");
    const actions = document.createElement("div");
    actions.className = "skill-item-actions";
    const delBtn = document.createElement("button");
    delBtn.className = "btn btn-ghost btn-sm";
    delBtn.style.color = "var(--danger)";
    delBtn.textContent = "删除";
    delBtn.addEventListener("click", () => confirmDeleteSession(sess.session_id));
    actions.appendChild(delBtn);
    item.appendChild(title);
    item.appendChild(meta);
    item.appendChild(actions);
    container.appendChild(item);
  });
}

// ─── Delete Actions ────────────────────────────────────────────────────────────
function confirmDeleteSession(sessionId) {
  if (!confirm(`确认删除会话 "${sessionId}" ?`)) return;
  const safeId = encodeURIComponent(sessionId);
  apiDELETE(`/api/sessions/${safeId}`)
    .then(() => {
      // 从当前缓存移除并重绘
      if (state.memoryCache && state.memoryCache.sessions) {
        state.memoryCache.sessions = state.memoryCache.sessions.filter(s => s.session_id !== sessionId);
        renderSessionList(state.memoryCache.sessions);
        const sessionsMeta = document.getElementById("sessions-meta");
        if (sessionsMeta) sessionsMeta.textContent = `共 ${state.memoryCache.sessions.length} 个历史会话文件`;
      }
      setPanelStatus("sessions-meta", `已删除: ${sessionId}`);
    })
    .catch(err => alert(`删除失败: ${err.message}`));
}

function confirmDeleteMemory(path, title) {
  if (!confirm(`确认删除记忆文件 "${title || path}" ?\n路径: ${path}`)) return;
  const encoded = encodeURIComponent(path).replace(/%2F/g, "/");
  apiDELETE(`/api/memory/${encoded}`)
    .then(() => {
      if (state.memoryCache && state.memoryCache.skills) {
        state.memoryCache.skills = state.memoryCache.skills.filter(s => s.path !== path);
        renderSkillList(state.memoryCache.skills);
        const skillsMeta = document.getElementById("skills-meta");
        if (skillsMeta) skillsMeta.textContent = `共 ${state.memoryCache.skills.length} 个 SOP / 脚本文件`;
      }
      setPanelStatus("skills-meta", `已删除: ${title || path}`);
    })
    .catch(err => alert(`删除失败: ${err.message}`));
}

// ─── Save Memory (L1/L2/Insights) ─────────────────────────────────────────────
async function saveL2Global() {
  const el = document.getElementById("mem-L2");
  const content = el ? el.value : "";
  setPanelStatus("L2-status", "保存中...");
  try {
    await apiPUT("/api/memory/global", { content });
    setPanelStatus("L2-status", `✓ 已保存到 global_mem.txt (${content.length} 字符)`);
  } catch (err) {
    setPanelStatus("L2-status", `✗ 保存失败: ${err.message}`);
  }
}

async function saveL1Working() {
  const el = document.getElementById("mem-L1");
  const content = el ? el.value : "";
  setPanelStatus("L1-status", "保存中...");
  try {
    // L1 写入通过 add-item 接口（选择 L1 层）
    const res = await apiPOST("/api/memory/add-item", {
      layer: "L1",
      title: `key_info_${new Date().toISOString().slice(0, 16)}`,
      content: content,
    });
    setPanelStatus("L1-status", `✓ 已保存: ${res.path || "(写入 temp 目录)"}`);
  } catch (err) {
    setPanelStatus("L1-status", `✗ 保存失败: ${err.message}`);
  }
}

async function addInsight() {
  const titleEl = document.getElementById("insight-title");
  const contentEl = document.getElementById("insight-content");
  const title = (titleEl ? titleEl.value : "").trim() || "新增洞察";
  const content = (contentEl ? contentEl.value : "").trim();
  if (!content) { alert("请输入洞察内容"); return; }
  const full = `## ${title}\n${content}\n`;
  setPanelStatus("insight-status", "追加中...");
  try {
    await apiPOST("/api/memory/insights", { content: full });
    if (titleEl) titleEl.value = "";
    if (contentEl) contentEl.value = "";
    // 刷新
    const data = await apiGET("/api/memory");
    state.memoryCache = data;
    if (data.insights && document.getElementById("mem-insights")) {
      document.getElementById("mem-insights").value = data.insights.content || "";
    }
    setPanelStatus("insight-status", "✓ 已追加到 global_mem_insight.txt");
  } catch (err) {
    setPanelStatus("insight-status", `✗ 追加失败: ${err.message}`);
  }
}

// ─── Add Memory Item (通用) ───────────────────────────────────────────────────
function openAddMemoryModal() {
  document.getElementById("add-mem-title").value = "";
  document.getElementById("add-mem-content").value = "";
  document.getElementById("add-mem-layer").value = "L1";
  document.getElementById("add-mem-status").textContent = "";
  document.getElementById("add-memory-modal").classList.remove("hidden");
}

async function submitAddMemory() {
  const layer = document.getElementById("add-mem-layer").value;
  const title = document.getElementById("add-mem-title").value.trim();
  const content = document.getElementById("add-mem-content").value.trim();
  if (!content) { alert("请填写内容"); return; }
  setPanelStatus("add-mem-status", "保存中...");
  try {
    const res = await apiPOST("/api/memory/add-item", {
      layer: layer,
      title: title || `untitled_${Date.now()}`,
      content: content,
    });
    setPanelStatus("add-mem-status", `✓ 已保存 (${layer})`);
    document.getElementById("add-memory-modal").classList.add("hidden");
    // 刷新
    loadMemory();
  } catch (err) {
    setPanelStatus("add-mem-status", `✗ 保存失败: ${err.message}`);
  }
}

// ─── Add Skill (L3 SOP) ───────────────────────────────────────────────────────
function openAddSkillModal() {
  document.getElementById("skill-title").value = "";
  document.getElementById("skill-content").value = "";
  document.getElementById("skill-status").textContent = "";
  document.getElementById("add-skill-modal").classList.remove("hidden");
}

async function submitAddSkill() {
  const title = document.getElementById("skill-title").value.trim();
  const content = document.getElementById("skill-content").value.trim();
  if (!title || !content) { alert("请填写标题和内容"); return; }
  setPanelStatus("skill-status", "保存中...");
  try {
    // 复用 add-item，选择 L3
    await apiPOST("/api/memory/add-item", { layer: "L3", title, content });
    setPanelStatus("skill-status", `✓ 已添加到 L3 技能库`);
    document.getElementById("add-skill-modal").classList.add("hidden");
    loadMemory();
  } catch (err) {
    setPanelStatus("skill-status", `✗ 保存失败: ${err.message}`);
  }
}

// ─── Panel Status Helper ───────────────────────────────────────────────────────
function setPanelStatus(elementId, text) {
  const el = document.getElementById(elementId);
  if (el) {
    el.textContent = text;
    clearTimeout(el._timer);
    el._timer = setTimeout(() => { el.textContent = ""; }, 4000);
  }
}

// ─── Settings View ─────────────────────────────────────────────────────────────
async function loadSettingsView() {
  // 加载 LLM 列表
  const llmListEl = document.getElementById("llm-list");
  llmListEl.innerHTML = '<div class="llm-item skeleton"><div class="skeleton-text w-60"></div><div class="skeleton-text w-40"></div></div>';
  try {
    const [status, llms] = await Promise.all([
      apiGET("/api/status"),
      apiGET("/api/llms"),
    ]);
    state.llmList = llms;
    state.activeLlmNo = status.llm_no;
    renderLlmList(llms, status.llm_no);
  } catch {
    llmListEl.innerHTML = '<div class="llm-item"><span class="text-danger">加载失败</span></div>';
  }

  // 加载其他设置
  try {
    const settings = await apiGET("/api/settings");
    state.settingsCache = settings;
    const wm = document.getElementById("setting-work-mem");
    if (wm) wm.value = settings.work_mem_size != null ? settings.work_mem_size : 10;
    const mt = document.getElementById("setting-max-turns");
    if (mt) mt.value = settings.max_turns != null ? settings.max_turns : 70;
    const lng = document.getElementById("setting-lang");
    if (lng) lng.value = settings.lang || "zh";
    const vb = document.getElementById("setting-verbose");
    if (vb) vb.checked = !!settings.verbose;
    const io = document.getElementById("setting-inc-out");
    if (io) io.checked = !!settings.inc_out;
  } catch (err) {
    console.error("load settings error:", err);
  }

  const badge = document.getElementById("llm-badge");
  const active = state.llmList.find((l, i) => i === state.activeLlmNo);
  if (active && badge) badge.textContent = active.name;
}

function renderLlmList(llms, activeNo) {
  const container = document.getElementById("llm-list");
  container.innerHTML = "";
  llms.forEach((llm, i) => {
    const item = document.createElement("div");
    item.className = `llm-item${i === activeNo ? " active" : ""}`;
    item.innerHTML = `
      <div>
        <div class="llm-item-name">${llm.name}</div>
        <div class="llm-item-type">${i === activeNo ? "当前" : "备选"}</div>
      </div>
      ${i === activeNo ? '<span class="llm-check">✓</span>' : ""}
    `;
    if (i !== activeNo) {
      item.addEventListener("click", () => switchLlm(i));
    }
    container.appendChild(item);
  });
}

async function switchLlm(index) {
  try {
    await apiPOST("/api/llm/switch", { index });
    state.activeLlmNo = index;
    renderLlmList(state.llmList, index);
    const badge = document.getElementById("llm-badge");
    if (badge) badge.textContent = state.llmList[index]?.name || "";
  } catch (err) {
    alert(`切换失败: ${err.message}`);
  }
}

async function saveSettings() {
  const payload = {
    work_mem_size: parseInt(document.getElementById("setting-work-mem").value, 10) || 10,
    max_turns: parseInt(document.getElementById("setting-max-turns").value, 10) || 70,
    lang: document.getElementById("setting-lang").value || "zh",
    verbose: document.getElementById("setting-verbose").checked,
    inc_out: document.getElementById("setting-inc-out").checked,
  };
  const statusEl = document.getElementById("settings-status");
  if (statusEl) statusEl.textContent = "保存中...";
  try {
    const res = await apiPOST("/api/settings", payload);
    state.settingsCache = res.settings;
    if (statusEl) statusEl.textContent = "✓ 已保存到 temp/settings.json";
    setTimeout(() => { if (statusEl) statusEl.textContent = ""; }, 3500);
  } catch (err) {
    if (statusEl) statusEl.textContent = `✗ 保存失败: ${err.message}`;
  }
}

// ─── Status Helpers ─────────────────────────────────────────────────────────────
function setAgentRunning(val) {
  state.isAgentRunning = val;
  const indicator = document.getElementById("running-indicator");
  const sendBtn = document.getElementById("btn-send");
  const abortBtn = document.getElementById("btn-abort");
  const statusMini = document.querySelector("#agent-status-mini span:last-child");
  if (val) {
    indicator.classList.remove("hidden");
    if (sendBtn) sendBtn.disabled = true;
    if (abortBtn) abortBtn.style.display = "";
    if (statusMini) statusMini.textContent = "运行中";
    const dot = document.querySelector("#agent-status-mini .status-dot");
    if (dot) dot.className = "status-dot running";
  } else {
    indicator.classList.add("hidden");
    if (sendBtn) sendBtn.disabled = false;
    if (abortBtn) abortBtn.style.display = "none";
    if (statusMini) statusMini.textContent = "待机";
    const dot = document.querySelector("#agent-status-mini .status-dot");
    if (dot) dot.className = "status-dot idle";
    if (state.currentTaskId) {
      state.currentTaskId = null;
      if (state.ws) { state.ws.close(); state.ws = null; }
      setWsStatus("offline");
    }
  }
}

function setWsStatus(status) {
  state.wsStatus = status;
  const el = document.getElementById("ws-status");
  const dot = el && el.querySelector(".ws-dot");
  const label = el && el.querySelector(".ws-label");
  if (dot) dot.className = `ws-dot ${status}`;
  if (label) label.textContent = status === "online" ? "已连接" : status === "connecting" ? "连接中…" : "未连接";
}

async function refreshStatus() {
  try {
    const s = await apiGET("/api/status");
    if (s.is_running && !state.isAgentRunning) setAgentRunning(true);
  } catch { /* silent */ }
}

// ─── Utils ─────────────────────────────────────────────────────────────────────
function genId() { return Math.random().toString(36).slice(2, 10); }

function formatTime(ts) {
  const d = new Date(ts);
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

function autoResizeInput(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 120) + "px";
}

// ─── Event Bindings ─────────────────────────────────────────────────────────────
function initEvents() {
  document.querySelectorAll(".view-btn").forEach(btn => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });

  const chatInput = document.getElementById("chat-input");
  chatInput.addEventListener("input", () => {
    autoResizeInput(chatInput);
    const count = document.getElementById("char-count");
    if (count) count.textContent = chatInput.value.length > 0 ? `${chatInput.value.length}` : "";
  });
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(chatInput.value);
    }
  });
  document.getElementById("btn-send").addEventListener("click", () => {
    sendMessage(chatInput.value);
  });
  document.getElementById("btn-abort").addEventListener("click", async () => {
    try { await apiPOST("/api/abort", {}); wsSend({ type: "abort" }); }
    catch { /* already handled */ }
    setAgentRunning(false);
  });
  document.getElementById("btn-clear-chat").addEventListener("click", () => {
    state.messages = [];
    renderMessages();
  });
  document.getElementById("tool-card-close").addEventListener("click", () => {
    document.getElementById("tool-card").classList.add("hidden");
  });
  document.getElementById("setting-dark")?.addEventListener("change", (e) => {
    document.body.classList.toggle("dark", e.target.checked);
  });
  document.getElementById("btn-export-chat")?.addEventListener("click", () => {
    const text = state.messages.map(m => `[${m.role}] ${m.content}`).join("\n\n");
    downloadText("genericagent-chat.txt", text);
  });
  document.getElementById("btn-export-caps")?.addEventListener("click", async () => {
    try {
      const caps = await apiGET("/api/status");
      downloadText("genericagent-caps.json", JSON.stringify({ ...caps, version: "0.2.0" }, null, 2));
    } catch { /* stub */ }
  });
  const dropZone = document.getElementById("morph-drop");
  if (dropZone) {
    dropZone.addEventListener("dragover", e => { e.preventDefault(); dropZone.style.borderColor = "var(--accent)"; });
    dropZone.addEventListener("dragleave", () => { dropZone.style.borderColor = ""; });
    dropZone.addEventListener("drop", e => {
      e.preventDefault();
      dropZone.style.borderColor = "";
      const file = e.dataTransfer.files[0];
      if (file) {
        const reader = new FileReader();
        reader.onload = ev => {
          try {
            const data = JSON.parse(ev.target.result);
            console.log("Imported capabilities:", data);
            alert("能力导入成功（功能开发中）");
          } catch { alert("无效的 JSON 文件"); }
        };
        reader.readAsText(file);
      }
    });
  }

  // ─── Memory-related Events ─────────────────────────────────────────────
  document.querySelectorAll(".memory-tab").forEach(btn => {
    btn.addEventListener("click", () => switchMemoryTab(btn.dataset.mtab));
  });

  const refreshBtn = document.getElementById("btn-refresh-memory");
  if (refreshBtn) refreshBtn.addEventListener("click", loadMemory);

  const refreshSessBtn = document.getElementById("btn-refresh-sessions");
  if (refreshSessBtn) refreshSessBtn.addEventListener("click", loadMemory);

  // L1 编辑/保存
  const l1Textarea = document.getElementById("mem-L1");
  const l1SaveBtn = document.getElementById("btn-save-L1");
  if (l1Textarea && l1SaveBtn) {
    l1Textarea.addEventListener("input", () => { l1SaveBtn.disabled = false; });
    l1SaveBtn.addEventListener("click", saveL1Working);
  }
  const l1EditBtn = document.getElementById("btn-edit-L1");
  if (l1EditBtn) l1EditBtn.addEventListener("click", () => l1Textarea && l1Textarea.focus());

  // L2 编辑/保存
  const l2Textarea = document.getElementById("mem-L2");
  const l2SaveBtn = document.getElementById("btn-save-L2");
  if (l2Textarea && l2SaveBtn) {
    l2Textarea.addEventListener("input", () => { l2SaveBtn.disabled = false; });
    l2SaveBtn.addEventListener("click", saveL2Global);
  }
  const l2EditBtn = document.getElementById("btn-edit-L2");
  if (l2EditBtn) l2EditBtn.addEventListener("click", () => l2Textarea && l2Textarea.focus());

  // 洞察追加
  const addInsightBtn = document.getElementById("btn-add-insight");
  if (addInsightBtn) addInsightBtn.addEventListener("click", addInsight);

  // 新增记忆项
  const addMemBtn = document.getElementById("btn-add-memory");
  if (addMemBtn) addMemBtn.addEventListener("click", openAddMemoryModal);
  const submitMemBtn = document.getElementById("btn-submit-mem");
  if (submitMemBtn) submitMemBtn.addEventListener("click", submitAddMemory);

  // 新增 SOP
  const addSkillBtn = document.getElementById("btn-add-skill");
  if (addSkillBtn) addSkillBtn.addEventListener("click", openAddSkillModal);
  const submitSkillBtn = document.getElementById("btn-submit-skill");
  if (submitSkillBtn) submitSkillBtn.addEventListener("click", submitAddSkill);

  // Modal 关闭按钮（所有带 data-close 属性的）
  document.querySelectorAll(".modal-close").forEach(btn => {
    btn.addEventListener("click", () => {
      const targetId = btn.dataset.close;
      const target = document.getElementById(targetId);
      if (target) target.classList.add("hidden");
    });
  });

  // 设置保存
  const saveSettingsBtn = document.getElementById("btn-save-settings");
  if (saveSettingsBtn) saveSettingsBtn.addEventListener("click", saveSettings);
}

function downloadText(filename, text) {
  const blob = new Blob([text], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

// ─── Polling ───────────────────────────────────────────────────────────────────
let pollCount = 0;
setInterval(async () => {
  if (state.isAgentRunning) await refreshStatus();
  if (++pollCount % 6 === 0) {
    try {
      const s = await apiGET("/api/status");
      const badge = document.getElementById("llm-badge");
      if (badge && badge.textContent === "loading…") badge.textContent = s.llm_name;
    } catch { /* ignore */ }
  }
}, 5000);

// ─── Init ───────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  initEvents();
  switchView("chat");
  apiGET("/api/status").then(s => {
    const badge = document.getElementById("llm-badge");
    if (badge) badge.textContent = s.llm_name || "unknown";
  }).catch(() => {
    const badge = document.getElementById("llm-badge");
    if (badge) badge.textContent = "offline";
  });
});
