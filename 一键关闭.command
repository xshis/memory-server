#!/bin/bash
set -u
cd "$(dirname "$0")"

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "还没有安装。先运行 一键安装.command。"
  read -n 1 -s -r -p "按任意键关闭..."
  exit 1
fi

echo "停止服务..."
"$PY" -m kb stop

if [ -f data/tray.pid ]; then
  pid="$(cat data/tray.pid)"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
  fi
  rm -f data/tray.pid
fi

echo "已停止。"
sleep 1
