#!/usr/bin/env python3
"""
GenericAgent 统一启动脚本
-----------------------
启动后端 API 服务 + Web UI，可选启动各平台 Bot 和桌面宠物。

用法:
  python start.py                    # 仅启动 API + Web UI
  python start.py --api-only         # 仅 API 服务
  python start.py --ui-port 18793    # 自定义 UI 端口
  python start.py --tg               # 启动 Telegram Bot
  python start.py --feishu          # 启动飞书 Bot
  python start.py --qq              # 启动 QQ Bot
  python start.py --wecom           # 启动企业微信 Bot
  python start.py --dingtalk        # 启动钉钉 Bot
  python start.py --pet             # 启动桌面宠物 (pywebview)
  python start.py --llm 1           # 指定 LLM 编号 (默认 0)
  python start.py --bg               # 后台运行
"""
import argparse
import atexit
import os
import socket
import subprocess
import sys
import threading
import time

script_dir = os.path.dirname(os.path.abspath(__file__))
frontends_dir = os.path.join(script_dir, "frontends")
_api_port = 18792
_ui_port = 18793
_running_procs = []


def find_free_port(lo=18700, hi=18799):
    import random
    ports = list(range(lo, hi + 1))
    random.shuffle(ports)
    for p in ports:
        try:
            s = socket.socket()
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            continue
    raise RuntimeError(f"No free port in {lo}-{hi}")


def cleanup():
    for p in _running_procs:
        try:
            p.terminate()
            p.wait(timeout=3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def start_api(port):
    """启动 FastAPI 后端服务 (uvicorn)"""
    cmd = [
        sys.executable, "-m", "uvicorn",
        "api_server:app",
        "--host", "0.0.0.0",
        "--port", str(port),
        "--reload",
    ]
    env = os.environ.copy()
    proc = subprocess.Popen(cmd, cwd=script_dir, env=env)
    _running_procs.append(proc)
    atexit.register(lambda: proc.terminate())
    print(f"[start.py] API server running at http://localhost:{port}")
    return proc


def start_api_standalone(port):
    """启动独立 API 进程（供 --bg 模式外部管理）"""
    cmd = [
        sys.executable, "-m", "uvicorn",
        "api_server:app",
        "--host", "0.0.0.0",
        "--port", str(port),
    ]
    proc = subprocess.Popen(cmd, cwd=script_dir)
    print(f"[start.py] API server (PID {proc.pid}) at http://localhost:{port}")
    return proc


def check_api_alive(port, timeout=10):
    """等待 API 服务就绪"""
    import requests
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"http://localhost:{port}/", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def start_browser(url):
    """打开默认浏览器"""
    import webbrowser
    webbrowser.open(url)


# ─── Bot launchers ────────────────────────────────────────────────────────────

def start_tg_bot():
    path = os.path.join(frontends_dir, "tgapp.py")
    if not os.path.exists(path):
        print(f"[start.py] tgapp.py not found, skipping Telegram Bot")
        return
    proc = subprocess.Popen([sys.executable, path])
    _running_procs.append(proc)
    atexit.register(lambda: proc.terminate())
    print("[start.py] Telegram Bot started")


def start_feishu_bot():
    path = os.path.join(frontends_dir, "fsapp.py")
    if not os.path.exists(path):
        print(f"[start.py] fsapp.py not found, skipping Feishu Bot")
        return
    proc = subprocess.Popen([sys.executable, path])
    _running_procs.append(proc)
    atexit.register(lambda: proc.terminate())
    print("[start.py] Feishu Bot started")


def start_qq_bot():
    path = os.path.join(frontends_dir, "qqapp.py")
    if not os.path.exists(path):
        print(f"[start.py] qqapp.py not found, skipping QQ Bot")
        return
    proc = subprocess.Popen([sys.executable, path])
    _running_procs.append(proc)
    atexit.register(lambda: proc.terminate())
    print("[start.py] QQ Bot started")


def start_wecom_bot():
    path = os.path.join(frontends_dir, "wecomapp.py")
    if not os.path.exists(path):
        print(f"[start.py] wecomapp.py not found, skipping WeCom Bot")
        return
    proc = subprocess.Popen([sys.executable, path])
    _running_procs.append(proc)
    atexit.register(lambda: proc.terminate())
    print("[start.py] WeCom Bot started")


def start_dingtalk_bot():
    path = os.path.join(frontends_dir, "dingtalkapp.py")
    if not os.path.exists(path):
        print(f"[start.py] dingtalkapp.py not found, skipping DingTalk Bot")
        return
    proc = subprocess.Popen([sys.executable, path])
    _running_procs.append(proc)
    atexit.register(lambda: proc.terminate())
    print("[start.py] DingTalk Bot started")


def start_desktop_pet():
    """启动桌面宠物 (webview 窗口)"""
    try:
        import webview
    except ImportError:
        print("[start.py] pywebview not installed, skipping desktop pet (pip install pywebview)")
        return

    pet_window = None

    def pet_thread():
        nonlocal pet_window
        url = f"http://localhost:{_api_port}/ui"
        if not os.path.exists(os.path.join(script_dir, "proto_ui")):
            url = f"http://localhost:{_api_port}/"
        pet_window = webview.create_window(
            title="GenericAgent Pet",
            url=url,
            width=200, height=260,
            resizable=False, frameless=True,
            always_on_top=True,
        )
        webview.start()

    t = threading.Thread(target=pet_thread, daemon=True)
    t.start()
    print("[start.py] Desktop pet started (pywebview)")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GenericAgent 统一启动脚本")
    parser.add_argument("--api-only", action="store_true", help="仅启动 API 服务，不开 UI")
    parser.add_argument("--ui-port", type=int, default=_ui_port, help=f"Web UI 端口 (默认 {_ui_port})")
    parser.add_argument("--llm", type=int, default=0, dest="llm_no", help="LLM 编号 (默认 0)")
    parser.add_argument("--tg", action="store_true", help="启动 Telegram Bot")
    parser.add_argument("--feishu", action="store_true", help="启动飞书 Bot")
    parser.add_argument("--qq", action="store_true", help="启动 QQ Bot")
    parser.add_argument("--wecom", action="store_true", help="启动企业微信 Bot")
    parser.add_argument("--dingtalk", action="store_true", help="启动钉钉 Bot")
    parser.add_argument("--pet", action="store_true", help="启动桌面宠物")
    parser.add_argument("--bg", action="store_true", help="后台运行 (不阻塞终端)")
    parser.add_argument("--no-browser", action="store_true", help="启动后不打开浏览器")
    args = parser.parse_args()

    # Resolve API port
    global _api_port
    _api_port = find_free_port(18700, 18799)
    print(f"[start.py] === GenericAgent ===")
    print(f"[start.py] API port: {_api_port}")

    if args.bg:
        proc = start_api_standalone(_api_port)
        print(f"[start.py] Running in background (PID {proc.pid})")
        print(f"[start.py] UI available at http://localhost:{_api_port}/ui")
        return

    # Start API server
    api_proc = start_api(_api_port)

    # Wait for API to be ready
    print("[start.py] Waiting for API server to be ready...")
    import requests
    if not check_api_alive(_api_port, timeout=15):
        print("[start.py] ERROR: API server failed to start. Check logs above.")
        sys.exit(1)
    print("[start.py] API server ready.")

    # Start bots (async, don't wait)
    if args.tg:       start_tg_bot()
    if args.feishu:   start_feishu_bot()
    if args.qq:       start_qq_bot()
    if args.wecom:    start_wecom_bot()
    if args.dingtalk: start_dingtalk_bot()

    # UI or API-only mode
    if args.api_only:
        print("[start.py] API-only mode. Press Ctrl+C to stop.")
        try:
            api_proc.wait()
        except KeyboardInterrupt:
            print("\n[start.py] Shutting down...")
    else:
        ui_url = f"http://localhost:{_api_port}/ui"
        if not args.no_browser:
            time.sleep(1)
            start_browser(ui_url)

        print(f"[start.py] Web UI: {ui_url}")
        print("[start.py] Press Ctrl+C to stop.")

        if args.pet:
            start_desktop_pet()

        try:
            api_proc.wait()
        except KeyboardInterrupt:
            print("\n[start.py] Shutting down...")


if __name__ == "__main__":
    main()
