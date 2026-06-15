"""
Agent API Client - Task 10
Shared client module for all frontends to consume GenericAgent via unified ApiMessage format.

Provides:
  - AgentAPIClient: wraps local GeneriAgent, yields ApiMessage objects
  - RemoteAgentAPIClient: connects to api_server WebSocket, yields ApiMessage objects

All frontends should use these clients instead of directly calling GeneriAgent methods.
"""
import asyncio
import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import AsyncGenerator, Optional

# ─── Message Enums (mirrors api_server.py for frontend use) ──────────────────
class MsgType(str, Enum):
    MESSAGE = "message"
    CONTROL = "control"
    STATUS  = "status"
    ERROR   = "error"


class MsgSubtype(str, Enum):
    NEXT         = "next"
    DONE         = "done"
    START        = "start"
    PING         = "ping"
    PONG         = "pong"
    ABORT        = "abort"
    ABORT_ACK    = "abort_ack"
    AGENT_STATUS = "agent_status"
    LLM_LIST     = "llm_list"
    LLM_SWITCHED = "llm_switched"
    ERROR        = "error"


class Role(str, Enum):
    USER      = "user"
    ASSISTANT = "assistant"
    SYSTEM    = "system"
    TOOL      = "tool"


# ─── ApiMessage (mirrors api_server.py) ─────────────────────────────────────
@dataclass
class Metadata:
    timestamp:  str = ""
    source:     Optional[str] = None
    images:     Optional[list[str]] = None
    llm_no:     Optional[int] = None
    llm_name:   Optional[str] = None
    is_running: Optional[bool] = None
    error_code: Optional[str] = None


@dataclass
class ApiMessage:
    """
    Unified message envelope for all frontend-agent communications.
    Mirrors the ApiMessage schema defined in api_server.py.
    """
    type:     MsgType
    subtype:  MsgSubtype
    id:       str = ""
    task_id:  Optional[str] = None
    role:     Optional[Role] = None
    content:  Optional[str] = None
    metadata: Metadata = None

    def __post_init__(self):
        if self.id is None or self.id == "":
            self.id = f"msg_{uuid.uuid4().hex[:12]}"
        if self.metadata is None:
            self.metadata = Metadata()

    @classmethod
    def from_dict(cls, d: dict) -> "ApiMessage":
        """Parse a dict (e.g. from JSON) into ApiMessage."""
        d = d.copy()
        d["type"]    = MsgType(d["type"])
        d["subtype"] = MsgSubtype(d["subtype"])
        if d.get("role"):
            d["role"] = Role(d["role"])
        if d.get("metadata"):
            d["metadata"] = Metadata(**d["metadata"])
        elif d.get("metadata") is None:
            d["metadata"] = Metadata()
        # Remove id if None/empty
        d.setdefault("id", f"msg_{uuid.uuid4().hex[:12]}")
        return cls(**d)

    def to_dict(self) -> dict:
        """Serialize to dict for JSON encoding."""
        d = {
            "id":       self.id,
            "type":     self.type.value,
            "subtype":  self.subtype.value,
            "task_id":  self.task_id,
            "role":     self.role.value if self.role else None,
            "content":  self.content,
            "metadata": {
                "timestamp":  self.metadata.timestamp,
                "source":     self.metadata.source,
                "images":     self.metadata.images,
                "llm_no":     self.metadata.llm_no,
                "llm_name":   self.metadata.llm_name,
                "is_running": self.metadata.is_running,
                "error_code": self.metadata.error_code,
            },
        }
        # Remove None values
        d = {k: v for k, v in d.items() if v is not None}
        d["metadata"] = {k: v for k, v in d["metadata"].items() if v is not None}
        return d

    @classmethod
    def from_py_queue_item(cls, item: dict, task_id: str = "") -> "ApiMessage":
        """
        Convert a raw py_queue item from GeneriAgent.put_task()
        into an ApiMessage.
        
        Raw item: {"next": "...", "source": "user"} or {"done": "...", "source": "user"}
        """
        if "next" in item:
            return cls(
                type=MsgType.MESSAGE,
                subtype=MsgSubtype.NEXT,
                task_id=task_id,
                role=Role.ASSISTANT,
                content=item["next"],
                metadata=Metadata(source=item.get("source", "agent")),
            )
        elif "done" in item:
            return cls(
                type=MsgType.MESSAGE,
                subtype=MsgSubtype.DONE,
                task_id=task_id,
                role=Role.ASSISTANT,
                content=item["done"],
                metadata=Metadata(source=item.get("source", "agent")),
            )
        # Unknown item type - wrap as error
        return cls(
            type=MsgType.ERROR,
            subtype=MsgSubtype.ERROR,
            task_id=task_id,
            content=str(item),
            metadata=Metadata(error_code="ERR_UNKNOWN_ITEM"),
        )


# ─── Convenience constructors (mirrors api_server.py) ────────────────────────
def msg_next(task_id: str, content: str, source: str = "agent") -> ApiMessage:
    return ApiMessage(
        type=MsgType.MESSAGE,
        subtype=MsgSubtype.NEXT,
        task_id=task_id,
        role=Role.ASSISTANT,
        content=content,
        metadata=Metadata(source=source),
    )


def msg_done(task_id: str, content: str, source: str = "agent") -> ApiMessage:
    return ApiMessage(
        type=MsgType.MESSAGE,
        subtype=MsgSubtype.DONE,
        task_id=task_id,
        role=Role.ASSISTANT,
        content=content,
        metadata=Metadata(source=source),
    )


def msg_start(task_id: str, content: str, source: str = "user") -> ApiMessage:
    return ApiMessage(
        type=MsgType.MESSAGE,
        subtype=MsgSubtype.START,
        task_id=task_id,
        role=Role.USER,
        content=content,
        metadata=Metadata(source=source),
    )


def msg_agent_status(is_running: bool, llm_no: int, llm_name: str) -> ApiMessage:
    return ApiMessage(
        type=MsgType.STATUS,
        subtype=MsgSubtype.AGENT_STATUS,
        metadata=Metadata(is_running=is_running, llm_no=llm_no, llm_name=llm_name),
    )


def msg_error(task_id: str | None, content: str, error_code: str = "ERR_INTERNAL") -> ApiMessage:
    return ApiMessage(
        type=MsgType.ERROR,
        subtype=MsgSubtype.ERROR,
        task_id=task_id,
        content=content,
        metadata=Metadata(error_code=error_code),
    )


# ─── AgentAPIClient (local GeneriAgent wrapper) ──────────────────────────────
class AgentAPIClient:
    """
    Wraps a local GeneriAgent instance and exposes an async interface
    that yields unified ApiMessage objects.

    Frontends should use this instead of directly calling GeneriAgent methods.

    Usage:
        client = AgentAPIClient()
        async for msg in client.send_message("hello"):
            print(msg.type, msg.subtype, msg.content)
    """

    def __init__(self, agent=None):
        self._agent = agent
        self._owns_agent = agent is None

    def start(self):
        """Start the agent in a background thread. Call once at startup."""
        if self._owns_agent and self._agent is None:
            from agentmain import GeneriAgent
            self._agent = GeneriAgent()
            t = threading.Thread(target=self._agent.run, daemon=True, name="AgentAPIClient-Worker")
            t.start()
            time.sleep(0.1)  # Let agent initialize

    @property
    def agent(self):
        return self._agent

    def is_running(self) -> bool:
        return self._agent.is_running if self._agent else False

    def get_llm_name(self) -> str:
        return self._agent.get_llm_name() if self._agent else ""

    def list_llms(self):
        return self._agent.list_llms() if self._agent else []

    def next_llm(self, index: int):
        self._agent.next_llm(index)

    def abort(self):
        if self._agent:
            self._agent.abort()

    async def send_message(
        self,
        query: str,
        source: str = "user",
        images: list[str] | None = None,
        task_id: str | None = None,
    ) -> AsyncGenerator[ApiMessage, None]:
        """
        Send a message to the agent and yield ApiMessage objects.

        Yields:
            ApiMessage with subtype=NEXT for streaming chunks
            ApiMessage with subtype=DONE when complete
            ApiMessage with subtype=ERROR on error
        """
        if self._agent is None:
            yield msg_error(task_id, "Agent not initialized", "ERR_NOT_INITIALIZED")
            return

        tid = task_id or str(uuid.uuid4())
        py_queue = self._agent.put_task(query, source=source, images=images or [])

        try:
            while True:
                try:
                    item = await asyncio.to_thread(py_queue.get, True, 1.0)
                except queue.Empty:
                    # Heartbeat - allow caller to check for interrupts
                    yield ApiMessage(
                        type=MsgType.MESSAGE,
                        subtype=MsgSubtype.NEXT,
                        task_id=tid,
                        content="",  # Empty = heartbeat
                        metadata=Metadata(source=source),
                    )
                    continue

                msg = ApiMessage.from_py_queue_item(item, task_id=tid)
                yield msg

                if msg.subtype == MsgSubtype.DONE:
                    break
        except Exception as e:
            yield msg_error(tid, str(e), "ERR_EXCEPTION")

    # ── Non-async convenience methods ────────────────────────────────────────

    def put_task_sync(self, query: str, source: str = "user", images: list[str] | None = None):
        """Synchronous put_task - returns (task_id, py_queue)."""
        if self._agent is None:
            raise RuntimeError("Agent not initialized")
        tid = str(uuid.uuid4())
        py_queue = self._agent.put_task(query, source=source, images=images or [])
        return tid, py_queue


# ─── RemoteAgentAPIClient (connects to api_server WebSocket) ────────────────
class RemoteAgentAPIClient:
    """
    Connects to the FastAPI backend (api_server.py) via WebSocket
    and yields unified ApiMessage objects.

    Use this when the frontend runs as a client connecting to a shared
    api_server process (rather than owning a local GeneriAgent).

    Usage:
        client = RemoteAgentAPIClient(base_url="http://127.0.0.1:18792")
        # First POST /api/chat to get task_id, then:
        async for msg in client.subscribe(task_id):
            print(msg.type, msg.subtype, msg.content)
    """

    def __init__(self, base_url: str = "http://127.0.0.1:18792"):
        self.base_url = base_url.rstrip("/")
        self._ws_url = base_url.replace("http", "ws") + "/ws"
        self._ws = None
        self._reader_task = None
        self._msg_queue: asyncio.Queue = asyncio.Queue()
        self._abort_event = asyncio.Event()

    async def _ws_reader(self, ws):
        """Background task: read from WebSocket, put messages in queue."""
        try:
            while True:
                raw = await ws.receive_json()
                msg = ApiMessage.from_dict(raw)
                await self._msg_queue.put(msg)
                if msg.subtype == MsgSubtype.DONE:
                    break
        except Exception as e:
            await self._msg_queue.put(msg_error(None, str(e), "ERR_WS_READ"))
        finally:
            self._msg_queue.put(None)  # Sentinel

    async def subscribe(self, task_id: str) -> AsyncGenerator[ApiMessage, None]:
        """
        Subscribe to a task's message stream via WebSocket.
        Must call POST /api/chat FIRST to create the task and get task_id.
        """
        import websockets

        ws_url = f"{self._ws_url}/{task_id}"
        try:
            self._ws = await websockets.connect(ws_url)
        except Exception as e:
            yield msg_error(task_id, f"Failed to connect: {e}", "ERR_WS_CONNECT")
            return

        self._reader_task = asyncio.create_task(self._ws_reader(self._ws))

        try:
            # Send subscribe message
            await self._ws.send_json({"type": "control", "subtype": "subscribe"})

            while True:
                msg = await self._msg_queue.get()
                if msg is None:
                    break
                yield msg
                if msg.subtype == MsgSubtype.DONE:
                    break
        except Exception as e:
            yield msg_error(task_id, str(e), "ERR_SUBSCRIBE")
        finally:
            if self._reader_task:
                self._reader_task.cancel()
            if self._ws:
                await self._ws.close()

    async def send_abort(self, task_id: str):
        """Send abort via WebSocket."""
        if self._ws:
            try:
                await self._ws.send_json({"type": "control", "subtype": "abort"})
            except Exception:
                pass

    @classmethod
    async def chat(
        cls,
        query: str,
        source: str = "user",
        base_url: str = "http://127.0.0.1:18792",
    ) -> AsyncGenerator[ApiMessage, None]:
        """
        Convenience: POST /api/chat then subscribe to the task.
        Yields ApiMessage objects until DONE.
        """
        import websockets, aiohttp

        base = base_url.rstrip("/")
        tid = None

        # 1. Create task via HTTP
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    f"{base}/api/chat",
                    json={"query": query, "source": source},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    tid = data.get("task_id")
        except Exception as e:
            yield msg_error(None, f"Failed to create task: {e}", "ERR_CREATE_TASK")
            return

        if not tid:
            yield msg_error(None, "No task_id returned", "ERR_NO_TASK_ID")
            return

        # 2. Subscribe via WebSocket
        client = cls(base_url=base)
        async for msg in client.subscribe(tid):
            yield msg
