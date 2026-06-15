"""
FastAPI Backend API Server for GenericAgent
Wraps GeneraticAgent (agentmain.py) with HTTP + WebSocket interface.

Run: uvicorn api_server:app --host 0.0.0.0 --port 18792 --reload
"""
import asyncio
import json
import re
import threading
import uuid
import queue
import time
from collections import defaultdict
from contextlib import asynccontextmanager
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
    out_q = asyncio.Queue()
    _task_queues[task_id] = out_q
    _task_meta[task_id] = {
        "source": source,
        "created_at": time.time(),
        "task_id": task_id,
    }
    with _agent_lock:
        _current_task_id[0] = task_id

    pending_tool = None   # {"name", "args", "started"}
    buffer = ""           # accumulate chunk text to detect tool boundaries

    def bridge():
        nonlocal pending_tool, buffer
        while True:
            try:
                item = display_queue.get(timeout=120)
            except queue.Empty:
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda: out_q.put_nowait(_build_ws_msg("error", message="timeout"))
                )
                break

            if item is None:
                # End of stream
                if pending_tool:
                    asyncio.get_event_loop().call_soon_threadsafe(
                        lambda: out_q.put_nowait(_build_ws_msg(
                            "tool_end",
                            tool_call={"id": "", "name": pending_tool["name"],
                                       "args": pending_tool["args"], "result": None},
                            success=False,
                        ))
                    )
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda: out_q.put_nowait(None)
                )
                break

            if isinstance(item, dict):
                content = item.get("next") or item.get("done") or ""
            elif isinstance(item, str):
                content = item
            else:
                content = str(item)

            if not content:
                continue

            # ---- parse tool_start / tool_end from text ----
            tool_calls = _parse_tool_calls_from_chunk(content)

            for tc in tool_calls:
                # Close any pending tool first
                if pending_tool:
                    asyncio.get_event_loop().call_soon_threadsafe(
                        lambda: out_q.put_nowait(_build_ws_msg(
                            "tool_end",
                            tool_call={"id": "", "name": pending_tool["name"],
                                       "args": pending_tool["args"], "result": None},
                            success=True,
                        ))
                    )
                # Start new tool
                pending_tool = {"name": tc["name"], "args": tc["args"]}
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda: out_q.put_nowait(_build_ws_msg(
                        "tool_start",
                        tool_call={"id": str(uuid.uuid4())[:8],
                                   "name": tc["name"], "args": tc["args"]},
                    ))
                )

            # If we see the end marker and have a pending tool, close it
            if _TOOL_END_RE.search(content) and pending_tool:
                ended = pending_tool
                pending_tool = None
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda: out_q.put_nowait(_build_ws_msg(
                        "tool_end",
                        tool_call={"id": "", "name": ended["name"],
                                   "args": ended["args"], "result": None},
                        success=True,
                    ))
                )

            # Forward text as "next" (strip tool块，只留纯文本)
            clean = _TOOL_START_RE.sub("", content)
            clean = _TOOL_END_RE.sub("", clean)
            clean = _TOOL_CODE_BLOCK_RE.sub("", clean)
            clean = clean.strip()
            if clean:
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda c=clean, s=(item.get("source") if isinstance(item, dict) else "agent"): out_q.put_nowait(
                        _build_ws_msg("next", content=c, source=s)
                    )
                )

            # Final done
            if isinstance(item, dict) and item.get("done") is not None:
                # Close any open tool
                if pending_tool:
                    asyncio.get_event_loop().call_soon_threadsafe(
                        lambda: out_q.put_nowait(_build_ws_msg(
                            "tool_end",
                            tool_call={"id": "", "name": pending_tool["name"],
                                       "args": pending_tool["args"], "result": None},
                            success=True,
                        ))
                    )
                done_content = _TOOL_START_RE.sub("", item["done"])
                done_content = _TOOL_CODE_BLOCK_RE.sub("", done_content).strip()
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda: out_q.put_nowait(_build_ws_msg(
                        "done",
                        content=done_content,
                        source=item.get("source", "agent"),
                    ))
                )
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
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=18792)
