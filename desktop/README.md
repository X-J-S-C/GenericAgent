# GenericAgent Desktop

基于 **Electron** + **FastAPI** 的桌面应用外壳，将 `proto_ui/` 网页 UI 包装成桌面程序，并提供系统托盘、窗口管理、后端守护等能力。

## 目录结构

```
desktop/
├── main.js          # Electron 主进程（托盘/窗口/后端进程管理）
├── preload.js       # 渲染进程 <-> 主进程安全桥
└── package.json     # npm 依赖与打包配置
```

## 前置依赖

| 依赖 | 版本要求 | 用途 |
| --- | --- | --- |
| Node.js | ≥ 16 | 运行 Electron |
| npm | 随 Node | 包管理 |
| Python | ≥ 3.10 | 运行 FastAPI 后端 |
| pip | 随 Python | `pip install fastapi uvicorn requests` |

## 快速启动

### macOS / Linux

```bash
cd desktop
npm install      # 首次安装
npm start        # 启动桌面端
```

或在项目根目录执行：

```bash
./start-desktop.sh
```

### Windows

```bat
cd desktop
npm install
npm start
```

或在项目根目录双击：

```
start-desktop.bat
```

## 使用流程

1. **启动后**：自动拉起 Python FastAPI 后端（端口 `18792`）
2. **打开主窗口**：加载 `http://127.0.0.1:18792/ui/`
3. **系统托盘**：
   - 单击 / 双击 → 显示/隐藏主窗口
   - 右键菜单 → 重启后端 / 在浏览器中打开 UI / 退出
4. **首次访问**：
   - 若后端尚未就绪，页面会显示加载动画并自动重试

## 主要功能

### 🔹 系统托盘 (Tray)
- 打开主界面 / 在浏览器中打开 UI
- 重启后端服务
- 打开项目目录
- 显示后端连接状态
- 退出

### 🔹 原生窗口控制
- 最小化 / 最大化 / 隐藏到托盘
- 窗口位置、大小、最大化状态自动持久化
- 关闭按钮 → 隐藏到托盘（不退出程序）
- 单实例锁（防止重复启动）

### 🔹 后端守护
- 启动时拉起 `python api_server.py --port 18792`
- 健康检查循环，后端启动就绪后再加载 UI
- 非预期退出时通知前端
- 托盘菜单可一键重启后端

## 打包发布

```bash
cd desktop
npm install

# Windows 安装包 + 便携版
npm run dist:win

# macOS dmg
npm run dist:mac

# Linux AppImage / deb
npm run dist:linux

# 自动按当前平台打包
npm run dist
```

打包产物位于 `desktop/dist/`。

## 架构简图

```
┌───────────────────────────────────────────────────┐
│  Electron 主进程 (main.js)                        │
│                                                    │
│  ├─ 托盘 (Tray)  ─── 系统菜单                      │
│  ├─ 主窗口 (BrowserWindow)                        │
│  ├─ preload 安全桥 (contextBridge)                │
│  └─ 子进程: python api_server.py --port 18792 ─┐  │
└──────────────────────────────────────────────────┼──┘
                                                   │
                                                   ▼
                                  FastAPI (api_server.py)
                                  ├─ /, /api/status, /api/llms, /api/chat
                                  ├─ /api/memory/*, /api/sessions/*, /api/settings
                                  ├─ /ws/{task_id}  (流式响应)
                                  └─ /ui/* (静态页面)
                                                   │
                                                   ▼
                           浏览器 / Electron 渲染进程 (proto_ui/)
                           ├─ 聊天 / 指挥家 / 蜂巢 / 变形者
                           ├─ 记忆管理 (L0-L3)
                           └─ 设置面板
```

## 配置与调试

### 修改后端端口
在 `desktop/main.js` 中修改 `DEFAULT_PORT`；
或直接使用命令行：
```bash
python api_server.py --port 9000
```

### 开发时打开 DevTools
在 `main.js` `createMainWindow()` 中取消注释：
```js
mainWindow.webContents.openDevTools({ mode: "detach" });
```

### 后端错误排查
1. 控制台查看 `[Backend]`/`[BackendErr]` 日志
2. 手动运行 `python api_server.py --port 18792`，在浏览器打开 `http://127.0.0.1:18792/ui/`，确认网页正常
3. 检查 `mykey.py` 中 DeepSeek API Key 是否有效

## 配置 LLM

编辑项目根目录的 `mykey.py`（首次运行若不存在会自动创建默认配置，指向 DeepSeek v4-pro）：

```python
native_oai_config = {
    "name": "deepseek-v4-pro",
    "apikey": "sk-你的KEY",
    "apibase": "https://api.deepseek.com/v1",
    "model": "deepseek-v4-pro",
    "api_mode": "chat_completions",
    "max_retries": 3,
    "connect_timeout": 10,
    "read_timeout": 120,
    "max_tokens": 8192,
}
```

修改后通过托盘菜单「重启后端服务」使配置生效。

## 常见问题

| 问题 | 解决方案 |
| --- | --- |
| 启动后空白页 | 检查 Python 环境是否安装 `fastapi`/`uvicorn`/`requests`，`pip install -U fastapi uvicorn requests` |
| "后端启动失败"弹窗 | 检查 `mykey.py` 是否存在；确认 Python 版本 ≥ 3.10 |
| 托盘图标在 Linux 上显示异常 | 部分 Linux 桌面环境不支持 tray，可在 `main.js` 中禁用 `createTray()` |
| Windows 上 18792 端口被占用 | 修改 `DEFAULT_PORT` 或关闭占用程序 |
