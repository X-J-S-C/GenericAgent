"""
FastAPI Backend API Server for GenericAgent
Wraps GeneraticAgent (agentmain.py) with HTTP + WebSocket interface.

Run: uvicorn api_server:app --host 0.0.0.0 --port 18792 --reload
"""
import asyncio
import json
import os
import re
import threading
import uuid
import queue
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Globals (populated by lifespan)
# ---------------------------------------------------------------------------
_agent = None
_task_queues = {}          # task_id -> asyncio.Queue
_task_meta = {}             # task_id -> {"source", "created_at", "task_text"}
_current_task_id = {}       # agent_no (0) -> task_id  (单 agent，所以只有 0)
_agent_lock = threading.Lock()
_sessions = {}              # session_id -> SessionInfo (in-memory, stub for now)

# Tool-call regex — matches "🛠️ Tool: `tool_name`  📥 args:\n````text\n{json}\n````"
_TOOL_START_RE = re.compile(
    r"🛠️\s+Tool:\s*`([^`]+)`\s*📥\s*args:\n````text\n(.*?)\n````",
    re.DOTALL,
)
_TOOL_END_RE = re.compile(r"`````\n")  # marks end of tool result output
_TOOL_CODE_BLOCK_RE = re.compile(r"````\n?(.*?)\n?````", re.DOTALL)


def _parse_tool_calls_from_chunk(chunk: str):
    """从原始文本 chunk 中解析工具调用信息，返回 list of (tool_name, args_json_str)"""
    results = []
    for m in _TOOL_START_RE.finditer(chunk):
        tool_name = m.group(1).strip()
        args_raw = m.group(2).strip()
        try:
            args = json.loads(args_raw)
        except json.JSONDecodeError:
            args = {"_raw": args_raw}
        results.append({"name": tool_name, "args": args})
    return results


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    from agentmain import GeneraticAgent

    _agent = GeneraticAgent()
    _agent.next_llm(0)

    t = threading.Thread(target=_agent.run, daemon=True, name="GeneraticAgent-run")
    t.start()

    print("[API Server] GeneraticAgent started")
    yield

    _agent.task_queue.put(None)
    print("[API Server] GeneraticAgent shutdown complete")


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(title="GenericAgent API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_proto_ui = Path(__file__).parent / "proto_ui"
if _proto_ui.exists():
    app.mount("/ui", StaticFiles(directory=str(_proto_ui), html=True), name="proto_ui")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_agent():
    if _agent is None:
        raise HTTPException(503, "Agent not initialized")
    return _agent


def _build_ws_msg(msg_type: str, **fields):
    """Build a WS message dict, filtering out None values."""
    return {k: v for k, v in {"type": msg_type, **fields}.items() if v is not None}


async def _register_task(task_id: str, display_queue: queue.Queue, source: str = "user"):
    """Bridge GeneraticAgent threading.Queue → asyncio.Queue with structured parsing."""
    loop = asyncio.get_event_loop()      # <-- 必须在协程中捕获，不能在子线程里 get_event_loop
    out_q: asyncio.Queue = asyncio.Queue()
    _task_queues[task_id] = out_q
    _task_meta[task_id] = {"source": source, "created_at": time.time(), "task_id": task_id}
    with _agent_lock:
        _current_task_id[0] = task_id

    def _put(obj):
        loop.call_soon_threadsafe(out_q.put_nowait, obj)

    pending_tool = None

    def bridge():
        nonlocal pending_tool
        while True:
            try:
                item = display_queue.get(timeout=120)
            except queue.Empty:
                _put(_build_ws_msg("error", message="timeout waiting for agent response"))
                break

            if item is None:
                if pending_tool:
                    _put(_build_ws_msg("tool_end", tool_call={
                        "name": pending_tool["name"], "args": pending_tool["args"], "result": None},
                        success=False))
                _put(None)
                break

            if isinstance(item, dict):
                content = item.get("next") or item.get("done") or ""
            elif isinstance(item, str):
                content = item
            else:
                content = str(item)

            if not content:
                continue

            # parse tool_start
            tool_calls = _parse_tool_calls_from_chunk(content)
            for tc in tool_calls:
                if pending_tool:
                    _put(_build_ws_msg("tool_end", tool_call={
                        "name": pending_tool["name"], "args": pending_tool["args"], "result": None},
                        success=True))
                pending_tool = {"name": tc["name"], "args": tc["args"]}
                _put(_build_ws_msg("tool_start", tool_call={
                    "id": str(uuid.uuid4())[:8], "name": tc["name"], "args": tc["args"]}))

            # if end-marker and pending tool, close it
            if _TOOL_END_RE.search(content) and pending_tool:
                ended = pending_tool
                pending_tool = None
                _put(_build_ws_msg("tool_end", tool_call={
                    "name": ended["name"], "args": ended["args"], "result": None},
                    success=True))

            # forward text
            clean = _TOOL_START_RE.sub("", content)
            clean = _TOOL_END_RE.sub("", clean)
            clean = _TOOL_CODE_BLOCK_RE.sub("", clean).strip()
            if clean:
                src = item.get("source", "agent") if isinstance(item, dict) else "agent"
                _put(_build_ws_msg("next", content=clean, source=src))

            # final done
            if isinstance(item, dict) and item.get("done") is not None:
                if pending_tool:
                    _put(_build_ws_msg("tool_end", tool_call={
                        "name": pending_tool["name"], "args": pending_tool["args"], "result": None},
                        success=True))
                done_content = _TOOL_START_RE.sub("", item["done"])
                done_content = _TOOL_CODE_BLOCK_RE.sub("", done_content).strip()
                _put(_build_ws_msg("done", content=done_content,
                    source=item.get("source", "agent")))
                with _agent_lock:
                    _current_task_id.pop(0, None)
                _running_tasks.pop(task_id, None)
                break

        _task_queues.pop(task_id, None)
        _task_meta.pop(task_id, None)

    threading.Thread(target=bridge, daemon=True, name=f"bridge-{task_id[:8]}").start()


_running_tasks = {}  # task_id -> True


# ---------------------------------------------------------------------------
# HTTP Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return {"status": "ok", "agent": "GenericAgent", "version": "0.1.0"}


@app.get("/api/status")
async def get_status():
    """Return current agent running status."""
    agent = _get_agent()
    with _agent_lock:
        cur = _current_task_id.get(0)
    return {
        "is_running": agent.is_running,
        "llm_no": agent.llm_no,
        "llm_name": agent.get_llm_name(),
        "history_len": len(agent.history),
        "current_task_id": cur,
    }


@app.get("/api/llms")
async def list_llms():
    """List all available LLM clients."""
    agent = _get_agent()
    return [
        {"index": i, "name": name, "active": active}
        for i, name, active in agent.list_llms()
    ]


@app.post("/api/llm/switch")
async def switch_llm(payload: dict):
    """Switch active LLM by index."""
    agent = _get_agent()
    n = payload.get("index")
    if n is None:
        raise HTTPException(400, "missing 'index'")
    if not isinstance(n, int):
        raise HTTPException(400, "'index' must be an integer")
    n_clients = len(agent.llmclients)
    if n < 0 or n >= n_clients:
        raise HTTPException(400, f"index {n} out of range (0-{n_clients - 1}), {n_clients} LLM(s) available")
    try:
        agent.next_llm(n)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "llm_no": agent.llm_no, "llm_name": agent.get_llm_name()}


@app.post("/api/abort")
async def abort_task():
    """Abort the currently running task."""
    agent = _get_agent()
    agent.abort()
    return {"ok": True}


@app.post("/api/chat")
async def chat(payload: dict):
    """Send a message. Returns task_id; subscribe to /ws/{task_id} for streaming."""
    query = payload.get("message") or payload.get("query")
    if not query:
        raise HTTPException(400, "missing 'message' field")

    source = payload.get("source", "user")
    images = payload.get("images") or []

    agent = _get_agent()
    if agent.is_running:
        raise HTTPException(409, "Agent is busy. POST /api/abort first.")

    task_id = str(uuid.uuid4())[:8]
    _running_tasks[task_id] = True
    display_queue = agent.put_task(query, source=source, images=images)
    await _register_task(task_id, display_queue, source=source)

    return {"task_id": task_id, "status": "queued"}


# ---------------------------------------------------------------------------
# Session Management
# ---------------------------------------------------------------------------

def _get_sessions_dir():
    """返回 L4 原始会话存储目录路径。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory", "L4_raw_sessions")


def _list_sessions_on_disk():
    """扫描 L4 目录，返回文件列表。"""
    sessions_dir = _get_sessions_dir()
    if not os.path.isdir(sessions_dir):
        return []
    files = []
    for name in os.listdir(sessions_dir):
        path = os.path.join(sessions_dir, name)
        if os.path.isfile(path):
            st = os.stat(path)
            files.append({
                "session_id": name,
                "path": path,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "mtime_iso": datetime.fromtimestamp(st.st_mtime).isoformat() if hasattr(datetime, "fromtimestamp") else "",
            })
    files.sort(key=lambda x: x["mtime"], reverse=True)
    return files


@app.get("/api/sessions")
async def list_sessions():
    """列出所有历史会话（来自 L4 原始会话目录）。"""
    files = _list_sessions_on_disk()
    return {
        "total": len(files),
        "sessions": [
            {
                "session_id": f["session_id"],
                "size": f["size"],
                "mtime": f["mtime"],
                "mtime_iso": f["mtime_iso"],
            }
            for f in files
        ],
    }


@app.get("/api/sessions/active")
async def get_active_session():
    """返回当前活跃会话详情（Agent 内存中的 history 与 key_info）。"""
    agent = _get_agent()
    with _agent_lock:
        current_task_id_val = _current_task_id.get(0)

    working_info = None
    passed_sessions = 0
    if agent.handler is not None and hasattr(agent.handler, "working"):
        working_info = agent.handler.working.get("key_info", "")
        passed_sessions = agent.handler.working.get("passed_sessions", 0)

    return {
        "is_running": agent.is_running,
        "current_task_id": current_task_id_val,
        "history_len": len(agent.history),
        "history": agent.history,
        "key_info": working_info,
        "passed_sessions": passed_sessions,
        "llm_name": agent.get_llm_name(),
    }


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除指定会话文件。"""
    # 简单安全校验：防止路径穿越
    if ".." in session_id or "/" in session_id or "\\" in session_id:
        raise HTTPException(400, "非法 session_id")

    sessions_dir = _get_sessions_dir()
    target = os.path.join(sessions_dir, session_id)

    if not os.path.isfile(target):
        raise HTTPException(404, f"未找到会话: {session_id}")

    try:
        os.remove(target)
        return {"ok": True, "deleted": session_id}
    except Exception as e:
        raise HTTPException(500, f"删除失败: {e}")


# ---------------------------------------------------------------------------
# Memory Management (L0 - L3)
# ---------------------------------------------------------------------------

def _get_memory_dir():
    """返回 memory 目录路径。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory")


def _read_file_safe(path):
    """安全读取文件内容，失败返回空字符串。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except (FileNotFoundError, OSError):
        return ""


def _write_file_safe(path, content):
    """安全写入文件内容。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _scan_sops(directory):
    """扫描目录中的 .md / .py SOP 文件，返回列表。"""
    if not os.path.isdir(directory):
        return []
    items = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        if not (name.endswith(".md") or name.endswith(".py")):
            continue
        st = os.stat(path)
        items.append({
            "title": name,
            "path": f"{os.path.basename(directory)}/{name}",
            "layer": "L3",
            "size": st.st_size,
            "mtime": st.st_mtime,
            "preview": _read_file_safe(path)[:200],
        })
    return items


@app.get("/api/memory")
async def get_memory_overview():
    """返回 L0-L3 记忆概览。"""
    mem_dir = _get_memory_dir()
    agent = _get_agent()

    # L0 — 原始会话（L4 目录）
    l4_sessions = _list_sessions_on_disk()

    # L1 — 工作记忆（Agent 内存中的 key_info）
    working_key_info = ""
    passed_sessions = 0
    if agent.handler is not None and hasattr(agent.handler, "working"):
        working_key_info = agent.handler.working.get("key_info", "")
        passed_sessions = agent.handler.working.get("passed_sessions", 0)

    # L2 — 全局记忆文件
    global_mem_path = os.path.join(mem_dir, "global_mem.txt")
    insight_path = os.path.join(mem_dir, "global_mem_insight.txt")

    global_mem_content = _read_file_safe(global_mem_path)
    insight_content = _read_file_safe(insight_path)

    # L3 — 技能库（SOP/脚本 文件）
    skills = []
    skills.extend(_scan_sops(mem_dir))
    skill_search_dir = os.path.join(mem_dir, "skill_search", "skill_search")
    skills.extend(_scan_sops(skill_search_dir))
    auto_op_dir = os.path.join(mem_dir, "autonomous_operation_sop")
    skills.extend(_scan_sops(auto_op_dir))

    return {
        "sessions": l4_sessions,
        "insights": {
            "content": insight_content,
            "path": "memory/global_mem_insight.txt",
        },
        "global": {
            "content": global_mem_content,
            "path": "memory/global_mem.txt",
        },
        "working": {
            "key_info": working_key_info,
            "passed_sessions": passed_sessions,
            "history_len": len(agent.history),
        },
        "skills": skills,
    }


@app.put("/api/memory/global")
async def update_global_memory(payload: dict):
    """更新全局记忆（global_mem.txt）。"""
    content = payload.get("content")
    if content is None:
        raise HTTPException(400, "缺少 content 字段")
    if not isinstance(content, str):
        raise HTTPException(400, "content 必须是字符串")

    mem_dir = _get_memory_dir()
    path = os.path.join(mem_dir, "global_mem.txt")
    try:
        _write_file_safe(path, content)
        return {"ok": True, "path": "memory/global_mem.txt", "size": len(content)}
    except Exception as e:
        raise HTTPException(500, f"写入失败: {e}")


@app.post("/api/memory/insights")
async def add_memory_insight(payload: dict):
    """追加记忆洞察到 global_mem_insight.txt。"""
    content = payload.get("content")
    if not content or not isinstance(content, str):
        raise HTTPException(400, "缺少 content 字段（字符串）")

    mem_dir = _get_memory_dir()
    path = os.path.join(mem_dir, "global_mem_insight.txt")
    existing = _read_file_safe(path)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if hasattr(datetime, "now") else ""
    new_content = f"{existing}\n\n# [{timestamp} 新增洞察]\n{content}\n"

    try:
        _write_file_safe(path, new_content)
        return {"ok": True, "path": "memory/global_mem_insight.txt", "size": len(new_content)}
    except Exception as e:
        raise HTTPException(500, f"写入失败: {e}")


@app.get("/api/memory/skills")
async def list_memory_skills():
    """列出技能库中的所有 SOP/脚本文件。"""
    mem_dir = _get_memory_dir()
    skills = []
    skills.extend(_scan_sops(mem_dir))
    skill_search_dir = os.path.join(mem_dir, "skill_search", "skill_search")
    skills.extend(_scan_sops(skill_search_dir))
    auto_op_dir = os.path.join(mem_dir, "autonomous_operation_sop")
    skills.extend(_scan_sops(auto_op_dir))
    return {"total": len(skills), "skills": skills}


@app.post("/api/memory/add-item")
async def add_memory_item(payload: dict):
    """新增记忆项（主要用于 L1 工作记忆，也支持 L2/L3 文件）。

    body: { layer: "L1" | "L2" | "L3", content: str, title: str }
    """
    layer = payload.get("layer", "L1")
    content = payload.get("content")
    title = payload.get("title", "untitled")

    if not content or not isinstance(content, str):
        raise HTTPException(400, "缺少 content 字段（字符串）")
    if layer not in ("L0", "L1", "L2", "L3"):
        raise HTTPException(400, "layer 必须是 L0/L1/L2/L3")

    mem_dir = _get_memory_dir()

    if layer == "L1":
        # 工作记忆：写入一个临时文件，同时尝试更新 Agent 内存 key_info
        agent = _get_agent()
        if agent.handler is not None and hasattr(agent.handler, "working"):
            old = agent.handler.working.get("key_info", "")
            agent.handler.working["key_info"] = f"{old}\n\n# {title}\n{content}\n".lstrip()

        l1_dir = os.path.join(mem_dir, "L1_working")
        safe_title = re.sub(r"[^\w\-.]", "_", title)
        path = os.path.join(l1_dir, f"{safe_title}_{int(time.time())}.txt")
        _write_file_safe(path, content)
        return {"ok": True, "layer": "L1", "path": path, "updated_memory_key": True}

    if layer == "L2":
        # 全局记忆：追加到 global_mem.txt
        path = os.path.join(mem_dir, "global_mem.txt")
        existing = _read_file_safe(path)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        new_content = f"{existing}\n\n# [{timestamp}] {title}\n{content}\n"
        _write_file_safe(path, new_content)
        return {"ok": True, "layer": "L2", "path": "memory/global_mem.txt", "size": len(new_content)}

    if layer == "L3":
        # 技能库：写入新的 SOP 文件
        safe_title = re.sub(r"[^\w\-.]", "_", title)
        path = os.path.join(mem_dir, f"{safe_title}.md")
        file_content = f"# {title}\n\n{content}\n"
        _write_file_safe(path, file_content)
        return {"ok": True, "layer": "L3", "path": f"memory/{safe_title}.md", "size": len(file_content)}

    # L0
    l0_dir = _get_sessions_dir()
    safe_title = re.sub(r"[^\w\-.]", "_", title)
    path = os.path.join(l0_dir, f"session_{safe_title}_{int(time.time())}.txt")
    _write_file_safe(path, content)
    return {"ok": True, "layer": "L0", "path": path, "size": len(content)}


@app.delete("/api/memory/{item_path:path}")
async def delete_memory_item(item_path: str):
    """删除记忆项（文件）。item_path 为相对于项目根目录的相对路径。"""
    # 安全校验
    if ".." in item_path:
        raise HTTPException(400, "非法路径")
    if item_path.startswith("/") or item_path.startswith("\\"):
        raise HTTPException(400, "必须是相对路径")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    target = os.path.normpath(os.path.join(script_dir, item_path))

    # 确保目标在 memory 目录下或其子目录内
    mem_root = _get_memory_dir()
    sessions_root = _get_sessions_dir()
    if not (target.startswith(mem_root) or target.startswith(sessions_root)):
        raise HTTPException(400, "只能删除 memory 目录下的文件")

    if not os.path.isfile(target):
        raise HTTPException(404, f"未找到文件: {item_path}")

    try:
        os.remove(target)
        return {"ok": True, "deleted": item_path}
    except Exception as e:
        raise HTTPException(500, f"删除失败: {e}")


# ---------------------------------------------------------------------------
# Settings Management
# ---------------------------------------------------------------------------

_SETTINGS_FILE = None  # 在运行时解析


def _get_settings_path():
    """返回 settings.json 路径。"""
    global _SETTINGS_FILE
    if _SETTINGS_FILE is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        _SETTINGS_FILE = os.path.join(script_dir, "temp", "settings.json")
    return _SETTINGS_FILE


def _load_settings():
    """加载 settings，不存在则返回默认值。"""
    path = _get_settings_path()
    default = {
        "work_mem_size": 10,
        "max_turns": 70,
        "verbose": True,
        "lang": "zh",
    }
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            merged = default.copy()
            merged.update(data)
            return merged
        return default
    except (json.JSONDecodeError, OSError):
        return default


def _save_settings(settings):
    """保存 settings 到 JSON 文件。"""
    path = _get_settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


@app.get("/api/settings")
@app.get("/api/settings/settings")
async def get_settings():
    """返回当前设置。"""
    return _load_settings()


@app.post("/api/settings")
async def update_settings(payload: dict):
    """保存设置（合并更新）。

    body: { setting_key: value, ... }
    """
    if not isinstance(payload, dict):
        raise HTTPException(400, "payload 必须是对象 {setting: value}")

    current = _load_settings()
    # 合并字段（只允许已知字段）
    allowed = {"work_mem_size", "max_turns", "verbose", "lang", "inc_out"}
    for key, value in payload.items():
        if key not in allowed:
            # 允许自定义字段也保存，但发出提醒
            current[key] = value
        else:
            if key == "work_mem_size" or key == "max_turns":
                if not isinstance(value, int) or value <= 0:
                    raise HTTPException(400, f"{key} 必须是正整数")
            current[key] = value

    try:
        _save_settings(current)
    except Exception as e:
        raise HTTPException(500, f"保存失败: {e}")

    # 如果 Agent 正在运行，更新部分运行时字段
    agent = _get_agent()
    if hasattr(agent, "verbose") and "verbose" in payload:
        agent.verbose = bool(payload["verbose"])
    if hasattr(agent, "inc_out") and "inc_out" in payload:
        agent.inc_out = bool(payload["inc_out"])

    return {"ok": True, "settings": current}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/{task_id}")
async def ws_chat(websocket: WebSocket, task_id: str):
    """
    Stream responses for a task.

    Client → Server:
      {"type": "subscribe"}
      {"type": "abort"}
      {"type": "ping"}

    Server → Client:
      {"type": "subscribed", "task_id": "..."}
      {"type": "next",      "content": "...", "source": "agent"}
      {"type": "tool_start","tool_call": {"id":"...","name":"...","args":{}}}
      {"type": "tool_end",  "tool_call": {...}, "success": true}
      {"type": "done",      "content": "...", "source": "agent"}
      {"type": "error",     "message": "..."}
      {"type": "pong"}
    """
    await websocket.accept()
    q = _task_queues.get(task_id)

    if q is None:
        await websocket.send_json(_build_ws_msg("error", message=f"Unknown task_id: {task_id}"))
        await websocket.close()
        return

    await websocket.send_json(_build_ws_msg("subscribed", task_id=task_id))

    async def receive_loop():
        try:
            while True:
                item = await asyncio.wait_for(q.get(), timeout=180)
                if item is None:
                    break
                await websocket.send_json(item)
        except asyncio.TimeoutError:
            await websocket.send_json(_build_ws_msg("error", message="timeout"))
        except Exception:
            pass

    async def send_loop():
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type")
                if t == "abort":
                    _get_agent().abort()
                    await websocket.send_json(_build_ws_msg("abort_ack"))
                elif t == "ping":
                    await websocket.send_json(_build_ws_msg("pong"))
        except WebSocketDisconnect:
            pass

    await asyncio.gather(receive_loop(), send_loop())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn, argparse
    parser = argparse.ArgumentParser(description="GenericAgent FastAPI Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18792)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
