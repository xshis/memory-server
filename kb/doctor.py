from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import socket
import sqlite3
import ssl
import sys
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from .config import HOME, ensure_dirs, load_config
from .db import connect, init_schema, list_roots


def _memory_bytes() -> int | None:
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
            return None
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except Exception:
        return None


def _human_bytes(value: int | None) -> str:
    if value is None:
        return "未知"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{value}B"


def _check(status: str, name: str, message: str, suggestion: str = "") -> dict[str, Any]:
    return {
        "status": status,
        "name": name,
        "message": message,
        "suggestion": suggestion,
    }


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _feishu_reachable() -> tuple[bool, str]:
    try:
        context = ssl.create_default_context()
        with socket.create_connection(("open.feishu.cn", 443), timeout=4) as raw:
            with context.wrap_socket(raw, server_hostname="open.feishu.cn") as tls:
                tls.settimeout(4)
                tls.sendall(b"HEAD / HTTP/1.0\r\nHost: open.feishu.cn\r\n\r\n")
                tls.recv(64)
        return True, "TLS 连接正常"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _ollama_reachable() -> tuple[bool, str]:
    try:
        with urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as response:
            response.read(16)
        return True, "本地 Ollama 可访问"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def run_doctor(*, include_feishu: bool = False, include_ollama: bool = False) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    config = load_config()

    version = sys.version_info
    if version >= (3, 10):
        checks.append(_check("ok", "Python", f"{version.major}.{version.minor}.{version.micro}"))
    else:
        checks.append(_check("fail", "Python", f"{version.major}.{version.minor}.{version.micro}",
                             "需要 Python 3.10 或更高版本"))

    checks.append(_check("ok", "系统", f"{platform.system()} {platform.release()} / {platform.machine()}"))

    sqlite_version = sqlite3.sqlite_version
    checks.append(_check("ok", "SQLite", sqlite_version))
    try:
        memory_db = sqlite3.connect(":memory:")
        memory_db.execute("CREATE VIRTUAL TABLE fts_test USING fts5(x, tokenize='trigram')")
        memory_db.close()
        checks.append(_check("ok", "全文检索", "FTS5 + trigram 可用"))
    except sqlite3.OperationalError as exc:
        checks.append(_check("warn", "全文检索", f"trigram 不可用：{exc}", "会回退到 unicode61，中文检索效果可能下降"))

    try:
        ensure_dirs()
        probe = HOME / "data" / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(_check("ok", "数据目录", str(HOME)))
    except OSError as exc:
        checks.append(_check("fail", "数据目录", str(HOME), f"不可写：{exc}"))

    disk = shutil.disk_usage(HOME)
    free_gb = disk.free / (1024 ** 3)
    if free_gb >= 10:
        checks.append(_check("ok", "磁盘空间", f"可用 {free_gb:.1f}GB"))
    elif free_gb >= 3:
        checks.append(_check("warn", "磁盘空间", f"可用 {free_gb:.1f}GB", "建议至少留 10GB"))
    else:
        checks.append(_check("fail", "磁盘空间", f"可用 {free_gb:.1f}GB", "空间不足，至少需要 3GB"))

    memory = _memory_bytes()
    if memory is None:
        checks.append(_check("warn", "内存", "无法检测", "请确认至少 8GB"))
    else:
        memory_gb = memory / (1024 ** 3)
        if memory_gb >= 15:
            checks.append(_check("ok", "内存", f"{memory_gb:.1f}GB"))
        elif memory_gb >= 7:
            checks.append(_check("warn", "内存", f"{memory_gb:.1f}GB", "搜索可用；本地 7B 模型建议 16GB"))
        else:
            checks.append(_check("warn", "内存", f"{memory_gb:.1f}GB", "建议只用搜索模式或云端模型"))

    try:
        conn = connect(HOME / "data" / "kb.db")
        init_schema(conn)
        roots = list_roots(conn)
        conn.close()
        if roots:
            checks.append(_check("ok", "索引范围", f"{len(roots)} 个根目录"))
        else:
            checks.append(_check("warn", "索引范围", "还没有配置", "运行 `kb scope` 选择文件夹或盘符"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("fail", "数据库", str(exc), "检查数据目录权限"))

    mcp_port = int(config["mcp"].get("port", 8766))
    for name, host, port in (
        ("MCP 端口", str(config["mcp"].get("host", "127.0.0.1")), mcp_port),
        ("3D 脑图端口", "127.0.0.1", 8770),
    ):
        if _port_free(host, port):
            checks.append(_check("ok", name, f"{port} 可用"))
        else:
            checks.append(_check("warn", name, f"{port} 被占用", "改配置里的端口，或关闭占用程序"))

    optional = {
        "pypdf": "PDF 正文提取",
        "docx": "DOCX 正文提取",
        "openpyxl": "XLSX 正文提取",
        "pptx": "PPTX 正文提取",
        "cv2": "图片整理（OpenCV）",
    }
    missing = [label for module, label in optional.items() if importlib.util.find_spec(module) is None]
    if missing:
        checks.append(_check("warn", "办公文档解析", "缺少：" + "、".join(missing),
                             "安装可选依赖：pip install -e '.[docs]'"))
    else:
        checks.append(_check("ok", "办公文档解析", "PDF/DOCX/XLSX/PPTX 可用"))

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY")
    if proxy:
        checks.append(_check("ok", "代理", proxy))
    else:
        checks.append(_check("ok", "代理", "未设置，直连"))

    if include_ollama:
        from . import llm

        data = llm.status()
        if not data["installed"]:
            checks.append(_check("warn", "Ollama", "未安装",
                                 "运行 kb setup --with-ollama，或 kb model install"))
        elif not data["running"]:
            checks.append(_check("warn", "Ollama", "服务未运行",
                                 "运行 ollama serve，或 kb model enable"))
        else:
            models = "、".join(data["models"]) or "无"
            checks.append(_check("ok", "Ollama", f"运行中；模型：{models}"))
            if data["mode"] == "ollama" and data["configured_model"] not in data["models"]:
                checks.append(_check("warn", "本地模型", f"{data['configured_model']} 未下载",
                                     "运行 kb model install"))

    if include_feishu:
        ok, message = _feishu_reachable()
        checks.append(_check("ok" if ok else "fail", "飞书网络", message,
                             "" if ok else "手机飞书机器人不可用；检查代理/防火墙/DNS"))

    return checks


def print_checks(checks: list[dict[str, Any]]) -> int:
    icons = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]"}
    for item in checks:
        print(f"{icons.get(item['status'], '[INFO]')} {item['name']}：{item['message']}")
        if item.get("suggestion"):
            print(f"        建议：{item['suggestion']}")
    failures = sum(1 for item in checks if item["status"] == "fail")
    warnings = sum(1 for item in checks if item["status"] == "warn")
    print()
    if failures:
        print(f"自检结果：{failures} 项失败，{warnings} 项警告。请先处理失败项。")
        return 1
    if warnings:
        print(f"自检结果：通过，但有 {warnings} 项警告。可以运行，部分能力受限。")
    else:
        print("自检结果：全部通过。")
    return 0
