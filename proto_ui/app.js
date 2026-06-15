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
  wsStatus: "offline",   // offline | connecting | online
  currentTaskId: null,
  isAgentRunning: false,
  messages: [],          // [{id, role, content, time, toolCalls?}]
  llmList: [],
  activeLlmNo: 0,
  conductorAgents: [],   // stub
  hiveNotes: [],         // stub
};

// ─── API Helpers ────────────────────────────────────────────────────────────────
const API_BASE = "";  // 空字符串 = 同源 (FastAPI serve static files)

async function apiGET(path) {
  const r = await fetch(API_BASE + path);
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}`);
  return r.json();
}

async function apiPOST(path, body) {
  const r = await fetch(API_BASE + path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`POST ${path} → ${r.status}: ${txt}`);
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
    // Send subscribe immediately
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

  // Heartbeat
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
    case "subscribed":
      // Connected, waiting for content
      break;

    case "next":
      appendAgentChunk(msg.content, "agent");
      break;

    case "tool_start":
      // Mark tool start on last message
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

    case "pong":
      // heartbeat OK
      break;
  }
}

// ─── Chat Core ─────────────────────────────────────────────────────────────────
async function sendMessage(text) {
  if (!text.trim() || state.isAgentRunning) return;

  const input = document.getElementById("chat-input");
  input.value = "";
  autoResizeInput(input);

  // Add user message
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
  // Append to the last agent message or create new one
  const last = state.messages[state.messages.length - 1];
  if (last && last.role === "agent" && !last.done) {
    last.content += text;
  } else {
    const id = genId();
    const time = Date.now();
    state.messages.push({ id, role: "agent", content: text, time, done: false, toolCalls: [] });
  }
  renderMessages();
  scrollChatToBottom();
}

function appendAgentMessage(text, source) {
  const id = genId();
  state.messages.push({ id, role: source || "agent", content: text, time: Date.now(), done: true, toolCalls: [] });
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
    // Remove all non-empty-state children
    Array.from(container.children).forEach(el => {
      if (el.id !== "chat-empty") el.remove();
    });
    return;
  }

  empty.style.display = "none";

  // Efficient diff: rebuild only if count changed or force
  const existingIds = new Set(
    Array.from(container.querySelectorAll(".msg")).map(el => el.dataset.id)
  );
  const currentIds = new Set(state.messages.map(m => m.id));

  if (existingIds.size !== currentIds.size) {
    rebuildMessageList(container);
    return;
  }

  // Update content of existing messages
  state.messages.forEach(m => {
    const el = container.querySelector(`[data-id="${m.id}"]`);
    if (!el) return;
    const contentEl = el.querySelector(".msg-content");
    if (contentEl) contentEl.textContent = m.content;
    // Update tool badges
    renderToolBadges(el, m.toolCalls);
  });
}

function rebuildMessageList(container) {
  // Remove old messages (keep empty state)
  Array.from(container.children).forEach(el => { if (el.id !== "chat-empty") el.remove(); });

  state.messages.forEach(m => {
    const el = buildMessageEl(m);
    container.appendChild(el);
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
  // Remove old badges
  container.querySelectorAll(".tool-call-badge").forEach(b => b.remove());
  if (!toolCalls || toolCalls.length === 0) return;

  toolCalls.forEach(tc => {
    const badge = document.createElement("span");
    badge.className = "tool-call-badge";
    badge.dataset.toolName = tc.name;
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

  if (name === "settings") loadSettings();
}

// ─── Settings ──────────────────────────────────────────────────────────────────
async function loadSettings() {
  // Load LLM list
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

  // Update sidebar badge
  const badge = document.getElementById("llm-badge");
  const active = llms.find((l, i) => i === state.activeLlmNo);
  if (active) badge.textContent = active.name;
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
        <div class="llm-item-type">${llm.active ? "当前" : "备选"}</div>
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
    badge.textContent = state.llmList[index]?.name || "";
  } catch (err) {
    alert(`切换失败: ${err.message}`);
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
    sendBtn.disabled = true;
    abortBtn.style.display = "";
    statusMini.textContent = "运行中";
    document.querySelector("#agent-status-mini .status-dot").className = "status-dot running";
  } else {
    indicator.classList.add("hidden");
    sendBtn.disabled = false;
    abortBtn.style.display = "none";
    statusMini.textContent = "待机";
    document.querySelector("#agent-status-mini .status-dot").className = "status-dot idle";
    // Clear current task
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
  const dot = el.querySelector(".ws-dot");
  const label = el.querySelector(".ws-label");
  dot.className = `ws-dot ${status}`;
  label.textContent = status === "online" ? "已连接" : status === "connecting" ? "连接中…" : "未连接";
}

async function refreshStatus() {
  try {
    const s = await apiGET("/api/status");
    if (s.is_running && !state.isAgentRunning) {
      setAgentRunning(true);
    }
  } catch { /* silent */ }
}

// ─── Utils ─────────────────────────────────────────────────────────────────────
function genId() {
  return Math.random().toString(36).slice(2, 10);
}

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
  // View switching
  document.querySelectorAll(".view-btn").forEach(btn => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });

  // Chat input
  const chatInput = document.getElementById("chat-input");
  chatInput.addEventListener("input", () => {
    autoResizeInput(chatInput);
    const count = document.getElementById("char-count");
    count.textContent = chatInput.value.length > 0 ? `${chatInput.value.length}` : "";
  });
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(chatInput.value);
    }
  });

  // Send button
  document.getElementById("btn-send").addEventListener("click", () => {
    sendMessage(chatInput.value);
  });

  // Abort button
  document.getElementById("btn-abort").addEventListener("click", async () => {
    try {
      await apiPOST("/api/abort", {});
      wsSend({ type: "abort" });
    } catch { /* already handled by server */ }
    setAgentRunning(false);
  });

  // Clear chat
  document.getElementById("btn-clear-chat").addEventListener("click", () => {
    state.messages = [];
    renderMessages();
  });

  // Tool card close
  document.getElementById("tool-card-close").addEventListener("click", () => {
    document.getElementById("tool-card").classList.add("hidden");
  });

  // Dark mode toggle
  document.getElementById("setting-dark")?.addEventListener("change", (e) => {
    document.body.classList.toggle("dark", e.target.checked);
  });

  // Export chat
  document.getElementById("btn-export-chat")?.addEventListener("click", () => {
    const text = state.messages.map(m => `[${m.role}] ${m.content}`).join("\n\n");
    downloadText("genericagent-chat.txt", text);
  });

  // Export capabilities (morph)
  document.getElementById("btn-export-caps")?.addEventListener("click", async () => {
    try {
      const caps = await apiGET("/api/status");
      downloadText("genericagent-caps.json", JSON.stringify({ ...caps, version: "0.2.0" }, null, 2));
    } catch { /* stub */ }
  });

  // Morph drop zone
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
          } catch {
            alert("无效的 JSON 文件");
          }
        };
        reader.readAsText(file);
      }
    });
  }
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
  if (state.isAgentRunning) {
    await refreshStatus();
  }
  // Poll status mini every 5s to update llm badge
  if (++pollCount % 6 === 0) {
    try {
      const s = await apiGET("/api/status");
      const badge = document.getElementById("llm-badge");
      if (badge.textContent === "loading…") {
        badge.textContent = s.llm_name;
      }
    } catch { /* ignore */ }
  }
}, 5000);

// ─── Init ───────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  initEvents();
  switchView("chat");
  // Load initial status
  apiGET("/api/status").then(s => {
    const badge = document.getElementById("llm-badge");
    badge.textContent = s.llm_name || "unknown";
  }).catch(() => {
    document.getElementById("llm-badge").textContent = "offline";
  });
});
