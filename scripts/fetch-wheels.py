#!/usr/bin/env python3
"""预下载 Windows 依赖 wheel，打进分享包（在 macOS/Linux 上执行）。

用法：.venv/bin/python scripts/fetch-wheels.py
输出：wheels/（win_amd64 + cp312 wheel），pack-share.py 自动打进 zip。
Windows 端 install.ps1 先用 --no-index --find-links wheels 离线安装，缺包才走网络。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WHEELS = REPO / "wheels"

# 与 pyproject 对齐：基础依赖 + embed + 构建依赖（离线 editable 安装需要）
REQUIREMENTS = [
    "mcp>=2.3.0,<3",
    "numpy>=1.26",
    "watchdog>=4.0",
    "fastembed>=0.2.7",
    "setuptools>=68",
    "wheel",
]


def main() -> int:
    WHEELS.mkdir(exist_ok=True)
    cmd = [
        sys.executable, "-m", "pip", "download", "-d", str(WHEELS),
        "--only-binary=:all:",
        "--platform", "win_amd64",
        "--python-version", "3.12",
        "--implementation", "cp",
        *REQUIREMENTS,
    ]
    print("下载 Windows wheel ...")
    subprocess.run(cmd, check=True)
    files = sorted(WHEELS.glob("*.whl"))
    total = sum(f.stat().st_size for f in files)
    print(f"完成：{len(files)} 个 wheel，共 {total / 1024 / 1024:.1f} MB -> {WHEELS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
