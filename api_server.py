"""
FastAPI backend for GenericAgent - Task 02
Wraps GeneraticAgent with HTTP + WebSocket API.
"""
import asyncio
import json
import queue
import threading
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agentmain import GeneraticAgent


# ─── Globals ────────────────────────────────────────────────────────────────
_agent: GeneraticAgent | None = None
_agent_lock = threading.Lock()
_tasks: dict[str, asyncio.Queue] = {}   # task_id → asyncio.Queue


# ─── Lifespan ───────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    with _agent_lock:
        _agent = GeneraticAgent()
        threading.Thread(target=_agent.run, daemon=True, name="GA-Worker").start()
    print("[api_server] GeneriAgent started in background thread")
    yield
    # shutdown
    if _agent and _agent.is_running:
        _agent.abort()
    print("[api_server] Shutdown complete")


# ─── App ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="GenericAgent API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Request/Response models ────────────────────────────────────────────────
class ChatRequest(BaseModel):
    query: str
    source: str = "user"
    images: list[str] | None = None


class ChatResponse(BaseModel):
    task_id: str


class SwitchLLMRequest(BaseModel):
    index: int


class StatusResponse(BaseModel):
    is_running: bool
    llm_no: int
    llm_name: str


class LLMInfo(BaseModel):
    index: int
    name: str
    is_active: bool


# ─── Helpers ────────────────────────────────────────────────────────────────
def _get_agent() -> GeneraticAgent:
    if _agent is None:
        raise HTTPException(503, "Agent not initialized")
    return _agent


def _bridge_to_async(task_id: str, py_queue: queue.Queue):
    """Pull from threading.Queue, push into asyncio.Queue."""
    async_q = _tasks[task_id]
    while True:
        try:
            item = py_queue.get(timeout=1.0)
            asyncio.run_coroutine_threadsafe(async_q.put(item), asyncio.get_event_loop())
            if "done" in item:
                break
        except queue.Empty:
            continue


# ─── HTTP Endpoints ─────────────────────────────────────────────────────────
@app.get("/", tags=["health"])
async def root():
    return {"status": "ok", "service": "GenericAgent API"}


@app.get("/api/status", response_model=StatusResponse, tags=["agent"])
async def get_status():
    agent = _get_agent()
    return StatusResponse(
        is_running=agent.is_running,
        llm_no=agent.llm_no,
        llm_name=agent.get_llm_name(),
    )


@app.get("/api/llms", response_model=list[LLMInfo], tags=["agent"])
async def list_llms():
    agent = _get_agent()
    return [LLMInfo(index=i, name=name, is_active=active)
            for i, name, active in agent.list_llms()]


@app.post("/api/llm/switch", response_model=StatusResponse, tags=["agent"])
async def switch_llm(req: SwitchLLMRequest):
    agent = _get_agent()
    try:
        agent.next_llm(req.index)
    except Exception as e:
        raise HTTPException(400, str(e))
    return StatusResponse(
        is_running=agent.is_running,
        llm_no=agent.llm_no,
        llm_name=agent.get_llm_name(),
    )


@app.post("/api/abort", tags=["agent"])
async def abort_task():
    agent = _get_agent()
    agent.abort()
    return {"ok": True}


@app.post("/api/chat", response_model=ChatResponse, tags=["chat"])
async def chat(req: ChatRequest, background: BackgroundTasks):
    task_id = str(uuid.uuid4())
    async_q: asyncio.Queue = asyncio.Queue()
    _tasks[task_id] = async_q

    agent = _get_agent()
    py_queue = agent.put_task(req.query, source=req.source, images=req.images)

    threading.Thread(
        target=_bridge_to_async,
        args=(task_id, py_queue),
        daemon=True,
        name=f"Bridge-{task_id[:8]}",
    ).start()

    return ChatResponse(task_id=task_id)


# ─── WebSocket Endpoint ─────────────────────────────────────────────────────
@app.websocket("/ws/{task_id}")
async def websocket_chat(ws: WebSocket, task_id: str):
    if task_id not in _tasks:
        await ws.close(code=4004, reason="Task not found")
        return

    await ws.accept()
    async_q = _tasks[task_id]

    try:
        while True:
            msg = await ws.receive_json()
            msg_type = msg.get("type", "")

            if msg_type == "abort":
                _get_agent().abort()
                await ws.send_json({"type": "abort_ack"})
                break

            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})

            elif msg_type == "subscribe":
                # Drain queue and send accumulated content
                while not async_q.empty():
                    item = await async_q.get()
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
                        _tasks.pop(task_id, None)
                        return

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        _tasks.pop(task_id, None)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=18792, reload=True)
