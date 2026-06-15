import asyncio
import json
import os
import queue
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.append(os.path.abspath(os.path.dirname(__file__)))


# ---------------------------------------------------------------------------
# Agent singleton
# ---------------------------------------------------------------------------

_AGENT_LOCK = threading.Lock()
_AGENT: Optional["GeneraticAgent"] = None
_TASK_REGISTRY: Dict[str, queue.Queue] = {}
_TASK_REGISTRY_LOCK = threading.Lock()
_TASK_TTL = 10 * 60  # 10 minutes
_TASK_LAST_USE: Dict[str, float] = {}


def _get_agent():
    global _AGENT
    if _AGENT is not None:
        return _AGENT
    with _AGENT_LOCK:
        if _AGENT is not None:
            return _AGENT
        from agentmain import GeneraticAgent
        _AGENT = GeneraticAgent()
        threading.Thread(target=_AGENT.run, daemon=True).start()
    return _AGENT


def _gc_tasks():
    """Drop task queues older than TTL to bound memory."""
    with _TASK_REGISTRY_LOCK:
        now = time.time()
        stale = [tid for tid, t in _TASK_LAST_USE.items() if now - t > _TASK_TTL]
        for tid in stale:
            _TASK_REGISTRY.pop(tid, None)
            _TASK_LAST_USE.pop(tid, None)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    query: str
    source: str = "user"
    images: Optional[List[str]] = None  # data URIs / base64 / paths


class SwitchLLMRequest(BaseModel):
    index: int


# ---------------------------------------------------------------------------
# Bridge: thread Queue -> async iterator
# ---------------------------------------------------------------------------

async def _queue_to_async_iter(sync_q: queue.Queue, task_id: str, poll_interval: float = 0.05):
    """Pull items from a threading.Queue and yield them one by one.

    Uses a simple async sleep + non-blocking get so we never block the event
    loop on a long-running agent step.
    """
    loop = asyncio.get_running_loop()

    def _get_nowait():
        try:
            return sync_q.get_nowait()
        except queue.Empty:
            return _SENTINEL

    _SENTINEL = object()

    while True:
        # Try a blocking get with short timeout so we wake up promptly when the
        # agent has progress, but without burning CPU.
        try:
            item = await loop.run_in_executor(None, sync_q.get, True, 0.5)
            _TASK_LAST_USE[task_id] = time.time()
            yield item
            if "done" in item or "error" in item:
                return
            continue
        except Exception:
            # timeout or queue gone - just loop again
            pass

        # Peek once more to avoid unnecessary sleeps if the agent is fast,
        # otherwise yield control back to the event loop.
        await asyncio.sleep(poll_interval)
        if task_id not in _TASK_REGISTRY:
            return


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Boot the agent at startup so the first request has no extra latency.
    try:
        _get_agent()
        print("[api_server] GeneraticAgent initialized in daemon thread.")
    except Exception as exc:
        print(f"[api_server] WARNING: agent init failed: {exc}")
    yield
    # Clean-up at shutdown is minimal - the agent thread is a daemon thread.
    print("[api_server] shutting down.")


app = FastAPI(
    title="GeneraticAgent API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------

@app.get("/", tags=["meta"])
def root():
    return {"ok": True, "service": "GeneraticAgent API", "time": time.time()}


@app.get("/api/status", tags=["agent"])
def agent_status():
    a = _get_agent()
    return {
        "is_running": bool(getattr(a, "is_running", False)),
        "stop_sig": bool(getattr(a, "stop_sig", False)),
        "llm_no": getattr(a, "llm_no", 0),
        "llm_name": a.get_llm_name() if a else None,
    }


@app.get("/api/llms", tags=["agent"])
def list_llms():
    a = _get_agent()
    try:
        items = a.list_llms()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "llms": [{"index": idx, "name": name, "active": active} for idx, name, active in items],
        "current": a.llm_no,
    }


@app.post("/api/llm/switch", tags=["agent"])
def switch_llm(body: SwitchLLMRequest):
    a = _get_agent()
    try:
        a.next_llm(body.index)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "index": a.llm_no, "name": a.get_llm_name()}


@app.post("/api/abort", tags=["agent"])
def abort_task():
    a = _get_agent()
    a.abort()
    return {"ok": True, "message": "abort signal sent"}


@app.post("/api/chat", tags=["chat"])
def chat(body: ChatRequest):
    """Submit a user query. Returns a task_id for streaming via WebSocket."""
    a = _get_agent()
    task_id = uuid.uuid4().hex

    # Pre-register an empty queue. put_task() replaces it, but we store the
    # queue it returns so the WebSocket side can find it by task_id.
    dq = a.put_task(body.query, source=body.source, images=body.images)
    with _TASK_REGISTRY_LOCK:
        _TASK_REGISTRY[task_id] = dq
        _TASK_LAST_USE[task_id] = time.time()

    _gc_tasks()
    return {"ok": True, "task_id": task_id}


# ---------------------------------------------------------------------------
# WebSocket route
# ---------------------------------------------------------------------------

@app.websocket("/ws/{task_id}")
async def ws_stream(websocket: WebSocket, task_id: str):
    """Stream agent output for a given task_id.

    Client messages (JSON):
      {"type": "subscribe"}  - start/continue streaming
      {"type": "abort"}      - abort the underlying agent task
      {"type": "ping"}       - server responds with {"type":"pong"}

    Server messages (JSON):
      {"type": "next", "content": "...", "source": "..."}
      {"type": "done", "content": "..."}
      {"type": "error", "message": "..."}
    """
    await websocket.accept()

    # Quick validation - allow some wait for recently created tasks.
    q: Optional[queue.Queue] = None
    for _ in range(20):
        with _TASK_REGISTRY_LOCK:
            q = _TASK_REGISTRY.get(task_id)
        if q is not None:
            break
        await asyncio.sleep(0.1)

    if q is None:
        await websocket.send_json({"type": "error", "message": f"unknown task_id: {task_id}"})
        await websocket.close()
        return

    # Keep a short-lived consumer task and forward messages.
    consumer_task: Optional[asyncio.Task] = None

    async def _consumer():
        try:
            async for item in _queue_to_async_iter(q, task_id):
                if "next" in item:
                    await websocket.send_json({
                        "type": "next",
                        "content": item["next"],
                        "source": item.get("source", "agent"),
                    })
                elif "done" in item:
                    await websocket.send_json({
                        "type": "done",
                        "content": item["done"],
                        "source": item.get("source", "agent"),
                    })
                    return
                elif "error" in item:
                    await websocket.send_json({
                        "type": "error",
                        "message": str(item["error"]),
                    })
                    return
        except Exception as exc:
            try:
                await websocket.send_json({"type": "error", "message": str(exc)})
            except Exception:
                pass

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "invalid JSON"})
                continue

            mtype = msg.get("type")

            if mtype == "subscribe":
                if consumer_task is None or consumer_task.done():
                    consumer_task = asyncio.create_task(_consumer())
                await websocket.send_json({"type": "subscribed", "task_id": task_id})

            elif mtype == "abort":
                a = _get_agent()
                a.abort()
                await websocket.send_json({"type": "aborted", "task_id": task_id})

            elif mtype == "ping":
                await websocket.send_json({"type": "pong", "time": time.time()})

            else:
                await websocket.send_json({"type": "error", "message": f"unknown type: {mtype}"})

    except WebSocketDisconnect:
        pass
    finally:
        if consumer_task is not None and not consumer_task.done():
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass
        # Tidy up the registry once we are done with this task.
        with _TASK_REGISTRY_LOCK:
            _TASK_REGISTRY.pop(task_id, None)
            _TASK_LAST_USE.pop(task_id, None)


# ---------------------------------------------------------------------------
# Dev entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api_server:app",
        host=os.environ.get("GA_HOST", "0.0.0.0"),
        port=int(os.environ.get("GA_PORT", "18792")),
        reload=True,
    )
