"""FastAPI + WebSocket 后端：对外暴露 GeneraticAgent 的 HTTP 入口与流式订阅。

路由：
    GET  /                  健康检查
    GET  /api/status        Agent 运行状态 / 当前 LLM
    GET  /api/llms          列出所有已配置 LLM（含当前激活项）
    POST /api/llm/switch    切换 LLM，Body: {"index": int}
    POST /api/abort         中止当前 Agent 任务
    POST /api/chat          提交用户消息，返回 task_id
    WS   /ws/{task_id}      订阅对应 task 的流式输出

WebSocket 协议（客户端 -> 服务端，JSON）：
    {"type": "subscribe"}    继续订阅（默认建立连接即订阅）
    {"type": "abort"}        中止底层 Agent 任务
    {"type": "ping"}         心跳

WebSocket 协议（服务端 -> 客户端，JSON）：
    {"type": "next",  "content": str, "source": str}   中间增量/完整块
    {"type": "done",  "content": str}                  最终完整结果
    {"type": "error", "message": str}                  异常信息
    {"type": "pong"}                                   心跳响应
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import queue
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except Exception as _import_err:  # pragma: no cover - 仅在依赖未安装时触发
    print(f"[ERROR] FastAPI 依赖缺失：{_import_err}", file=sys.stderr)
    print("请先执行：pip install fastapi uvicorn[standard]", file=sys.stderr)
    raise

# ---------------------------------------------------------------------------
# 常量 / 配置
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

_DEFAULT_HOST = os.environ.get("GA_HOST", "0.0.0.0")
_DEFAULT_PORT = int(os.environ.get("GA_PORT", "18792"))

# task registry 的最大保留时长（秒）
_TASK_TTL = 10 * 60


# ---------------------------------------------------------------------------
# 全局 Agent 单例
# ---------------------------------------------------------------------------

@dataclass
class _TaskEntry:
    q: "queue.Queue[Dict[str, Any]]"
    created_at: float = field(default_factory=time.time)
    last_read_at: float = field(default_factory=time.time)


_TASK_REGISTRY: Dict[str, _TaskEntry] = {}
_REGISTRY_LOCK = threading.Lock()


def _gc_tasks(now: Optional[float] = None) -> None:
    """回收已过期的 task，避免内存无限增长。"""
    now = now if now is not None else time.time()
    with _REGISTRY_LOCK:
        expired = [tid for tid, e in _TASK_REGISTRY.items() if now - e.created_at > _TASK_TTL]
        for tid in expired:
            _TASK_REGISTRY.pop(tid, None)


def _register_task(task_id: str, q: "queue.Queue[Dict[str, Any]]") -> None:
    with _REGISTRY_LOCK:
        _TASK_REGISTRY[task_id] = _TaskEntry(q=q)


def _get_task_queue(task_id: str) -> Optional["queue.Queue[Dict[str, Any]]"]:
    with _REGISTRY_LOCK:
        entry = _TASK_REGISTRY.get(task_id)
        if entry is None:
            return None
        entry.last_read_at = time.time()
        return entry.q


# ---------------------------------------------------------------------------
# Agent 单例初始化（在 lifespan 中完成，避免 import 阶段被执行）
# ---------------------------------------------------------------------------

_AGENT_LOCK = threading.Lock()
_AGENT: Optional[Any] = None  # GeneraticAgent


def _ensure_agent() -> Any:
    """惰性初始化 GeneraticAgent（只调用一次）。"""
    global _AGENT
    with _AGENT_LOCK:
        if _AGENT is not None:
            return _AGENT
        from agentmain import GeneraticAgent  # 延迟导入，避免循环

        agent = GeneraticAgent()
        agent.verbose = True
        agent.inc_out = True
        threading.Thread(target=agent.run, daemon=True).start()
        _AGENT = agent
        return _AGENT


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001 - fastapi 协议要求签名
    """启动阶段初始化 Agent 单例；不阻塞事件循环。"""
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _ensure_agent)
    except Exception as exc:  # pragma: no cover - 防御性
        print(f"[WARN] Agent 初始化失败：{exc}", file=sys.stderr)
    yield


app = FastAPI(
    title="GenericAgent API Server",
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
# 请求体模型
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    query: str
    source: str = "user"
    images: Optional[List[str]] = None


class SwitchLLMRequest(BaseModel):
    index: int


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _agent_snapshot() -> Dict[str, Any]:
    agent = _AGENT
    if agent is None:
        return {"is_running": False, "llm_no": 0, "llm_name": None}
    try:
        name = agent.get_llm_name()
    except Exception:
        name = None
    return {
        "is_running": bool(getattr(agent, "is_running", False)),
        "llm_no": int(getattr(agent, "llm_no", 0)),
        "llm_name": name,
    }


# ---------------------------------------------------------------------------
# HTTP 路由
# ---------------------------------------------------------------------------

@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "GenericAgent API Server",
        "time": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/status")
def api_status() -> Dict[str, Any]:
    return {"ok": True, **_agent_snapshot()}


@app.get("/api/llms")
def api_llms() -> Dict[str, Any]:
    agent = _AGENT
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent 尚未初始化")
    try:
        items = [
            {"index": int(idx), "name": str(name), "active": bool(active)}
            for idx, name, active in agent.list_llms()
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取 LLM 列表失败：{exc}") from exc
    return {"ok": True, "llms": items, "current": _agent_snapshot()}


@app.post("/api/llm/switch")
def api_llm_switch(body: SwitchLLMRequest) -> Dict[str, Any]:
    agent = _AGENT
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent 尚未初始化")
    total = len(getattr(agent, "llmclients", []) or [])
    if total <= 0:
        raise HTTPException(status_code=500, detail="没有任何已配置的 LLM")
    idx = body.index
    if not (0 <= idx < total):
        raise HTTPException(status_code=400, detail=f"index 必须在 [0, {total - 1}] 范围内")
    try:
        agent.next_llm(idx)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"切换 LLM 失败：{exc}") from exc
    return {"ok": True, **_agent_snapshot()}


@app.post("/api/abort")
def api_abort() -> Dict[str, Any]:
    agent = _AGENT
    if agent is None:
        return {"ok": True, "aborted": False, "reason": "agent_not_initialized"}
    if not getattr(agent, "is_running", False):
        return {"ok": True, "aborted": False, "reason": "no_running_task"}
    try:
        agent.abort()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True, "aborted": True}


@app.post("/api/chat")
def api_chat(body: ChatRequest) -> Dict[str, Any]:
    agent = _AGENT
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent 尚未初始化")
    if not body.query or not str(body.query).strip():
        raise HTTPException(status_code=400, detail="query 不能为空")

    task_id = uuid.uuid4().hex
    _gc_tasks()
    try:
        display_q = agent.put_task(
            query=body.query,
            source=body.source or "user",
            images=body.images or [],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"提交任务失败：{exc}") from exc
    _register_task(task_id, display_q)
    return {"ok": True, "task_id": task_id}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

async def _drain_queue(display_q: "queue.Queue[Dict[str, Any]]", websocket: WebSocket) -> bool:
    """将 queue 中的元素推送到 WebSocket，直到收到 'done' 条目或连接断开。

    返回 True 表示正常结束（收到 done）；False 表示连接/读取失败。
    """
    loop = asyncio.get_running_loop()
    while True:
        try:
            item = await loop.run_in_executor(None, lambda: display_q.get(timeout=0.5))
        except queue.Empty:
            # 短超时：让出事件循环，允许其他协程运行（例如处理客户端消息）
            yield_ping = {"type": "ping_internal"}  # type: ignore[var-annotated]
            await asyncio.sleep(0)
            _ = yield_ping
            continue
        except Exception as exc:
            try:
                await websocket.send_json({"type": "error", "message": f"queue read: {exc}"})
            except Exception:
                pass
            return False

        try:
            if "next" in item:
                payload = {
                    "type": "next",
                    "content": str(item["next"]),
                    "source": str(item.get("source", "agent")),
                }
            elif "done" in item:
                payload = {"type": "done", "content": str(item["done"])}
            else:
                payload = {
                    "type": "next",
                    "content": json.dumps(item, ensure_ascii=False, default=str),
                    "source": "agent",
                }
            await websocket.send_json(payload)
        except WebSocketDisconnect:
            return False
        except Exception:
            return False

        if "done" in item:
            return True


@app.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str) -> None:
    await websocket.accept()

    display_q = _get_task_queue(task_id)
    if display_q is None:
        await websocket.send_json({
            "type": "error",
            "message": f"task_id {task_id} 不存在或已过期",
        })
        await websocket.close(code=1008)
        return

    drain_task = asyncio.create_task(_drain_queue(display_q, websocket))

    async def _handle_client_messages() -> None:
        try:
            while True:
                try:
                    data = await websocket.receive_json()
                except ValueError:
                    await websocket.send_json({"type": "error", "message": "请发送合法 JSON"})
                    continue
                except WebSocketDisconnect:
                    return

                msg_type = (data.get("type") if isinstance(data, dict) else None)
                if msg_type == "ping":
                    try:
                        await websocket.send_json({"type": "pong"})
                    except Exception:
                        return
                elif msg_type == "abort":
                    agent = _AGENT
                    if agent is not None and getattr(agent, "is_running", False):
                        try:
                            agent.abort()
                        except Exception:
                            pass
                elif msg_type == "subscribe":
                    # 默认连接即订阅；显式 subscribe 可视为 no-op
                    pass
                # 其他类型忽略
        except WebSocketDisconnect:
            return
        except Exception:
            return

    recv_task = asyncio.create_task(_handle_client_messages())

    try:
        await asyncio.gather(drain_task, recv_task, return_exceptions=True)
    finally:
        if not drain_task.done():
            drain_task.cancel()
        if not recv_task.done():
            recv_task.cancel()


# ---------------------------------------------------------------------------
# 直接执行入口：python api_server.py
# ---------------------------------------------------------------------------

def _main() -> None:
    import uvicorn  # 延迟导入

    uvicorn.run(
        "api_server:app",
        host=_DEFAULT_HOST,
        port=_DEFAULT_PORT,
        reload=False,
    )


if __name__ == "__main__":
    _main()
