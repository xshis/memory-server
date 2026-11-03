#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  echo "还没有安装。先运行：bash install.sh"
  exit 1
fi

# macOS 可能因为 TCC 权限拒绝读取 ~/Documents 下的虚拟环境。
if ! head -c 1 ".venv/pyvenv.cfg" >/dev/null 2>&1; then
  echo "无法读取 .venv/pyvenv.cfg：macOS 权限被拒绝（Operation not permitted）。"
  echo "这不是程序逻辑错误，系统在阻止当前终端访问 ~/Documents。"
  echo
  echo "任选一种处理："
  echo "  1) 系统设置 → 隐私与安全性 → 完全磁盘访问权限，给终端 / Kimi Code 打开开关，然后重启终端；"
  echo "  2) 把 personal-kb 整个目录移到 ~/personal-kb（不在 Documents 保护范围），"
  echo "     删掉 .venv 后重新运行 bash install.sh。"
  exit 1
fi

exec .venv/bin/python -m kb start "$@"
