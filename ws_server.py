"""
WebSocket 服务端 —— 任务 02 + 任务 05（会话隔离改造）

协议：
  C -> S:  { "type": "chat", "session_id": str?, "query": str, "images": [str]? }
           { "type": "cmd",  "action": "list_sessions" }
           { "type": "cmd",  "action": "switch_session", "session_id": str }
           { "type": "cmd",  "action": "new_session",    "title": str? }
           { "type": "cmd",  "action": "delete_session", "session_id": str }
           { "type": "cmd",  "action": "rename_session", "session_id": str, "title": str }
           { "type": "ping" }

  S -> C:  { "type": "chunk",  "session_id": str, "text": str }
           { "type": "done",   "session_id": str, "text": str }
           { "type": "system", "session_id": str?, "text": str, "sessions": [..]? }
           { "type": "pong",   "server_time": float }
           { "type": "error",  "message": str }

每个 WebSocket 连接会记住一个 "current_session_id"（默认 default，也可用
客户端首条消息 / cmd 里的 session_id 切换）。不同连接 / 不同 session_id
之间完全隔离 history 与 handler。
"""

import asyncio
import json
import os
import sys
import time
import uuid
import threading
from typing import Optional, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import websockets
except ImportError:
    print('[ERROR] 需要 websockets 库。请先: pip install websockets')
    sys.exit(1)

from agentmain import (
    get_session, list_sessions, delete_session,
    chat_once, _DEFAULT_SESSION_ID, _gen_session_id,
)


DEFAULT_HOST = os.environ.get('GA_WS_HOST', '0.0.0.0')
DEFAULT_PORT = int(os.environ.get('GA_WS_PORT', '8765'))
REQUEST_TIMEOUT = int(os.environ.get('GA_WS_TIMEOUT', '600'))  # 单条请求最大等待秒

# 连接级的会话 ID：每个 ws 连接独立记忆
_current_conn_sessions: Dict[str, str] = {}  # conn_id -> session_id
_conn_sessions_lock = threading.Lock()


def _session_list_payload(current_id: Optional[str] = None):
    try:
        items = list_sessions()
    except Exception:
        items = []
    return {
        'type': 'system',
        'text': 'sessions',
        'session_id': current_id or _DEFAULT_SESSION_ID,
        'sessions': items,
        'current_session_id': current_id or _DEFAULT_SESSION_ID,
    }


def _resolve_session_id(conn_id: str, msg: Dict[str, Any]) -> str:
    """优先使用消息里的 session_id；否则使用连接当前记住的；最后 default。"""
    explicit = (msg.get('session_id') or '').strip()
    if explicit:
        with _conn_sessions_lock:
            _current_conn_sessions[conn_id] = explicit
        return explicit
    with _conn_sessions_lock:
        return _current_conn_sessions.get(conn_id) or _DEFAULT_SESSION_ID


async def _drain_queue_to_ws(websocket, display_queue, session_id: str, timeout: int):
    """同步把 agent 产出的 chunk / done 桥接到 WebSocket。"""
    loop = asyncio.get_event_loop()
    started = time.time()
    while True:
        if time.time() - started > timeout:
            await websocket.send(json.dumps({
                'type': 'error', 'session_id': session_id,
                'message': f'请求超时 ({timeout}s)，已中断。',
            }, ensure_ascii=False))
            return
        try:
            item = await loop.run_in_executor(None, display_queue.get, True, 1.0)
        except Exception:
            # queue empty → 继续等（同时给一次 ping 保持连接）
            try:
                pong_waiter = await websocket.ping()
                await asyncio.wait_for(pong_waiter, timeout=5)
            except Exception:
                pass
            continue

        if not isinstance(item, dict):
            continue

        if 'next' in item:
            try:
                text = str(item['next'])
            except Exception:
                text = ''
            if text:
                await websocket.send(json.dumps({
                    'type': 'chunk', 'session_id': session_id, 'text': text,
                }, ensure_ascii=False))

        if 'done' in item:
            try:
                text = str(item['done'])
            except Exception:
                text = ''
            payload = {'type': 'done', 'session_id': session_id, 'text': text}
            new_sid = item.get('new_session_id')
            if new_sid:
                # /session.new / /session.switch 返回的新会话
                payload['new_session_id'] = new_sid
                with _conn_sessions_lock:
                    _current_conn_sessions[conn_id_of(websocket)] = new_sid
            # 附带最新会话列表，方便前端刷新
            try:
                payload['sessions'] = list_sessions()
            except Exception:
                pass
            await websocket.send(json.dumps(payload, ensure_ascii=False))
            return


_conn_id_map: Dict[int, str] = {}
_conn_id_counter = 0
_conn_id_counter_lock = threading.Lock()


def conn_id_of(websocket) -> str:
    key = id(websocket)
    cid = _conn_id_map.get(key)
    if cid is None:
        with _conn_id_counter_lock:
            global _conn_id_counter
            _conn_id_counter += 1
            cid = f"conn_{_conn_id_counter}"
        _conn_id_map[key] = cid
    return cid


async def handle_client(websocket):
    conn_id = conn_id_of(websocket)
    try:
        await websocket.send(json.dumps({
            'type': 'system',
            'session_id': _DEFAULT_SESSION_ID,
            'text': f'[Connected] conn={conn_id}。默认会话: {_DEFAULT_SESSION_ID}。可发送 /session.new /session.list /session.switch <id>。',
            'sessions': list_sessions(),
            'current_session_id': _DEFAULT_SESSION_ID,
        }, ensure_ascii=False))

        async for raw in websocket:
            try:
                msg = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode('utf-8'))
            except Exception:
                await websocket.send(json.dumps({'type': 'error', 'message': 'JSON 解析失败'}, ensure_ascii=False))
                continue

            # ping / pong
            if msg.get('type') == 'ping':
                await websocket.send(json.dumps({'type': 'pong', 'server_time': time.time()}, ensure_ascii=False))
                continue

            # 会话列表 / 切换 / 新建 / 删除 / 重命名 —— 控制命令
            if msg.get('type') == 'cmd':
                action = str(msg.get('action', '')).strip()
                try:
                    if action == 'list_sessions':
                        cur = _resolve_session_id(conn_id, msg)
                        await websocket.send(json.dumps(_session_list_payload(cur), ensure_ascii=False))
                        continue
                    if action == 'switch_session':
                        target = str(msg.get('session_id') or '').strip()
                        if not target:
                            await websocket.send(json.dumps({'type': 'error', 'message': '缺少 session_id'}, ensure_ascii=False))
                            continue
                        _ = get_session(target, create=False) or get_session(target, create=True)
                        with _conn_sessions_lock:
                            _current_conn_sessions[conn_id] = target
                        await websocket.send(json.dumps({
                            'type': 'system', 'session_id': target,
                            'text': f'已切换到会话 {target}',
                            'sessions': list_sessions(),
                            'current_session_id': target,
                        }, ensure_ascii=False))
                        continue
                    if action == 'new_session':
                        title = str(msg.get('title') or '').strip() or None
                        new_sid = _gen_session_id(title)
                        ns = get_session(new_sid, create=True)
                        if title: ns.set_title(title)
                        with _conn_sessions_lock:
                            _current_conn_sessions[conn_id] = new_sid
                        await websocket.send(json.dumps({
                            'type': 'system', 'session_id': new_sid,
                            'text': f'已创建并切换到新会话 {new_sid}',
                            'new_session_id': new_sid,
                            'sessions': list_sessions(),
                            'current_session_id': new_sid,
                        }, ensure_ascii=False))
                        continue
                    if action == 'delete_session':
                        target = str(msg.get('session_id') or '').strip()
                        ok, msg_text = delete_session(target)
                        # 若被删除的是当前会话，切回 default
                        with _conn_sessions_lock:
                            if _current_conn_sessions.get(conn_id) == target:
                                _current_conn_sessions[conn_id] = _DEFAULT_SESSION_ID
                        cur = _current_conn_sessions.get(conn_id) or _DEFAULT_SESSION_ID
                        await websocket.send(json.dumps({
                            'type': 'system', 'session_id': cur,
                            'text': msg_text,
                            'sessions': list_sessions(),
                            'current_session_id': cur,
                        }, ensure_ascii=False))
                        continue
                    if action == 'rename_session':
                        target = str(msg.get('session_id') or '').strip()
                        title = str(msg.get('title') or '').strip()
                        if not target or not title:
                            await websocket.send(json.dumps({'type': 'error', 'message': '缺少 session_id 或 title'}, ensure_ascii=False))
                            continue
                        s = get_session(target, create=False)
                        if s is None:
                            await websocket.send(json.dumps({'type': 'error', 'message': f'会话 {target} 不存在'}, ensure_ascii=False))
                            continue
                        s.set_title(title)
                        await websocket.send(json.dumps({
                            'type': 'system', 'session_id': target,
                            'text': f'会话 {target} 已重命名为「{title}」',
                            'sessions': list_sessions(),
                            'current_session_id': target,
                        }, ensure_ascii=False))
                        continue
                    await websocket.send(json.dumps({'type': 'error', 'message': f'未知 action: {action}'}, ensure_ascii=False))
                    continue
                except Exception as e:
                    await websocket.send(json.dumps({'type': 'error', 'message': f'命令执行失败: {e}'}, ensure_ascii=False))
                    continue

            # chat 消息 —— 走 agent 主循环
            if msg.get('type') != 'chat':
                await websocket.send(json.dumps({'type': 'error', 'message': '未知消息类型，期望 type=chat/cmd/ping'}, ensure_ascii=False))
                continue

            query = str(msg.get('query') or '').strip()
            if not query:
                await websocket.send(json.dumps({'type': 'error', 'message': 'query 为空'}, ensure_ascii=False))
                continue

            sid = _resolve_session_id(conn_id, msg)
            images = msg.get('images') or []

            try:
                display_queue, _s = chat_once(sid, query, source='ws', images=images, create=True)
            except Exception as e:
                await websocket.send(json.dumps({'type': 'error', 'session_id': sid, 'message': str(e)}, ensure_ascii=False))
                continue

            await _drain_queue_to_ws(websocket, display_queue, session_id=sid, timeout=REQUEST_TIMEOUT)
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as e:
        try:
            await websocket.send(json.dumps({'type': 'error', 'message': f'连接异常: {e}'}, ensure_ascii=False))
        except Exception:
            pass
    finally:
        # 清理连接级状态
        with _conn_sessions_lock:
            _current_conn_sessions.pop(conn_id, None)
        _conn_id_map.pop(id(websocket), None)


async def main():
    print(f'[GA WS] 监听 {DEFAULT_HOST}:{DEFAULT_PORT} (会话隔离已启用)')
    async with websockets.serve(handle_client, DEFAULT_HOST, DEFAULT_PORT,
                                 ping_interval=30, ping_timeout=20,
                                 max_size=None):
        await asyncio.Future()  # run forever


if __name__ == '__main__':
    asyncio.run(main())
