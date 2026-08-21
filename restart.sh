#!/bin/sh
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"

echo "===== 重启前后端 ====="

# 关掉旧进程（POSIX 兼容）
for port in 8000 3000; do
  pids=$(lsof -t -i:"$port" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    echo "Killed PID $pids (port $port)"
  else
    echo "Port $port 无占用"
  fi
done

sleep 1

# 启动后端
echo "启动后端 (port 8000)..."
cd "$PROJECT_DIR"
nohup uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000 > "$BACKEND_LOG" 2>&1 &
echo $! > /tmp/backend.pid

# 启动前端
echo "启动前端 (port 3000)..."
cd "$PROJECT_DIR/frontend"
nohup npx vite --host 0.0.0.0 --port 3000 > "$FRONTEND_LOG" 2>&1 &
echo $! > /tmp/frontend.pid

# 等后端起来
echo "等待后端启动..."
i=1
while [ "$i" -le 15 ]; do
  sleep 1
  if lsof -ti":8000" >/dev/null 2>&1; then
    echo "后端启动成功"
    break
  fi
  if [ "$i" -eq 15 ]; then
    echo "后端启动超时，检查 $BACKEND_LOG"
    tail -10 "$BACKEND_LOG" 2>&1 || true
    exit 1
  fi
  i=$((i + 1))
done

echo "===== 完成 ====="
echo "后端: http://localhost:8000"
echo "前端: http://localhost:3000"
echo "后端日志: $BACKEND_LOG"
echo "前端日志: $FRONTEND_LOG"
