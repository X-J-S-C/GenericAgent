"""
FastAPI backend for GenericAgent - Task 02 + Task 09
Wraps GeneraticAgent with HTTP + WebSocket API.
Unified JSON message format for all communications.
"""
import asyncio
import queue
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agentmain import GeneraticAgent


# ─── Enums ──────────────────────────────────────────────────────────────────
class MsgType(str, Enum):
    MESSAGE = "message"      # content-bearing messages
    CONTROL  = "control"     # ping/pong/abort
    STATUS   = "status"      # agent status updates
    ERROR    = "error"       # error responses


class MsgSubtype(str, Enum):
    # message subtypes
    NEXT      = "next"       # streaming chunk
    DONE      = "done"       # final message
    START     = "start"      # task started
    # control subtypes
    PING      = "ping"
    PONG      = "pong"
    ABORT     = "abort"
    ABORT_ACK = "abort_ack"
    # status subtypes
    AGENT_STATUS = "agent_status"
    LLM_LIST     = "llm_list"
    LLM_SWITCHED = "llm_switched"
    # error
    ERROR     = "error"


class Role(str, Enum):
    USER      = "user"
    ASSISTANT = "assistant"
    SYSTEM    = "system"
    TOOL      = "tool"


# ─── Unified Message Schema ─────────────────────────────────────────────────
class Metadata(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source:    Optional[str] = None
    images:    Optional[list[str]] = None
    llm_no:    Optional[int] = None
    llm_name:  Optional[str] = None
    is_running: Optional[bool] = None
    error_code: Optional[str] = None


class ApiMessage(BaseModel):
    """
    Universal message envelope for all WS and HTTP communications.
    Every message follows this structure.
    """
    id:       str = Field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    type:     MsgType
    subtype:  MsgSubtype
    task_id:  Optional[str] = None
    role:     Optional[Role] = None
    content:  Optional[str] = None
    metadata: Optional[Metadata] = Field(default_factory=Metadata)

    def to_ws_dict(self) -> dict:
        """Serialize for WebSocket/HTTP transport."""
        d = self.model_dump(exclude_none=True)
        # Simplify enum values to strings
        d["type"]    = self.type.value
        d["subtype"] = self.subtype.value
        if self.role:
            d["role"] = self.role.value
        d["metadata"] = self.metadata.model_dump(exclude_none=True)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ApiMessage":
        d = d.copy()
        d["type"]    = MsgType(d["type"])
        d["subtype"] = MsgSubtype(d["subtype"])
        if d.get("role"):
            d["role"] = Role(d["role"])
        if d.get("metadata"):
            d["metadata"] = Metadata(**d["metadata"])
        return cls(**d)


# ─── Convenience constructors ────────────────────────────────────────────────
def msg_next(task_id: str, content: str, source: str = "agent", role: Role = Role.ASSISTANT) -> ApiMessage:
    return ApiMessage(
        type=MsgType.MESSAGE,
        subtype=MsgSubtype.NEXT,
        task_id=task_id,
        role=role,
        content=content,
        metadata=Metadata(source=source),
    )


def msg_done(task_id: str, content: str, source: str = "agent", role: Role = Role.ASSISTANT) -> ApiMessage:
    return ApiMessage(
        type=MsgType.MESSAGE,
        subtype=MsgSubtype.DONE,
        task_id=task_id,
        role=role,
        content=content,
        metadata=Metadata(source=source),
    )


def msg_pong() -> ApiMessage:
    return ApiMessage(type=MsgType.CONTROL, subtype=MsgSubtype.PONG)


def msg_abort_ack(task_id: str) -> ApiMessage:
    return ApiMessage(type=MsgType.CONTROL, subtype=MsgSubtype.ABORT_ACK, task_id=task_id)


def msg_error(task_id: Optional[str], content: str, error_code: str = "ERR_INTERNAL") -> ApiMessage:
    return ApiMessage(
        type=MsgType.ERROR,
        subtype=MsgSubtype.ERROR,
        task_id=task_id,
        content=content,
        metadata=Metadata(error_code=error_code),
    )


def msg_agent_status(task_id: Optional[str] = None) -> ApiMessage:
    agent = _get_agent()
    return ApiMessage(
        type=MsgType.STATUS,
        subtype=MsgSubtype.AGENT_STATUS,
        task_id=task_id,
        metadata=Metadata(
            is_running=agent.is_running,
            llm_no=agent.llm_no,
            llm_name=agent.get_llm_name(),
        ),
    )


def msg_llm_list() -> ApiMessage:
    agent = _get_agent()
    llms = [{"index": i, "name": name, "is_active": active}
            for i, name, active in agent.list_llms()]
    content = __import__("json").dumps(llms, ensure_ascii=False)
    return ApiMessage(
        type=MsgType.STATUS,
        subtype=MsgSubtype.LLM_LIST,
        content=content,
        metadata=Metadata(llm_no=agent.llm_no, llm_name=agent.get_llm_name()),
    )


def msg_llm_switched(task_id: Optional[str] = None) -> ApiMessage:
    agent = _get_agent()
    return ApiMessage(
        type=MsgType.STATUS,
        subtype=MsgSubtype.LLM_SWITCHED,
        task_id=task_id,
        metadata=Metadata(llm_no=agent.llm_no, llm_name=agent.get_llm_name()),
    )


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
    print("[api_server] GeneraticAgent started in background thread")
    yield
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


# ─── Request models ─────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    query:   str
    source: str = "user"
    images: list[str] | None = None


class SwitchLLMRequest(BaseModel):
    index: int


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


def _ws_send(ws: WebSocket, msg: ApiMessage):
    asyncio.run_coroutine_threadsafe(ws.send_json(msg.to_ws_dict()), asyncio.get_event_loop())


# ─── HTTP Endpoints ─────────────────────────────────────────────────────────
@app.get("/", tags=["health"])
async def root():
    return ApiMessage(
        type=MsgType.STATUS,
        subtype=MsgSubtype.AGENT_STATUS,
        content="GenericAgent API is running",
        metadata=Metadata(
            is_running=_get_agent().is_running if _agent else None,
            llm_name=_get_agent().get_llm_name() if _agent else None,
        ),
    ).to_ws_dict()


@app.post("/api/chat", tags=["chat"])
async def chat(req: ChatRequest, background: BackgroundTasks):
    """Send a chat message. Returns task_id for WS subscription."""
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

    return ApiMessage(
        type=MsgType.MESSAGE,
        subtype=MsgSubtype.START,
        task_id=task_id,
        role=Role.USER,
        content=req.query,
        metadata=Metadata(source=req.source, images=req.images),
    ).to_ws_dict()


@app.get("/api/status", tags=["agent"])
async def get_status():
    return msg_agent_status().to_ws_dict()


@app.get("/api/llms", tags=["agent"])
async def list_llms():
    return msg_llm_list().to_ws_dict()


@app.post("/api/llm/switch", tags=["agent"])
async def switch_llm(req: SwitchLLMRequest):
    agent = _get_agent()
    try:
        agent.next_llm(req.index)
    except Exception as e:
        raise HTTPException(400, str(e))
    return msg_llm_switched().to_ws_dict()


@app.post("/api/abort", tags=["agent"])
async def abort_task():
    agent = _get_agent()
    agent.abort()
    return ApiMessage(
        type=MsgType.CONTROL,
        subtype=MsgSubtype.ABORT_ACK,
        content="Abort signal sent",
    ).to_ws_dict()


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
            raw = await ws.receive_json()
            msg = ApiMessage.from_dict(raw)
            subtype = msg.subtype

            if subtype == MsgSubtype.ABORT:
                _get_agent().abort()
                await ws.send_json(msg_abort_ack(task_id).to_ws_dict())
                break

            elif subtype == MsgSubtype.PING:
                await ws.send_json(msg_pong().to_ws_dict())

            elif subtype == MsgSubtype.NEXT or subtype == MsgSubtype.START:
                # Client sending NEXT is a no-op on server; just acknowledge
                pass

            elif subtype == MsgSubtype.SUBSCRIBE or subtype == MsgSubtype.DONE:
                # subscribe = drain queue; done = client is done consuming
                while not async_q.empty():
                    item = await async_q.get()
                    if "next" in item:
                        await ws.send_json(
                            msg_next(task_id, item["next"], item.get("source", "agent")).to_ws_dict()
                        )
                    elif "done" in item:
                        await ws.send_json(
                            msg_done(task_id, item["done"], item.get("source", "agent")).to_ws_dict()
                        )
                        _tasks.pop(task_id, None)
                        return

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json(msg_error(task_id, str(e)).to_ws_dict())
        except Exception:
            pass
    finally:
        _tasks.pop(task_id, None)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=18792, reload=True)
