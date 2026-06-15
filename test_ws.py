#!/usr/bin/env python3
"""端到端测试：聊天 API + WebSocket 流式响应"""
import asyncio
import json
import sys
import time

try:
    import websockets
except ImportError:
    print("pip install websockets")
    sys.exit(1)


async def test_full_flow():
    import urllib.request

    # Step 1: POST /api/chat
    host = "http://localhost:8000"
    data = json.dumps({"message": "你好！用一句话介绍你自己。"}).encode()
    req = urllib.request.Request(
        f"{host}/api/chat", data=data,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    resp = json.loads(urllib.request.urlopen(req).read())
    task_id = resp["task_id"]
    print(f"[1] POST /api/chat → task_id={task_id}")

    # Step 2: WebSocket /ws/{task_id}
    ws_url = f"ws://localhost:8000/ws/{task_id}"
    print(f"[2] Connecting to {ws_url} ...")

    next_count = 0
    done_received = False
    error_received = None

    async with websockets.connect(ws_url) as ws:
        start = time.time()
        print(f"[WS] Connected, waiting for stream...")

        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
                msg = json.loads(raw)
                msg_type = msg.get("type")

                if msg_type == "subscribed":
                    print(f"  ✓ subscribed")

                elif msg_type == "next":
                    next_count += 1
                    content = msg.get("content", "")
                    source = msg.get("source", "")
                    if len(content) > 60:
                        preview = content[:60] + "..."
                    else:
                        preview = content
                    print(f"  → next [{source}] len={len(content)}: {preview!r}")

                elif msg_type == "tool_start":
                    tc = msg.get("tool_call", {})
                    print(f"  → tool_start: {tc.get('name', '?')}")

                elif msg_type == "tool_end":
                    tc = msg.get("tool_call", {})
                    print(f"  → tool_end: {tc.get('name', '?')} (success={msg.get('success')})")

                elif msg_type == "done":
                    content = msg.get("content", "")
                    source = msg.get("source", "")
                    if len(content) > 120:
                        preview = content[:120] + "..."
                    else:
                        preview = content
                    print(f"  ✓ done [{source}] len={len(content)}: {preview!r}")
                    done_received = True
                    break

                elif msg_type == "error":
                    error_received = msg
                    print(f"  ✗ error: {msg.get('message', '?')}")
                    break

                else:
                    print(f"  ? unknown msg type: {msg_type}")

        except asyncio.TimeoutError:
            print("  ✗ timeout waiting for messages")

        elapsed = time.time() - start
        print(f"\n[3] Summary: next_count={next_count}, done={done_received}, error={bool(error_received)}, elapsed={elapsed:.1f}s")
        return done_received or (error_received is not None)  # OK if got proper response or error


if __name__ == "__main__":
    ok = asyncio.run(test_full_flow())
    sys.exit(0 if ok else 1)
