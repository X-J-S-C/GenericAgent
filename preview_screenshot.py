#!/usr/bin/env python3
"""通过 Chrome DevTools Protocol (CDP) 截图 UI 页面"""
import json
import sys
import urllib.request
import websocket
import base64
import time
import os

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
TARGET_URL = "http://localhost:8000/ui/"
OUTPUT_PATH = "/workspace/ui_screenshot.png"

def log(msg):
    print(f"  {msg}", flush=True)

def main():
    log(f"1. 连接到 CDP 端点 {CDP_HOST}:{CDP_PORT}")
    
    # 获取 WebSocket 调试 URL
    try:
        targets = json.loads(urllib.request.urlopen(f"http://{CDP_HOST}:{CDP_PORT}/json/list").read())
    except Exception as e:
        print(f"❌ 无法连接到 CDP: {e}")
        sys.exit(1)
    
    if not targets:
        # 创建一个新的页面
        try:
            urllib.request.urlopen(f"http://{CDP_HOST}:{CDP_PORT}/json/new")
            targets = json.loads(urllib.request.urlopen(f"http://{CDP_HOST}:{CDP_PORT}/json/list").read())
        except Exception as e:
            print(f"❌ 无法创建新页面: {e}")
            sys.exit(1)
    
    # 选择第一个页面或 about:blank
    page = None
    for t in targets:
        if t.get("type") == "page":
            page = t
            break
    
    if not page:
        print(f"❌ 没有找到页面目标: {targets}")
        sys.exit(1)
    
    ws_url = page["webSocketDebuggerUrl"]
    log(f"   WebSocket URL: {ws_url}")
    
    # 连接
    log(f"2. 建立 WebSocket 连接")
    ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
    
    msg_id = [0]
    def send(method, params=None):
        msg_id[0] += 1
        payload = {"id": msg_id[0], "method": method, "params": params or {}}
        ws.send(json.dumps(payload))
        return msg_id[0]
    
    def recv(timeout_sec=30):
        ws.settimeout(timeout_sec)
        return json.loads(ws.recv())
    
    def recv_result(wait_id, timeout_sec=30):
        start = time.time()
        while time.time() - start < timeout_sec:
            msg = recv(timeout_sec - (time.time() - start))
            if msg.get("id") == wait_id:
                if "error" in msg:
                    raise Exception(f"CDP 错误: {msg['error']}")
                return msg.get("result", {})
            # 如果是事件，忽略
        raise Exception(f"超时等待消息 #{wait_id}")
    
    # 启用必要的 domain
    log(f"3. 启用 Page 和 Network domain")
    send("Page.enable")
    recv_result(msg_id[0])
    send("Network.enable")
    recv_result(msg_id[0])
    
    # 导航到页面
    log(f"4. 导航到 {TARGET_URL}")
    send("Page.navigate", {"url": TARGET_URL})
    nav_id = msg_id[0]
    
    # 等待 Page.loadEventFired
    start = time.time()
    while time.time() - start < 30:
        msg = recv(10)
        if msg.get("method") == "Page.loadEventFired":
            log("   页面加载完成!")
            break
        if msg.get("method") == "Page.frameStoppedLoading":
            log("   框架停止加载")
        if msg.get("method") == "Network.responseReceived":
            resp = msg.get("params", {}).get("response", {})
            log(f"   Network response: {resp.get('status')} {resp.get('url', '')[:60]}")
    
    # 额外等待 2 秒让 CSS/JS 渲染
    time.sleep(2)
    
    # 设置窗口大小
    log(f"5. 设置视口大小")
    send("Emulation.setDeviceMetricsOverride", {
        "width": 1440,
        "height": 900,
        "deviceScaleFactor": 1,
        "mobile": False
    })
    recv_result(msg_id[0])
    
    # 截图
    log(f"6. 截图")
    send("Page.captureScreenshot", {
        "format": "png",
        "fromSurface": True,
        "captureBeyondViewport": True
    })
    result = recv_result(msg_id[0], 30)
    
    # 保存图片
    data = base64.b64decode(result["data"])
    with open(OUTPUT_PATH, "wb") as f:
        f.write(data)
    
    log(f"✅ 截图保存到 {OUTPUT_PATH} ({len(data)} 字节)")
    
    # 获取页面标题
    send("Runtime.evaluate", {"expression": "document.title"})
    title_result = recv_result(msg_id[0])
    log(f"   页面标题: {title_result.get('result', {}).get('value', '(空)')}")
    
    send("Runtime.evaluate", {"expression": "document.querySelector('#llm-badge')?.textContent || 'N/A'"})
    badge_result = recv_result(msg_id[0])
    log(f"   LLM badge: {badge_result.get('result', {}).get('value', 'N/A')}")
    
    ws.close()
    print(f"\n🎉 预览截图已生成: {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
