#!/bin/bash
set -u
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "首次运行，先安装环境..."
  bash install.sh || {
    read -n 1 -s -r -p "安装失败，按任意键关闭..."
    exit 1
  }
fi

PY=".venv/bin/python"

if [ "$(uname -s)" = "Darwin" ]; then
  if ! "$PY" -c "import rumps" >/dev/null 2>&1; then
    echo "安装菜单栏依赖 rumps ..."
    "$PY" -m pip install -e ".[tray]" || echo "菜单栏依赖安装失败，可稍后手动安装。"
  fi
fi

"$PY" -m kb setup --start
code=$?
if [ $code -ne 0 ]; then
  echo
  read -n 1 -s -r -p "初始化未完成，按任意键关闭..."
  exit $code
fi

if [ "$(uname -s)" != "Darwin" ]; then
  echo "已启动。当前系统没有菜单栏，可用 kb logs / kb stop 管理。"
  read -n 1 -s -r -p "按任意键关闭..."
  exit 0
fi

if ! "$PY" -c "import rumps" >/dev/null 2>&1; then
  echo "缺少 rumps，菜单栏未启动；服务已经在运行。"
  read -n 1 -s -r -p "按任意键关闭..."
  exit 0
fi

mkdir -p logs
nohup "$PY" -m kb tray >/dev/null 2>&1 &
echo $! > data/tray.pid
sleep 1
if kill -0 "$(cat data/tray.pid)" 2>/dev/null; then
  echo "菜单栏已启动：右上角 «KB» 图标。这个窗口可以关闭。"
else
  echo "菜单栏启动失败，查看 logs/tray.log"
fi
sleep 2
