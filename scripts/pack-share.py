#!/usr/bin/env python3
"""打分享包：把 personal-kb 代码 + 嵌入模型缓存 + 安装说明打成 zip，供 lan-share 分发。

用法（在 personal-kb 仓库根目录）：
    .venv/bin/python scripts/pack-share.py

输出：../lan-share/dist/personal-kb-share.zip（先写临时文件再原子替换）

要点：
- 必须用 Python zipfile 打中文名文件（系统 zip 不加 UTF-8 标志，Windows 解压乱码）；
- install.ps1 / scan-secrets.ps1 必须带 UTF-8 BOM，否则 Windows PowerShell 5.1 按 GBK
  解析，中文字节的 0x5C 会吃掉引号、报"缺少右 }"；
- fastembed_cache 来自 ~/.cache/fastembed，没有就跳过（安装时会自动联网下载）。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAN_SHARE = REPO.parent / "lan-share"
OUTPUT = LAN_SHARE / "dist" / "personal-kb-share.zip"
FASTEMBED_SOURCE = Path.home() / ".cache" / "fastembed"

ROOT_FILES = [
    ".gitignore", "README.md", "install.ps1", "install.sh", "pyproject.toml",
    "start.bat", "start.sh",
    "一键安装.bat", "一键安装.command", "一键启动.command", "一键关闭.command",
]
ROOT_DIRS = ["config", "docs", "kb", "scripts", "tests"]
EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".log"}
EXTRA_FILES = [LAN_SHARE / "安装说明.txt", LAN_SHARE / "安装演示.md"]
BOM_REQUIRED = {"install.ps1", "scan-secrets.ps1"}


def collect_files() -> list[Path]:
    files: list[Path] = []
    for name in ROOT_FILES:
        path = REPO / name
        if path.is_file():
            files.append(path)
        else:
            print(f"[警告] 根目录缺文件：{name}")
    for dirname in ROOT_DIRS:
        base = REPO / dirname
        if not base.is_dir():
            print(f"[警告] 目录不存在：{dirname}")
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if any(part in EXCLUDE_PARTS for part in path.parts):
                continue
            if path.suffix in EXCLUDE_SUFFIXES:
                continue
            files.append(path)
    for path in EXTRA_FILES:
        if path.is_file():
            files.append(path)
        else:
            print(f"[警告] 附加文件缺失：{path}")
    if FASTEMBED_SOURCE.is_dir():
        for path in sorted(FASTEMBED_SOURCE.rglob("*")):
            if path.is_file():
                files.append(path)
    else:
        print("[提示] ~/.cache/fastembed 不存在，分享包不含嵌入模型（安装时自动下载）")
    return files


def check_bom(path: Path) -> None:
    if path.name in BOM_REQUIRED:
        raw = path.read_bytes()
        if not raw.startswith(b"\xef\xbb\xbf"):
            raise SystemExit(f"[错误] {path.name} 缺少 UTF-8 BOM，Windows PowerShell 会解析失败，请先加 BOM")


def main() -> int:
    files = collect_files()
    for path in files:
        if path.name in BOM_REQUIRED:
            check_bom(path)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".zip", dir=OUTPUT.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in files:
                if path.is_relative_to(FASTEMBED_SOURCE):
                    rel = Path("fastembed_cache") / path.relative_to(FASTEMBED_SOURCE)
                elif path.is_relative_to(REPO):
                    rel = path.relative_to(REPO)
                else:
                    rel = Path(path.name)  # lan-share 附加文件打到包根
                zf.write(path, str(rel))
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        shutil.move(str(tmp), OUTPUT)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    size = OUTPUT.stat().st_size
    print(f"完成：{OUTPUT}")
    print(f"大小：{size / 1024 / 1024:.1f} MB，文件数：{len(files)}")
    print(f"SHA256: {digest[:16]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
