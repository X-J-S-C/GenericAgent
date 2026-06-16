#!/usr/bin/env bash
# ============================================================================
#  GenericAgent Desktop — 一键启动脚本
# ----------------------------------------------------------------------------
# 直接启动桌面端（npm + Electron）。首次运行自动安装 npm 依赖。
#
# 用法:
#   ./start-desktop.sh          # 启动桌面端
#   ./start-desktop.sh --dev    # 启用开发模式（自动打开 DevTools）
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_DIR="${SCRIPT_DIR}/desktop"

echo "========================================="
echo "  GenericAgent Desktop 启动"
echo "========================================="
echo ""

# ─── 检查 Python 依赖 ───────────────────────────────────────────────────────
echo "▸ 检查 Python 依赖..."
python3 -c "import fastapi, uvicorn" 2>/dev/null || {
    echo "  缺少依赖，正在安装 fastapi/uvicorn/requests ..."
    python3 -m pip install fastapi uvicorn requests
}
echo "  ✓ Python 依赖 OK"

# ─── 检查配置文件 ───────────────────────────────────────────────────────────
echo ""
echo "▸ 检查配置..."
if [ ! -f "${SCRIPT_DIR}/mykey.py" ]; then
    echo "  ⚠  mykey.py 不存在，已自动创建默认配置（deepseek-v4-pro）"
    cat > "${SCRIPT_DIR}/mykey.py" <<'PYEOF'
native_oai_config = {
    "name": "deepseek-v4-pro",
    "apikey": "sk-041685ca0cc640adaaa3669fa0f88b80",
    "apibase": "https://api.deepseek.com/v1",
    "model": "deepseek-v4-pro",
    "api_mode": "chat_completions",
    "max_retries": 3,
    "connect_timeout": 10,
    "read_timeout": 120,
    "max_tokens": 8192,
}
PYEOF
else
    echo "  ✓ mykey.py 存在"
fi

# ─── 启动 Electron 桌面端 ──────────────────────────────────────────────────
echo ""
echo "▸ 启动桌面端..."

cd "${DESKTOP_DIR}"

if [ ! -d "node_modules" ]; then
    echo "  首次运行，正在安装 Electron 依赖 (npm install) ..."
    npm install
fi

echo ""
echo "  ✓ 启动 Electron ..."
echo ""
npm start
