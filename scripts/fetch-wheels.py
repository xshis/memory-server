#!/usr/bin/env python3
"""预下载 Windows / macOS 依赖 wheel 并生成精确版本锁，打进分享包（在 macOS 上执行）。

用法：.venv/bin/python scripts/fetch-wheels.py [--target win|mac|both]
输出（每个目标一套，互不复用，因为二进制 wheel 平台不同）：
  wheels-win/ + constraints-win312.txt   win_amd64 / cp312（含 pystray 托盘）
  wheels-mac/ + constraints-mac312.txt   macosx_11_0_arm64 / cp312（含 rumps 菜单栏、pyobjc）

每个目标下载后自动做一次 --no-index 解析验证：闭包不完整直接报错，不会打出坏包。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

COMMON_REQUIREMENTS = [
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

COMMON_TOP_LEVEL = {
    "mcp", "numpy", "watchdog", "fastembed", "pypdf", "python-docx",
    "openpyxl", "python-pptx", "opencv-python-headless", "lark-oapi",
    "setuptools", "wheel",
}

TARGETS: dict[str, dict] = {
    "win": {
        "label": "Windows (win_amd64 / cp312)",
        "platform": "win_amd64",
        "wheels": "wheels-win",
        "constraints": "constraints-win312.txt",
        "requirements": COMMON_REQUIREMENTS,
        # pystray 是纯 wheel，但依赖带 sys_platform=="darwin" 标记，交叉下载单拉
        "extra_no_deps": ["pystray>=0.19"],
        "top_level": COMMON_TOP_LEVEL | {"pystray"},
    },
    "mac": {
        "label": "macOS (macosx_11_0_arm64 / cp312)",
        "platform": "macosx_11_0_arm64",
        "wheels": "wheels-mac",
        "constraints": "constraints-mac312.txt",
        # pyobjc 打头（rumps 的依赖，有 arm64 wheel）；rumps 本身只有 sdist，走 extra_no_deps
        "requirements": COMMON_REQUIREMENTS + ["pyobjc-framework-Cocoa"],
        "extra_no_deps": ["rumps>=0.4"],
        "verify_requirements": COMMON_REQUIREMENTS + ["pyobjc-framework-Cocoa"],
        "top_level": COMMON_TOP_LEVEL | {"rumps", "pyobjc-framework-cocoa"},
    },
}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def build_constraints(wheels: Path, constraints: Path, top_level: set[str]) -> dict[str, str]:
    pins: dict[str, str] = {}
    archives = list(wheels.glob("*.whl")) + list(wheels.glob("*.tar.gz"))
    for path in sorted(archives):
        if path.name.endswith(".tar.gz"):
            stem = path.name[: -len(".tar.gz")]
        else:
            stem = path.name[: -len(".whl")]
        parts = stem.split("-")
        if len(parts) < 2:
            continue
        pins[normalize(parts[0])] = parts[1]
    missing = sorted(top_level - set(pins))
    if missing:
        raise SystemExit(f"[错误] wheel 集合缺少顶层依赖：{missing}")
    lines = ["# 自动生成：与 wheels 中 wheel 严格一致的版本锁（cp312）"]
    lines += [f"{name}=={version}" for name, version in sorted(pins.items())]
    constraints.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pins


def verify_offline(wheels: Path, constraints: Path, platform: str, requirements: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed",
            "--no-index", "--find-links", str(wheels),
            "-c", str(constraints),
            "--only-binary=:all:",
            "--platform", platform, "--python-version", "3.12", "--implementation", "cp",
            "--target", tmp,
            *requirements,
        ]
        subprocess.run(cmd, check=True)


def fetch_target(key: str) -> None:
    spec = TARGETS[key]
    wheels = REPO / spec["wheels"]
    constraints = REPO / spec["constraints"]
    wheels.mkdir(exist_ok=True)
    for old in wheels.glob("*.whl"):
        old.unlink()

    print(f"[{spec['label']}] 下载 wheel ...")
    cmd = [
        sys.executable, "-m", "pip", "download", "-d", str(wheels),
        "--only-binary=:all:",
        "--platform", spec["platform"],
        "--python-version", "3.12",
        "--implementation", "cp",
        *spec["requirements"],
    ]
    subprocess.run(cmd, check=True)
    for requirement in spec["extra_no_deps"]:
        subprocess.run(
            [sys.executable, "-m", "pip", "download", "--no-deps", "-d", str(wheels), requirement],
            check=True,
        )

    pins = build_constraints(wheels, constraints, spec["top_level"])
    verify_requirements = spec.get("verify_requirements", spec["requirements"])
    print(f"[{spec['label']}] 验证离线闭包 ...")
    verify_offline(wheels, constraints, spec["platform"], verify_requirements)
    files = sorted(list(wheels.glob("*.whl")) + list(wheels.glob("*.tar.gz")))
    total = sum(f.stat().st_size for f in files)
    print(
        f"[{spec['label']}] 完成：{len(files)} 个 wheel，{total / 1024 / 1024:.1f} MB；"
        f"版本锁 {constraints.name}（{len(pins)} 个包，全部 == 精确版本），离线解析通过"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["win", "mac", "both"], default="both")
    args = parser.parse_args()
    keys = ["win", "mac"] if args.target == "both" else [args.target]
    for key in keys:
        fetch_target(key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
