/* ============================================================================
 * GenericAgent Desktop — preload.js (安全桥)
 * ----------------------------------------------------------------------------
 * 在 contextIsolation=true 的前提下，向前端渲染进程暴露一组最小可用 API。
 * 主进程与渲染进程双向通信，所有调用都经过白名单。
 * ========================================================================== */

const { contextBridge, ipcRenderer } = require("electron");

// ─── 健康检查 & 后端控制 ────────────────────────────────────────────────────
contextBridge.exposeInMainWorld("ga", {
  app: {
    info: () => ipcRenderer.invoke("app:info"),
    quit: () => ipcRenderer.invoke("app:quit"),
  },
  window: {
    minimize: () => ipcRenderer.invoke("window:minimize"),
    toggleMax: () => ipcRenderer.invoke("window:toggle-max"),
    hide: () => ipcRenderer.invoke("window:hide"),
  },
  backend: {
    health: () => ipcRenderer.invoke("backend:health"),
    restart: () => ipcRenderer.invoke("backend:restart"),
  },
  events: {
    onCrashed: (cb) => ipcRenderer.on("backend:crashed", (_e, d) => cb && cb(d)),
    onRestarted: (cb) => ipcRenderer.on("backend:restarted", (_e, d) => cb && cb(d)),
  },
});
