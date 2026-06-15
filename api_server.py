import os, sys, json, uuid, asyncio, threading, queue, time
from datetime import datetime
from typing import Optional, List, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agentmain import GeneraticAgent

app = FastAPI(title="GenericAgent API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent: Optional[GeneraticAgent] = None
agent_lock = threading.Lock()

_TASK_REGISTRY: Dict[str, dict] = {}
_TASK_TTL = 600
_TASK_MAX = 256

class ChatRequest(BaseModel):
    query: str
    source: str = "user"

class SwitchLLMRequest(BaseModel):
    index: int

class TaskInfo(BaseModel):
    task_id: str
    created_at: datetime
    done: bool

def _get_agent() -> GeneraticAgent:
    global agent
    with agent_lock:
        if agent is None:
            agent = GeneraticAgent()
            threading.Thread(target=agent.run, daemon=True).start()
    return agent

def _gc_tasks():
    now = time.time()
    expired = [tid for tid, info in _TASK_REGISTRY.items() 
               if now - info['created'] > _TASK_TTL or info.get('done')]
    for tid in expired:
        del _TASK_REGISTRY[tid]
    if len(_TASK_REGISTRY) >= _TASK_MAX:
        oldest = sorted(_TASK_REGISTRY.keys(), key=lambda k: _TASK_REGISTRY[k]['created'])
        for tid in oldest[:len(_TASK_REGISTRY) - _TASK_MAX // 2]:
            del _TASK_REGISTRY[tid]

async def _read_sync_queue(q: queue.Queue, timeout: float = 0.5):
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, lambda: q.get(timeout=timeout))
    except queue.Empty:
        return None

@app.get("/")
async def root():
    _gc_tasks()
    ag = _get_agent()
    return {
        "status": "running",
        "agent": "GenericAgent",
        "current_llm": ag.get_llm_name(model=True),
        "tasks_active": len([t for t in _TASK_REGISTRY.values() if not t.get('done')])
    }

@app.get("/api/status")
async def get_status():
    _gc_tasks()
    ag = _get_agent()
    return {
        "is_running": ag.is_running,
        "current_llm": ag.get_llm_name(model=True),
        "llm_full_name": ag.get_llm_name(),
        "active_tasks": len([t for t in _TASK_REGISTRY.values() if not t.get('done')]),
        "total_tasks": len(_TASK_REGISTRY)
    }

@app.get("/api/llms")
async def list_llms():
    ag = _get_agent()
    llms = ag.list_llms()
    return [{
        "index": idx,
        "name": name,
        "active": active
    } for idx, name, active in llms]

@app.post("/api/llm/switch")
async def switch_llm(request: SwitchLLMRequest):
    ag = _get_agent()
    try:
        ag.next_llm(request.index)
        return {"success": True, "current_llm": ag.get_llm_name(model=True)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/abort")
async def abort_task():
    ag = _get_agent()
    ag.abort()
    return {"success": True, "message": "Task aborted"}

@app.post("/api/chat")
async def create_chat(request: ChatRequest):
    _gc_tasks()
    ag = _get_agent()
    task_id = f"t_{uuid.uuid4().hex[:12]}"
    display_queue = ag.put_task(request.query, source=request.source)
    _TASK_REGISTRY[task_id] = {
        "queue": display_queue,
        "created": time.time(),
        "done": False
    }
    return {"task_id": task_id}

@app.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    await websocket.accept()
    task_info = _TASK_REGISTRY.get(task_id)
    if not task_info:
        await websocket.send_json({"type": "error", "data": "Task not found"})
        await websocket.close()
        return
    
    display_queue = task_info["queue"]
    try:
        while not task_info.get("done"):
            data = await _read_sync_queue(display_queue)
            if data is None:
                await asyncio.sleep(0.1)
                continue
            
            if "next" in data:
                await websocket.send_json({"type": "next", "data": data["next"]})
            elif "done" in data:
                await websocket.send_json({"type": "done", "data": data["done"]})
                task_info["done"] = True
                break
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.send_json({"type": "error", "data": str(e)})
    finally:
        task_info["done"] = True

@app.get("/api/tasks")
async def list_tasks():
    _gc_tasks()
    return [{
        "task_id": tid,
        "created_at": datetime.fromtimestamp(info["created"]),
        "done": info.get("done", False)
    } for tid, info in _TASK_REGISTRY.items()]

@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    if task_id in _TASK_REGISTRY:
        del _TASK_REGISTRY[task_id]
        return {"success": True}
    raise HTTPException(status_code=404, detail="Task not found")

if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("GA_HOST", "0.0.0.0")
    port = int(os.environ.get("GA_PORT", "18792"))
    uvicorn.run(app, host=host, port=port)
