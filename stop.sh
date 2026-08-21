#!/bin/sh
# 关闭项目前后端服务
# 前端: 端口 3000 | 后端: 端口 8000
# 用法: sh stop.sh  或  ./stop.sh
set -u

stop_port() {
  _sp_port=$1
  _sp_label=$2
  _sp_pids=$(lsof -t -i :"$_sp_port" 2>/dev/null || true)
  if [ -z "$_sp_pids" ]; then
    echo "  ⏹  $_sp_label (端口 $_sp_port): 未运行"
    return 0
  fi
  echo "  🔪 $_sp_label (端口 $_sp_port): 关闭 PID $_sp_pids"
  # shellcheck disable=SC2086
  kill $_sp_pids 2>/dev/null || true
}

echo "关闭前后端服务..."
stop_port 3000 "前端"
stop_port 8000 "后端"

sleep 1

# 确认释放
STILL_RUNNING=""
for _p in 3000 8000; do
  if lsof -t -i :"$_p" >/dev/null 2>&1; then
    STILL_RUNNING="$STILL_RUNNING $_p"
  fi
done

if [ -n "$STILL_RUNNING" ]; then
  echo "⚠️  以下端口仍被占用:$STILL_RUNNING (可能需强制 kill -9)"
  exit 1
fi

echo "✅ 前后端已全部关闭"
