"""FastAPI + WebSocket 后端：暴露 GeneraticAgent 的聊天/切换/中止能力。

HTTP:
  GET  /                  -> 健康信息
  GET  /api/status        -> agent 状态 / 当前 LLM
  GET  /api/llms          -> 已配置的 LLM 列表
  POST /api/llm/switch    -> 切换到指定 LLM(index)
  POST /api/abort         -> 中止当前任务
  POST /api/chat          -> 提交用户 prompt, 返回 task_id

WebSocket:
  WS   /ws/{task_id}      -> 订阅该任务的流式输出
         服务端推送消息类型 (JSON, text frame):
           {"type": "next", "data": "..."}          增量文本块
           {"type": "done", "data": "..."}          任务完成, 附完整响应
           {"type": "error", "data": "..."}         异常
           {"type": "ping", "data": "..."}          心跳
         客户端可发送任意消息, 服务端统一回复 {"type":"pong"}.
"""

import os
import sys
import json
import time
import uuid
import asyncio
import threading
from collections import OrderedDict

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

# ----------------------------------------------------------------------------
# 全局状态: 惰性初始化 + 任务注册表 + TTL GC
# ----------------------------------------------------------------------------
_AGENT_LOCK = threading.Lock()
_AGENT = None
_AGENT_THREAD = None

_TASK_REGISTRY = OrderedDict()        # task_id -> {"queue": queue.Queue, "created": float, "done": bool}
_TASK_TTL = 600                        # 10 分钟, 超过时间即清理
_REGISTRY_LOCK = threading.Lock()

_DEFAULT_HOST = os.environ.get("GA_HOST", "0.0.0.0")
_DEFAULT_PORT = int(os.environ.get("GA_PORT", "18792"))


def _get_agent():
    """惰性初始化 GeneraticAgent, 并启动 run 线程. 多次调用安全."""
    global _AGENT, _AGENT_THREAD
    if _AGENT is not None:
        return _AGENT
    with _AGENT_LOCK:
        if _AGENT is not None:
            return _AGENT
        from agentmain import GeneraticAgent  # noqa: F401  触发 mykeys 等加载
        agent = GeneraticAgent()
        t = threading.Thread(target=agent.run, daemon=True, name="ga-agent-run")
        t.start()
        _AGENT = agent
        _AGENT_THREAD = t
        return _AGENT


def _gc_tasks():
    """清理超过 TTL 的任务. 外部加锁调用."""
    now = time.time()
    expired = [tid for tid, meta in _TASK_REGISTRY.items()
               if now - meta["created"] > _TASK_TTL]
    for tid in expired:
        _TASK_REGISTRY.pop(tid, None)


def _new_task_id() -> str:
    return "t_" + uuid.uuid4().hex[:12]


def _register_task(task_id: str, q) -> None:
    with _REGISTRY_LOCK:
        _gc_tasks()
        _TASK_REGISTRY[task_id] = {"queue": q, "created": time.time(), "done": False}
        # 保持插入有序, 超出上限时丢弃最旧
        while len(_TASK_REGISTRY) > 256:
            _TASK_REGISTRY.popitem(last=False)


def _mark_task_done(task_id: str) -> None:
    with _REGISTRY_LOCK:
        meta = _TASK_REGISTRY.get(task_id)
        if meta is not None:
            meta["done"] = True


# ----------------------------------------------------------------------------
# FastAPI 应用
# ----------------------------------------------------------------------------
app = FastAPI(title="GenericAgent API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -- 请求 / 响应模型 --------------------------------------------------------
class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=100000, description="用户输入")


class ChatResponse(BaseModel):
    task_id: str


class SwitchRequest(BaseModel):
    index: int = Field(..., ge=0, description="LLM 索引(来自 /api/llms)")


# -- 路由 -------------------------------------------------------------------
@app.get("/")
def root():
    agent = _get_agent()
    return {
        "service": "GenericAgent API",
        "version": "0.1.0",
        "status": "ok",
        "current_llm": getattr(agent, "get_llm_name", lambda: "unknown")(),
    }


@app.get("/api/status")
def api_status():
    agent = _get_agent()
    with _REGISTRY_LOCK:
        pending = sum(1 for m in _TASK_REGISTRY.values() if not m["done"])
        total = len(_TASK_REGISTRY)
    return {
        "running": bool(getattr(agent, "is_running", False)),
        "current_llm": agent.get_llm_name(),
        "tasks_pending": pending,
        "tasks_total": total,
    }


@app.get("/api/llms")
def api_llms():
    agent = _get_agent()
    try:
        items = agent.list_llms()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"list_llms failed: {e}")
    return {"llms": [{"index": idx, "name": name, "active": active} for idx, name, active in items]}


@app.post("/api/llm/switch")
def api_switch_llm(body: SwitchRequest):
    agent = _get_agent()
    try:
        agent.next_llm(body.index)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "current": agent.get_llm_name()}


@app.post("/api/abort")
def api_abort():
    agent = _get_agent()
    if not getattr(agent, "is_running", False):
        return {"ok": True, "info": "nothing to abort"}
    try:
        agent.abort()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "info": "abort signal sent"}


@app.post("/api/chat", response_model=ChatResponse)
def api_chat(body: ChatRequest):
    agent = _get_agent()
    try:
        q = agent.put_task(body.query, source="web")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    task_id = _new_task_id()
    _register_task(task_id, q)
    return ChatResponse(task_id=task_id)


# -- WebSocket --------------------------------------------------------------
async def _read_sync_queue(q, sentinel_done):
    """在默认线程池里以短超时轮询同步 queue.Queue, 桥接为 async generator."""
    loop = asyncio.get_running_loop()

    def _poll_once():
        try:
            return q.get(timeout=0.5)
        except Exception:
            return sentinel_done

    sentinel_tick = object()
    while True:
        item = await loop.run_in_executor(None, _poll_once)
        if item is sentinel_done:
            # 短暂超时, 让事件循环去做别的事; 等待客户端 ping 检查连接
            yield sentinel_tick
            continue
        yield item
        if isinstance(item, dict) and "done" in item:
            return


@app.websocket("/ws/{task_id}")
async def ws_task(websocket: WebSocket, task_id: str):
    await websocket.accept()

    with _REGISTRY_LOCK:
        meta = _TASK_REGISTRY.get(task_id)
        if meta is None:
            await websocket.send_json({"type": "error", "data": f"unknown task_id: {task_id}"})
            await websocket.close()
            return
        q = meta["queue"]

    sentinel_done = object()
    consumer_task = None

    async def _consumer():
        """消费客户端发来的消息, 忽略内容, 仅维持 ping-pong."""
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                except Exception:
                    msg = {"type": "ping"}
                if isinstance(msg, dict) and msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "data": msg.get("data", "")})
                else:
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    consumer_task = asyncio.create_task(_consumer())

    try:
        async for item in _read_sync_queue(q, sentinel_done):
            if not isinstance(item, dict):
                continue
            if "next" in item:
                await websocket.send_json({"type": "next", "data": item["next"]})
            elif "done" in item:
                await websocket.send_json({"type": "done", "data": item["done"]})
                _mark_task_done(task_id)
                return
            elif "error" in item:
                await websocket.send_json({"type": "error", "data": item["error"]})
                _mark_task_done(task_id)
                return
    except WebSocketDisconnect:
        return
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "data": str(e)})
        except Exception:
            pass
        return
    finally:
        if consumer_task is not None and not consumer_task.done():
            consumer_task.cancel()
            try:
                await consumer_task
            except Exception:
                pass


# ----------------------------------------------------------------------------
# CLI 入口: python api_server.py -> 用 uvicorn 启动
# ----------------------------------------------------------------------------
def _main():
    try:
        import uvicorn
    except ImportError:
        print("[ERROR] uvicorn not installed. Run: pip install 'uvicorn[standard]>=0.27'", file=sys.stderr)
        sys.exit(1)

    _get_agent()
    print(f"[INFO] GenericAgent API listening on {_DEFAULT_HOST}:{_DEFAULT_PORT}")
    uvicorn.run(
        "api_server:app",
        host=_DEFAULT_HOST,
        port=_DEFAULT_PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    _main()
