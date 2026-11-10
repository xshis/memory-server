#!/usr/bin/env python3
"""预下载 Windows 依赖 wheel 并生成精确版本锁，打进分享包（在 macOS/Linux 上执行）。

用法：.venv/bin/python scripts/fetch-wheels.py
输出：
  wheels/                      win_amd64 + cp312 全套 wheel（基础+语义+文档+图片+飞书）
  constraints-win312.txt       每个包 == 精确版本（与 wheel 严格一致）

下载后自动做一次 --no-index 解析验证：闭包不完整会直接报错，不会打出坏包。
Windows 端 install.ps1 用 --no-index --find-links wheels -c constraints-win312.txt 安装，
版本与包内锁定清单严格一致，全程不联网。
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WHEELS = REPO / "wheels"
CONSTRAINTS = REPO / "constraints-win312.txt"

# 与 pyproject 对齐：基础依赖 + 全部可选 extras + 构建依赖
REQUIREMENTS = [
    "mcp>=2.3.0,<3",
    "numpy>=1.26",
    "watchdog>=4.0",
    "fastembed>=0.2.7",
    "pypdf>=4.0",
    "python-docx>=1.0",
    "openpyxl>=3.1",
    "python-pptx>=0.6",
    "opencv-python-headless>=4.8",
    "lark-oapi>=1.7.0",
    "setuptools>=68",
    "wheel",
]
# 纯 Python wheel，但带了 sys_platform == "darwin" 的平台标记依赖（pyobjc），
# 交叉下载器无法解析，单独 --no-deps 拉取；Windows 端实际只依赖 Pillow（在主闭包里）。
EXTRA_WHEELS = ["pystray>=0.19"]

# 顶层依赖的规范化名，用于完整性校验
TOP_LEVEL = {
    "mcp", "numpy", "watchdog", "fastembed", "pypdf", "python-docx",
    "openpyxl", "python-pptx", "opencv-python-headless", "lark-oapi",
    "pystray", "setuptools", "wheel",
}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def build_constraints() -> dict[str, str]:
    pins: dict[str, str] = {}
    for whl in sorted(WHEELS.glob("*.whl")):
        parts = whl.name.split("-")
        if len(parts) < 2:
            continue
        name, version = normalize(parts[0]), parts[1]
        pins[name] = version
    missing = sorted(TOP_LEVEL - set(pins))
    if missing:
        raise SystemExit(f"[错误] wheel 集合缺少顶层依赖：{missing}")
    lines = ["# 自动生成：与 wheels/ 中 wheel 严格一致的版本锁（win_amd64 / cp312）"]
    lines += [f"{name}=={version}" for name, version in sorted(pins.items())]
    CONSTRAINTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pins


def verify_offline() -> None:
    """用 --no-index 重放一次解析，证明闭包完整（不联网、不安装）。"""
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed",
            "--no-index", "--find-links", str(WHEELS),
            "-c", str(CONSTRAINTS),
            "--only-binary=:all:",
            "--platform", "win_amd64", "--python-version", "3.12", "--implementation", "cp",
            "--target", tmp,
            *REQUIREMENTS,
        ]
        print("验证离线闭包 ...")
        subprocess.run(cmd, check=True)


def main() -> int:
    WHEELS.mkdir(exist_ok=True)
    # 清掉旧 wheel，保证锁和文件严格对应
    for old in WHEELS.glob("*.whl"):
        old.unlink()
    cmd = [
        sys.executable, "-m", "pip", "download", "-d", str(WHEELS),
        "--only-binary=:all:",
        "--platform", "win_amd64",
        "--python-version", "3.12",
        "--implementation", "cp",
        *REQUIREMENTS,
    ]
    print("下载 Windows wheel（基础+语义+文档+图片+飞书+托盘）...")
    subprocess.run(cmd, check=True)
    for requirement in EXTRA_WHEELS:
        subprocess.run(
            [sys.executable, "-m", "pip", "download", "--no-deps", "-d", str(WHEELS), requirement],
            check=True,
        )
    pins = build_constraints()
    verify_offline()
    files = sorted(WHEELS.glob("*.whl"))
    total = sum(f.stat().st_size for f in files)
    print(f"完成：{len(files)} 个 wheel，共 {total / 1024 / 1024:.1f} MB")
    print(f"版本锁：{CONSTRAINTS.name}（{len(pins)} 个包，全部 == 精确版本），离线解析验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
