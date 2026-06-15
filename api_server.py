"""
FastAPI backend for GenericAgent.
REST API for chat/control + WebSocket streaming.
"""
import asyncio, threading, uuid, time, queue as _queue
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import from agentmain
from agentmain import GeneraticAgent


# ── Singleton agent ────────────────────────────────────────────────────────────

_agent_instance: Optional[GeneraticAgent] = None
_agent_lock = threading.Lock()


def get_agent() -> GeneraticAgent:
    """Thread-safe access to the global GeneraticAgent singleton."""
    global _agent_instance
    with _agent_lock:
        if _agent_instance is None:
            _agent_instance = GeneraticAgent()
        return _agent_instance


# ── Task registry (in-memory) ──────────────────────────────────────────────────

class TaskEntry:
    """Tracks a running task and its display queue for WS consumers."""
    def __init__(self, display_queue: _queue.Queue):
        self.display_queue = display_queue
        self.aborted = False
        self.done = False


_task_registry: dict[str, TaskEntry] = {}
_registry_lock = threading.Lock()


def _register_task(display_queue: _queue.Queue) -> str:
    task_id = str(uuid.uuid4())[:8]
    with _registry_lock:
        _task_registry[task_id] = TaskEntry(display_queue)
    return task_id


def _get_task(task_id: str) -> Optional[TaskEntry]:
    with _registry_lock:
        return _task_registry.get(task_id)


def _cleanup_task(task_id: str):
    with _registry_lock:
        _task_registry.pop(task_id, None)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the GeneraticAgent run-loop in a daemon thread on startup."""
    agent = get_agent()
    t = threading.Thread(target=agent.run, daemon=True, name="GeneraticAgent-run")
    t.start()
    print(f"[api] GenericAgent started (llm={agent.get_llm_name()})")
    yield
    print("[api] Shutting down GenericAgent...")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="GenericAgent API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Request/Response models ───────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str
    source: str = "user"
    images: list[str] = []


class ChatResponse(BaseModel):
    task_id: str


class SwitchLLMRequest(BaseModel):
    index: int


# ── REST Endpoints ─────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "service": "GenericAgent API"}


@app.get("/api/status")
async def status():
    """Returns current agent running state."""
    agent = get_agent()
    return {
        "is_running": agent.is_running,
        "llm_no": agent.llm_no,
        "llm_name": agent.get_llm_name(),
    }


@app.get("/api/llms")
async def list_llms():
    """List all available LLM configurations."""
    agent = get_agent()
    return {"llms": [{"index": i, "name": name, "active": active}
                     for i, name, active in agent.list_llms()]}


@app.post("/api/llm/switch")
async def switch_llm(body: SwitchLLMRequest):
    """Switch to a different LLM by index."""
    agent = get_agent()
    try:
        agent.next_llm(body.index)
        return {"ok": True, "llm_no": agent.llm_no, "llm_name": agent.get_llm_name()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/abort")
async def abort_task():
    """Abort the currently running task."""
    agent = get_agent()
    if not agent.is_running:
        return {"ok": True, "message": "no task running"}
    agent.abort()
    return {"ok": True, "message": "abort signal sent"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(body: ChatRequest):
    """
    Submit a new chat task. Returns a task_id that is used to subscribe
    to the response stream via WebSocket /ws/{task_id}.
    """
    agent = get_agent()
    display_queue = agent.put_task(body.query, source=body.source, images=body.images)
    task_id = _register_task(display_queue)
    return ChatResponse(task_id=task_id)


# ── WebSocket endpoint ─────────────────────────────────────────────────────────

async def _ws_sender(ws: WebSocket, task_id: str, entry: TaskEntry):
    """
    Drain the task's display queue and forward messages over WebSocket.
    Handles: next, done, error, abort.
    """
    try:
        while not entry.done:
            try:
                item = entry.display_queue.get(timeout=0.5)
            except _queue.Empty:
                if entry.aborted:
                    await ws.send_json({"type": "done", "content": "[Aborted]", "source": "system"})
                    break
                continue

            if "next" in item:
                await ws.send_json({
                    "type": "next",
                    "content": item["next"],
                    "source": item.get("source", "agent"),
                })
            elif "done" in item:
                await ws.send_json({
                    "type": "done",
                    "content": item["done"],
                    "source": item.get("source", "agent"),
                })
                entry.done = True
                break
            elif "error" in item:
                await ws.send_json({"type": "error", "content": item["error"]})
                entry.done = True
                break
    except Exception as e:
        await ws.send_json({"type": "error", "content": str(e)})
    finally:
        _cleanup_task(task_id)


@app.websocket("/ws/{task_id}")
async def websocket_chat(ws: WebSocket, task_id: str):
    """
    WebSocket endpoint for streaming task responses.

    Client → Server messages:
      {"type": "subscribe"}       : start receiving stream
      {"type": "abort"}           : abort the current task
      {"type": "ping"}            : keepalive (pong sent back)

    Server → Client messages:
      {"type": "next", "content": "...", "source": "user"}
      {"type": "done", "content": "..."}
      {"type": "error", "content": "..."}
    """
    entry = _get_task(task_id)
    if entry is None:
        await ws.close(code=4004, reason="Task not found")
        return

    await ws.accept()

    # Track abort state locally so we don't need agent lock
    async def _recv_loop():
        try:
            while True:
                msg = await ws.receive_json()
                t = msg.get("type", "")
                if t == "abort":
                    entry.aborted = True
                    agent = get_agent()
                    agent.abort()
                    await ws.send_json({"type": "abort_ack"})
                elif t == "ping":
                    await ws.send_json({"type": "pong"})
                elif t == "subscribe":
                    await ws.send_json({"type": "subscribed", "task_id": task_id})
        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    # Run receiver and sender concurrently
    await asyncio.gather(_recv_loop(), _ws_sender(ws, task_id, entry))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=18792, reload=False)
