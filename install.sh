#!/usr/bin/env bash
# Portable installer for macOS / Linux. Windows uses install.ps1.
set -euo pipefail

cd "$(dirname "$0")"

echo "== 本地知识库助手 · 安装 =="
echo "目录：$(pwd)"

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        PYTHON_BIN="$candidate"
        break
      fi
    fi
  done
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "未找到 Python 3.10+。"
  echo "macOS 可运行：brew install python@3.12"
  echo "或从 https://www.python.org/downloads/ 安装后重试。"
  exit 1
fi

echo "Python：$($PYTHON_BIN --version 2>&1)"

echo ""
echo "第 0 步 · 环境预检（有问题会先提示，不动任何文件）"
network_ok=1
if command -v curl >/dev/null 2>&1; then
  if ! curl -sS -m 6 -o /dev/null https://pypi.org/simple/ 2>/dev/null; then
    network_ok=0
  fi
fi
free_gb=$(df -g . | awk 'NR==2 {print $4}')
if [ "$network_ok" = "1" ]; then
  echo "  网络（pypi.org）：OK"
else
  echo "  [警告] 连不上 pypi.org——依赖安装会失败。请先解决网络/代理。"
fi
echo "  磁盘（剩余 ${free_gb}GB）"
if [ "$network_ok" != "1" ]; then
  printf "网络不通仍要继续吗？[y/N] "
  read -r answer
  case "$answer" in
    y|Y) ;;
    *) exit 1 ;;
  esac
fi

if [ ! -d ".venv" ]; then
  echo "创建虚拟环境 .venv ..."
  "$PYTHON_BIN" -m venv .venv
fi

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "虚拟环境创建失败：$PY 不存在"
  exit 1
fi

if ! "$PY" -m pip --version >/dev/null 2>&1; then
  echo "初始化 pip ..."
  "$PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  echo "虚拟环境里没有 pip。请删除 .venv 后重新运行，或用系统 Python 创建 venv。"
  exit 1
fi

echo "安装依赖 ..."
"$PY" -m pip install --upgrade pip >/dev/null
if [ "${KB_WITH_EMBED:-1}" = "1" ]; then
  if ! "$PY" -m pip install -e ".[embed]"; then
    echo "  [警告] 语义检索依赖（fastembed）安装失败，退回基础安装（仅关键词搜索）。"
    echo "  可稍后重试：$PY -m pip install -e '.[embed]'"
    "$PY" -m pip install -e .
  fi
else
  "$PY" -m pip install -e .
fi

if [ "${KB_WITH_DOCS:-0}" = "1" ]; then
  echo "安装办公文档解析依赖 ..."
  "$PY" -m pip install -e ".[docs]"
fi

if [ "${KB_WITH_VISION:-0}" = "1" ]; then
  echo "安装图片整理依赖（OpenCV）..."
  "$PY" -m pip install -e ".[vision]"
fi

if [ "${KB_WITH_FEISHU:-0}" = "1" ]; then
  echo "安装飞书机器人依赖 ..."
  "$PY" -m pip install -e ".[feishu]"
fi

if [ "$(uname -s)" = "Darwin" ]; then
  echo "安装 macOS 菜单栏依赖 ..."
  "$PY" -m pip install -e ".[tray]" || echo "  菜单栏依赖安装失败，可稍后手动运行：pip install -e '.[tray]'"
fi

echo
echo "本地模型（可选，不装也能正常搜索）"
echo "  1) qwen2.5:3b   推荐，约 1.9GB，速度和质量平衡"
echo "  2) qwen2.5:1.5b 更快，约 1GB，质量略低"
echo "  3) qwen2.5:7b   更强，约 4.7GB，CPU 上明显更慢"
echo "  4) 不安装"
echo "  提示：100Mbps 下载约 3~6 分钟；慢网 10~30 分钟。资料不出本机。"
MODEL_NAME="${KB_OLLAMA_MODEL:-qwen2.5:3b}"
OLLAMA_CHOICE="${KB_WITH_OLLAMA:-}"
if [ -z "$OLLAMA_CHOICE" ] && [ -n "${KB_OLLAMA_MODEL:-}" ]; then OLLAMA_CHOICE=1; fi
if [ "$OLLAMA_CHOICE" = "0" ]; then
  :
elif [ "$OLLAMA_CHOICE" != "1" ]; then
  if [ -t 0 ]; then
    read -r -p "请选择 [1]: " OLLAMA_PICK || true
    case "${OLLAMA_PICK:-1}" in
      1|"") MODEL_NAME="qwen2.5:3b" ;;
      2) MODEL_NAME="qwen2.5:1.5b" ;;
      3) MODEL_NAME="qwen2.5:7b" ;;
      4) OLLAMA_CHOICE=0 ;;
      *) MODEL_NAME="qwen2.5:3b" ;;
    esac
    [ "$OLLAMA_CHOICE" = "0" ] || OLLAMA_CHOICE=1
  else
    OLLAMA_CHOICE=0
  fi
fi
if [ "$OLLAMA_CHOICE" = "1" ]; then
  if ! command -v ollama >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/ollama" ]; then
    if [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
      echo "安装 Ollama ..."
      brew install ollama || true
    fi
  fi
  if command -v ollama >/dev/null 2>&1 || [ -x "$HOME/.local/bin/ollama" ]; then
    echo "下载模型 $MODEL_NAME（首次会启动 Ollama，可能需要几分钟）..."
    "$PY" -m kb model install --name "$MODEL_NAME" || echo "  模型安装失败，可稍后运行：kb model install --name $MODEL_NAME"
  else
    echo "  未检测到 Ollama。先安装：https://ollama.com/download"
    echo "  安装后运行：kb model install --name $MODEL_NAME"
  fi
else
  echo "  已跳过；以后需要可运行 kb model install --name qwen2.5:3b"
fi

echo
echo "运行环境自检 ..."
"$PY" -m kb doctor || true

echo
echo "完成。下一步："
echo "  双击 一键启动.command   # 环境检测 + 权限引导 + 启动 + 菜单栏"
echo "  source .venv/bin/activate"
echo "  kb setup                 # 环境检测、权限引导、接入飞书/豆包"
echo "  kb scope                 # 选择索引范围（全盘/文件夹/盘符）"
echo "  kb index                 # 建立索引"
echo "  kb search \"关键词\"       # 搜索"
echo "  kb tray                  # macOS 菜单栏控制"
echo "  kb map --open            # 打开 3D 脑图 http://127.0.0.1:8770"
echo "  kb mcp --http            # 启动 MCP HTTP，供飞书/豆包接入"
echo
echo "macOS 首次访问“桌面/文稿/下载”时，如果被系统拦截，"
echo "运行 kb setup 会自动打开 系统设置 → 隐私与安全性 对应页面。"
