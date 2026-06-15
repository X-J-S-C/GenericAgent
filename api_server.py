"""
FastAPI Backend API Server for GenericAgent
Wraps GeneraticAgent (agentmain.py) with HTTP + WebSocket interface.

Run: uvicorn api_server:app --host 0.0.0.0 --port 18792 --reload
"""
import asyncio
import json
import threading
import uuid
import queue
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# ---------------------------------------------------------------------------
# Globals (populated by lifespan)
# ---------------------------------------------------------------------------
_agent = None
_task_queues = {}      # task_id -> asyncio.Queue
_task_meta = {}         # task_id -> {"source": str, "created_at": float}
_running_tasks = {}     # task_id -> True (to detect stale tasks)
_agent_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Lifespan — start/stop GeneraticAgent background thread
# ---------------------------------------------------------------------------
def _run_agent_loop(agent):
    """Run in a daemon thread: pulls from agent.task_queue and routes."""
    while True:
        task = agent.task_queue.get()
        if task is None:
            break
        raw_query, source, images, display_queue = (
            task["query"], task["source"], task.get("images") or [], task["output"]
        )
        # Hand off to the existing GeneraticAgent.run() machinery
        # It already iterates display_queue.put({...}) — we just forward here.
        # The actual response streaming is handled by the WebSocket reader
        # that reads from the same display_queue.
        pass  # display_queue is read by ws_reader; nothing to do here


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    # Import here to avoid circular deps (agentmain imports llmcore which may need env)
    from agentmain import GeneraticAgent

    # Start agent
    _agent = GeneraticAgent()
    _agent.next_llm(0)

    # Start the daemon thread that processes the task queue
    t = threading.Thread(target=_agent.run, daemon=True, name="GeneraticAgent-run")
    t.start()

    print("[API Server] GeneraticAgent started in background thread")
    yield

    # Shutdown
    _agent.task_queue.put(None)
    print("[API Server] GeneraticAgent shutdown complete")


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(title="GenericAgent API", version="0.1.0", lifespan=lifespan)

# CORS — allow any origin for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve proto_ui/ static files if directory exists
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


async def _register_task(task_id: str, display_queue: queue.Queue, source: str = "user"):
    """Bridge a threading.Queue (GeneraticAgent output) → asyncio.Queue."""
    _task_queues[task_id] = asyncio.Queue()
    _task_meta[task_id] = {"source": source, "created_at": time.time()}
    _running_tasks[task_id] = True

    def bridge():
        """Pump items from threading.Queue → asyncio.Queue."""
        while True:
            try:
                item = display_queue.get(timeout=60)
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda i=item: _task_queues[task_id].put_nowait(i)
                )
                if isinstance(item, dict):
                    if item.get("done") is not None:
                        _running_tasks.pop(task_id, None)
                        break
                elif isinstance(item, str) and item.startswith("[ROUND END]"):
                    _running_tasks.pop(task_id, None)
                    break
            except queue.Empty:
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda: _task_queues[task_id].put_nowait(None)
                )
                break

    threading.Thread(target=bridge, daemon=True, name=f"bridge-{task_id[:8]}").start()


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
    return {
        "is_running": agent.is_running,
        "llm_no": agent.llm_no,
        "llm_name": agent.get_llm_name(),
        "history_len": len(agent.history),
    }


@app.get("/api/llms")
async def list_llms():
    """List all available LLM clients."""
    agent = _get_agent()
    return [{"index": i, "name": name, "active": active}
            for i, name, active in agent.list_llms()]


@app.post("/api/llm/switch")
async def switch_llm(payload: dict):
    """Switch active LLM by index."""
    agent = _get_agent()
    n = payload.get("index")
    if n is None:
        raise HTTPException(400, "missing 'index' field")
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
async def chat(payload: dict, background_tasks: BackgroundTasks):
    """
    Send a message to the agent.
    Returns immediately with a task_id.
    Subscribe to /ws/{task_id} to receive streaming responses.
    """
    query = payload.get("message") or payload.get("query")
    if not query:
        raise HTTPException(400, "missing 'message' field")

    source = payload.get("source", "user")
    images = payload.get("images") or []

    agent = _get_agent()
    if agent.is_running:
        raise HTTPException(409, "Agent is already running a task. POST /api/abort first.")

    task_id = str(uuid.uuid4())[:8]
    display_queue = agent.put_task(query, source=source, images=images)
    await _register_task(task_id, display_queue, source=source)

    return {"task_id": task_id, "status": "queued"}


# ---------------------------------------------------------------------------
# WebSocket — stream responses for a task
# ---------------------------------------------------------------------------

@app.websocket("/ws/{task_id}")
async def ws_chat(websocket: WebSocket, task_id: str):
    """
    WebSocket endpoint for streaming task responses.

    Client sends JSON messages to control the stream:
      - {"type": "subscribe"}          — start receiving (required first)
      - {"type": "abort"}             — abort this task

    Server sends JSON messages:
      - {"type": "next", "content": "..."}   — incremental output
      - {"type": "done", "content": "..."}   — final response
      - {"type": "error", "message": "..."}  — error occurred
    """
    await websocket.accept()
    q = _task_queues.get(task_id)

    if q is None:
        await websocket.send_json({"type": "error", "message": f"Unknown task_id: {task_id}"})
        await websocket.close()
        return

    # Send initial ack
    await websocket.send_json({"type": "subscribed", "task_id": task_id})

    async def receive_loop():
        """Pump messages from asyncio.Queue → WebSocket."""
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=120)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "error", "message": "timeout waiting for response"})
                break

            if item is None:
                break

            if isinstance(item, dict):
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
                    break
            elif isinstance(item, str):
                await websocket.send_json({"type": "next", "content": item, "source": "agent"})

        # Cleanup
        _task_queues.pop(task_id, None)
        _task_meta.pop(task_id, None)
        _running_tasks.pop(task_id, None)

    async def send_loop():
        """Pump incoming WebSocket messages → agent actions."""
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if msg.get("type") == "abort":
                    _get_agent().abort()
                    await websocket.send_json({"type": "abort_ack"})
                elif msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass

    # Run both loops concurrently; receive_loop exits when done
    await asyncio.gather(receive_loop(), send_loop())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=18792)
