/* ============================================================================
 * GenericAgent Desktop — Electron Main Process
 * ----------------------------------------------------------------------------
 * 功能：
 *   1. 启动 & 守护 Python FastAPI 后端
 *   2. 创建主窗口（加载 proto_ui/index.html）
 *   3. 系统托盘（菜单：显示/隐藏 / 重启 / 设置 / 退出）
 *   4. 窗口状态持久化（位置、大小、最大化状态）
 *   5. preload 安全桥：窗口控制 API + 后端健康检查
 *
 * 运行:
 *   cd desktop
 *   npm install
 *   npm start
 * ========================================================================== */

const {
  app,
  BrowserWindow,
  Tray,
  Menu,
  ipcMain,
  nativeImage,
  shell,
  dialog,
  Notification,
} = require("electron");

const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");
const http = require("http");

// ─── 全局引用 ───────────────────────────────────────────────────────────────
let mainWindow = null;
let tray = null;
let backendProcess = null;
let quitting = false;
const isDev = !app.isPackaged;

// ─── 常量 & 路径 ────────────────────────────────────────────────────────────
const APP_NAME = "GenericAgent";
const DEFAULT_PORT = 18792;
const UI_URL = `http://127.0.0.1:${DEFAULT_PORT}/ui/`;

const appDir = app.isPackaged
  ? path.dirname(process.execPath)          // 打包后：exe 同级
  : path.join(__dirname, "..");              // 开发时：项目根

const pythonProjectDir = appDir;
const backendScript = path.join(pythonProjectDir, "api_server.py");
const settingsFile = path.join(
  app.getPath("userData"),
  "window-state.json"
);

// ─── 窗口状态持久化 ──────────────────────────────────────────────────────────
function loadWindowState() {
  try {
    return JSON.parse(fs.readFileSync(settingsFile, "utf-8"));
  } catch {
    return { width: 1280, height: 800, maximized: false };
  }
}
function saveWindowState() {
  if (!mainWindow) return;
  try {
    const [x, y] = mainWindow.getPosition();
    const [width, height] = mainWindow.getSize();
    fs.writeFileSync(
      settingsFile,
      JSON.stringify(
        {
          x,
          y,
          width,
          height,
          maximized: mainWindow.isMaximized(),
        },
        null,
        2
      ),
      "utf-8"
    );
  } catch {}
}

// ─── Python 后端生命周期 ────────────────────────────────────────────────────
function findPythonExecutable() {
  // Windows 上优先 py launcher，其次 python / python3
  const candidates = process.platform === "win32"
    ? ["py -3", "python", "python3"]
    : ["python3", "python"];
  return candidates[0]; // 简化：取第一个，也可做 exists 检查
}

function startBackend() {
  return new Promise((resolve, reject) => {
    if (backendProcess && !backendProcess.killed) {
      resolve(true);
      return;
    }

    // 先检查 Python 文件存在
    if (!fs.existsSync(backendScript)) {
      console.error("[Desktop] api_server.py 找不到:", backendScript);
      reject(new Error("后端文件不存在"));
      return;
    }

    const pythonBin = findPythonExecutable();
    console.log(`[Desktop] 启动后端: ${pythonBin} ${backendScript}`);

    try {
      backendProcess = spawn(
        pythonBin,
        [backendScript, "--port", String(DEFAULT_PORT)],
        {
          cwd: pythonProjectDir,
          stdio: ["ignore", "pipe", "pipe"],
          windowsHide: true,
        }
      );
    } catch (e) {
      console.error("[Desktop] spawn 失败:", e);
      reject(e);
      return;
    }

    backendProcess.stdout.on("data", (d) => {
      const line = d.toString().trim();
      if (line) console.log("[Backend]", line);
    });
    backendProcess.stderr.on("data", (d) => {
      const line = d.toString().trim();
      if (line && !line.includes("uvicorn")) console.error("[BackendErr]", line);
    });

    backendProcess.on("exit", (code) => {
      console.log(`[Desktop] 后端退出 code=${code}`);
      if (code !== 0 && mainWindow) {
        // 非预期退出 — 通知前端
        mainWindow.webContents.send("backend:crashed", { code });
      }
      backendProcess = null;
    });

    // 健康检查：最多等 15 秒
    const start = Date.now();
    const tryCheck = () => {
      httpGetHealth((ok) => {
        if (ok) {
          console.log("[Desktop] 后端已就绪");
          resolve(true);
        } else if (Date.now() - start > 15000) {
          console.error("[Desktop] 后端启动超时");
          reject(new Error("后端启动超时"));
        } else {
          setTimeout(tryCheck, 800);
        }
      });
    };
    setTimeout(tryCheck, 1500);
  });
}

function stopBackend() {
  return new Promise((resolve) => {
    if (!backendProcess || backendProcess.killed) {
      resolve();
      return;
    }
    console.log("[Desktop] 停止后端进程...");
    backendProcess.kill("SIGTERM");

    setTimeout(() => {
      if (backendProcess && !backendProcess.killed) {
        try { backendProcess.kill("SIGKILL"); } catch {}
      }
      resolve();
    }, 1500);
  });
}

function httpGetHealth(cb) {
  const req = http.get(`http://127.0.0.1:${DEFAULT_PORT}/`, (res) => {
    cb(res.statusCode === 200);
  });
  req.on("error", () => cb(false));
  req.setTimeout(1000, () => { cb(false); req.destroy(); });
}

// ─── 主窗口 ─────────────────────────────────────────────────────────────────
function createMainWindow() {
  const state = loadWindowState();

  mainWindow = new BrowserWindow({
    title: APP_NAME,
    x: state.x,
    y: state.y,
    width: state.width || 1280,
    height: state.height || 800,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: "#1f1d1a",
    icon: getAppIcon(),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      spellcheck: false,
    },
  });

  if (state.maximized) mainWindow.maximize();

  // 加载 UI (从后端 http 加载，复用 proto_ui)
  mainWindow.loadURL(UI_URL).catch((err) => {
    console.error("[Desktop] 加载 UI 失败:", err);
  });

  // 保存窗口状态
  ["resize", "move", "close"].forEach((evt) => {
    mainWindow.on(evt, () => saveWindowState());
  });

  mainWindow.on("close", (e) => {
    if (!quitting) {
      e.preventDefault();
      mainWindow.hide();
    }
  });

  mainWindow.on("closed", () => { mainWindow = null; });

  // 打开 devtools (仅开发模式)
  if (isDev) {
    mainWindow.webContents.once("did-finish-load", () => {
      // mainWindow.webContents.openDevTools({ mode: "detach" });
    });
  }
}

// ─── 系统托盘 ────────────────────────────────────────────────────────────────
function getAppIcon() {
  // 使用一个简单的 base64 PNG（16x16 橙色图标）
  const png = nativeImage.createFromDataURL(
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAWklEQVQ4jWNgGAWjYBSMglEwCkbBKBgFotDwHwMDAwMDI8fGKGRkZkJSQYGRiRGDAygLAL0H9QMQGwMDI8fGKGRkZEJcY2Rk5GRiRGJ0pGRiRGRi0MDEyMDJiZGAAAJwIDBxz5uHQAAAABJRU5ErkJggg=="
  );
  return png.isEmpty() ? undefined : png;
}

function createTray() {
  try {
    const icon = getAppIcon() || nativeImage.createEmpty();
    tray = new Tray(icon);
    tray.setToolTip(APP_NAME);
    updateTrayMenu("online");

    tray.on("click", () => {
      if (mainWindow) {
        mainWindow.isVisible() ? mainWindow.hide() : mainWindow.show();
      }
    });
    tray.on("double-click", () => {
      if (mainWindow) mainWindow.show();
    });
  } catch (e) {
    console.error("[Desktop] Tray 初始化失败:", e);
  }
}

function updateTrayMenu(status) {
  if (!tray) return;
  const menu = Menu.buildFromTemplate([
    {
      label: "打开主界面",
      click: () => {
        if (!mainWindow) createMainWindow();
        else mainWindow.show();
        mainWindow && mainWindow.focus();
      },
    },
    {
      label: "在浏览器中打开 UI",
      click: () => shell.openExternal(UI_URL),
    },
    { type: "separator" },
    {
      label: "重启后端服务",
      click: async () => {
        await stopBackend();
        startBackend().then(() => {
          if (mainWindow) mainWindow.webContents.send("backend:restarted");
          if (Notification.isSupported()) {
            new Notification({ title: APP_NAME, body: "后端服务已重启" }).show();
          }
        }).catch((e) => {
          dialog.showErrorBox("后端启动失败", String(e));
        });
      },
    },
    {
      label: "打开项目目录",
      click: () => shell.openPath(pythonProjectDir),
    },
    { type: "separator" },
    {
      label: `状态: ${status === "online" ? "已连接" : "未连接"}`,
      enabled: false,
    },
    {
      label: "退出",
      click: () => {
        quitting = true;
        stopBackend().finally(() => app.quit());
      },
    },
  ]);
  tray.setContextMenu(menu);
}

// ─── IPC 通信 (前端 <-> 主进程) ─────────────────────────────────────────────
function setupIPC() {
  ipcMain.handle("window:minimize", () => mainWindow && mainWindow.minimize());
  ipcMain.handle("window:toggle-max", () => {
    if (!mainWindow) return;
    mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize();
  });
  ipcMain.handle("window:hide", () => mainWindow && mainWindow.hide());

  ipcMain.handle("backend:health", () => new Promise((resolve) => {
    httpGetHealth((ok) => resolve({ ok, url: UI_URL }));
  }));
  ipcMain.handle("backend:restart", async () => {
    await stopBackend();
    try {
      await startBackend();
      return { ok: true };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  });
  ipcMain.handle("app:quit", () => {
    quitting = true;
    stopBackend().finally(() => app.quit());
  });
  ipcMain.handle("app:info", () => ({
    name: APP_NAME,
    version: app.getVersion(),
    port: DEFAULT_PORT,
    uiUrl: UI_URL,
  }));
}

// ─── App 生命周期 ───────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  console.log(`[Desktop] ${APP_NAME} 启动 (${process.platform})`);

  setupIPC();
  createTray();

  try {
    await startBackend();
    updateTrayMenu("online");
  } catch (e) {
    updateTrayMenu("offline");
    dialog.showErrorBox("后端启动失败",
      `无法启动 Python 后端: ${e.message}\n\n请确认已安装 Python 和依赖:\n  pip install fastapi uvicorn requests`
    );
  }

  createMainWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
  });
});

app.on("window-all-closed", (e) => {
  if (process.platform !== "darwin") {
    // 保留在托盘 — 不退出
  }
});

app.on("before-quit", () => { quitting = true; });

app.on("quit", async () => {
  if (!quitting) {
    quitting = true;
    await stopBackend();
  }
});

// 确保单实例
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    } else {
      createMainWindow();
    }
  });
}
