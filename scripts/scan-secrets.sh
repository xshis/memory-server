#!/usr/bin/env bash
cd "$(dirname "$0")/.."
if [ ! -x ".venv/bin/python" ]; then
  echo "还没有安装。先运行：bash install.sh"
  exit 1
fi
exec .venv/bin/python -m kb scan "$@"
