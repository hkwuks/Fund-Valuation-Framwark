#!/usr/bin/env bash
# 关闭项目前后端服务
# 前端: 端口 3000 | 后端: 端口 8000
# 用法: ./scripts/stop_servers.sh

set -u

FRONT_PORTS=(3000)
BACK_PORTS=(8000)

stop_port() {
  local port=$1 label=$2
  local pids
  pids=$(lsof -t -i :"$port" 2>/dev/null)
  if [ -z "$pids" ]; then
    echo "  ⏹  $label (端口 $port): 未运行"
    return
  fi
  echo "  🔪 $label (端口 $port): 关闭 PID $pids"
  kill $pids 2>/dev/null
}

echo "关闭前后端服务..."
for p in "${FRONT_PORTS[@]}"; do stop_port "$p" "前端"; done
for p in "${BACK_PORTS[@]}"; do stop_port "$p" "后端"; done

sleep 1

# 确认释放
STILL_RUNNING=""
for p in "${FRONT_PORTS[@]}"; do
  lsof -t -i :"$p" >/dev/null 2>&1 && STILL_RUNNING="$STILL_RUNNING $p"
done
for p in "${BACK_PORTS[@]}"; do
  lsof -t -i :"$p" >/dev/null 2>&1 && STILL_RUNNING="$STILL_RUNNING $p"
done

if [ -n "$STILL_RUNNING" ]; then
  echo "⚠️  以下端口仍被占用:$STILL_RUNNING (可能需强制 kill -9)"
  exit 1
fi

echo "✅ 前后端已全部关闭"
