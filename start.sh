#!/bin/bash
# ========================================
# CSGOEmpire 捡漏助手 — macOS/Linux 启动脚本
# ========================================

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "=========================================="
echo "  CSGOEmpire 双引擎捡漏助手"
echo "=========================================="
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未找到 python3，请先安装 Python 3.11+"
    exit 1
fi

echo "[1/3] 检查依赖..."
pip3 install -r backend/requirements.txt -q

echo "[2/3] 启动后端服务 (http://127.0.0.1:8080)..."
echo "[3/3] API 文档: http://127.0.0.1:8080/docs"
echo ""

cd backend
python3 -m uvicorn main:app --host 127.0.0.1 --port 8080
