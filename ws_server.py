"""
WebSocket Server for GenericAgent
- _TASK_REGISTRY: thread-safe task registry
- WebSocket endpoint with 5-minute timeout
"""

import json
import threading
import time
import uuid
from typing import Dict, Optional, Any
from datetime import datetime

# _TASK_REGISTRY: task_id -> {status, created_at, last_active, client, ...}
_TASK_REGISTRY: Dict[str, Dict[str, Any]] = {}
_TASK_LOCK = threading.Lock()

# WebSocket timeout in seconds (5 minutes)
WS_TIMEOUT = 300


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Get task info from registry (thread-safe)."""
    with _TASK_LOCK:
        return _TASK_REGISTRY.get(task_id)


def register_task(task_id: str, client=None) -> Dict[str, Any]:
    """Register a new task (thread-safe)."""
    with _TASK_LOCK:
        info = {
            'task_id': task_id,
            'status': 'active',
            'created_at': time.time(),
            'last_active': time.time(),
            'client': client,
        }
        _TASK_REGISTRY[task_id] = info
        return info


def update_task_activity(task_id: str) -> bool:
    """Update last_active timestamp (thread-safe)."""
    with _TASK_LOCK:
        if task_id in _TASK_REGISTRY:
            _TASK_REGISTRY[task_id]['last_active'] = time.time()
            return True
        return False


def set_task_status(task_id: str, status: str) -> bool:
    """Set task status (thread-safe)."""
    with _TASK_LOCK:
        if task_id in _TASK_REGISTRY:
            _TASK_REGISTRY[task_id]['status'] = status
            _TASK_REGISTRY[task_id]['last_active'] = time.time()
            return True
        return False


def unregister_task(task_id: str) -> bool:
    """Remove task from registry (thread-safe)."""
    with _TASK_LOCK:
        if task_id in _TASK_REGISTRY:
            del _TASK_REGISTRY[task_id]
            return True
        return False


def list_tasks(status: Optional[str] = None) -> list:
    """List all tasks, optionally filtered by status (thread-safe)."""
    with _TASK_LOCK:
        tasks = []
        for tid, info in _TASK_REGISTRY.items():
            if status is None or info['status'] == status:
                tasks.append({
                    'task_id': tid,
                    'status': info['status'],
                    'created_at': datetime.fromtimestamp(info['created_at']).isoformat(),
                    'last_active': datetime.fromtimestamp(info['last_active']).isoformat(),
                    'age_seconds': time.time() - info['created_at'],
                })
        return tasks


def cleanup_stale_tasks(timeout: int = WS_TIMEOUT) -> int:
    """Remove tasks with no activity beyond timeout. Returns count of removed."""
    removed = 0
    now = time.time()
    with _TASK_LOCK:
        stale = [
            tid for tid, info in _TASK_REGISTRY.items()
            if info['status'] == 'active' and now - info['last_active'] > timeout
        ]
        for tid in stale:
            del _TASK_REGISTRY[tid]
            removed += 1
    return removed


def start_cleanup_thread(interval: int = 60):
    """Start background thread to cleanup stale tasks."""
    def _cleanup():
        while True:
            time.sleep(interval)
            removed = cleanup_stale_tasks()
            if removed > 0:
                print(f"[WS] Cleaned up {removed} stale tasks")
    
    t = threading.Thread(target=_cleanup, daemon=True)
    t.start()
    return t


class WebSocketHandler:
    """Handler for WebSocket client connections."""
    
    def __init__(self, ws, task_id: str):
        self.ws = ws
        self.task_id = task_id
        self.last_ping = time.time()
    
    async def send_json(self, data: dict):
        """Send JSON data to client."""
        try:
            await self.ws.send(json.dumps(data))
        except Exception as e:
            print(f"[WS] Send error: {e}")
    
    async def send_message(self, type: str, content: Any, **kwargs):
        """Send a message with type and content."""
        msg = {'type': type, 'content': content, 'task_id': self.task_id, 'timestamp': time.time()}
        msg.update(kwargs)
        await self.send_json(msg)
    
    async def handle_ping(self):
        """Handle ping from client, respond with pong and update activity."""
        self.last_ping = time.time()
        update_task_activity(self.task_id)
        await self.send_message('pong', 'alive')
    
    async def handle_message(self, data: str):
        """Handle incoming message from client."""
        update_task_activity(self.task_id)
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            await self.send_message('error', 'Invalid JSON')
            return
        
        msg_type = msg.get('type', '')
        
        if msg_type == 'ping':
            await self.handle_ping()
        elif msg_type == 'query':
            # Forward query to agent
            await self.send_message('ack', 'Query received')
        else:
            await self.send_message('error', f'Unknown message type: {msg_type}')


def create_app(agent):
    """Create WebSocket app with FastAPI or Bottle.
    Returns the WSGI app and a run function.
    """
    # Use Bottle for simplicity
    import bottle
    from bottle import Bottle, request, abort
    
    app = Bottle()
    
    @app.route('/ws/<task_id>')
    def websocket_endpoint(task_id: str):
        # Upgrade to WebSocket
        try:
            ws = request.environ.get('wsgi.websocket')
        except Exception:
            abort(400, 'WebSocket not supported')
        
        if ws is None:
            abort(400, 'WebSocket connection required')
        
        # Register task
        handler = WebSocketHandler(ws, task_id)
        register_task(task_id, ws)
        
        try:
            # Send welcome message
            handler.send_json({
                'type': 'connected',
                'task_id': task_id,
                'timestamp': time.time(),
            })
            
            # Message loop
            while True:
                msg = ws.receive()
                if msg is None:
                    break
                # Note: Bottle's websocket expects text frame
                if isinstance(msg, str):
                    # Run async handler in sync context for simplicity
                    import asyncio
                    try:
                        loop = asyncio.get_event_loop()
                    except RuntimeError:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                    loop.run_until_complete(handler.handle_message(msg))
                else:
                    # Binary message - ignore
                    pass
        finally:
            unregister_task(task_id)
    
    @app.route('/tasks')
    def list_tasks_endpoint():
        """List all registered tasks."""
        status = request.query.status or None
        return {'tasks': list_tasks(status)}
    
    @app.route('/tasks/<task_id>')
    def get_task_endpoint(task_id: str):
        """Get info about a specific task."""
        info = get_task(task_id)
        if info is None:
            abort(404, 'Task not found')
        return {
            'task_id': task_id,
            'status': info['status'],
            'created_at': datetime.fromtimestamp(info['created_at']).isoformat(),
            'last_active': datetime.fromtimestamp(info['last_active']).isoformat(),
        }
    
    @app.route('/health')
    def health():
        """Health check endpoint."""
        return {'status': 'ok', 'tasks': len(_TASK_REGISTRY)}
    
    return app


def run_server(agent=None, host='0.0.0.0', port=8765, threaded=True):
    """Run the WebSocket server."""
    app = create_app(agent)
    
    # Start cleanup thread
    start_cleanup_thread(interval=60)
    
    print(f"[WS] Starting server on {host}:{port}")
    app.run(host=host, port=port, threaded=threaded)


if __name__ == '__main__':
    # Test server without agent
    print("[WS] Starting standalone server (no agent)...")
    run_server(agent=None, host='0.0.0.0', port=8765)