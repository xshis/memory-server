from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import TextIO

from .config import ensure_dirs, logs_dir

LOG_NAMES = ("map", "mcp", "watch", "feishu", "tray")
MAX_LOG_BYTES = 5 * 1024 * 1024


class _Tee:
    """同时写入原终端和日志文件。"""

    def __init__(self, *streams: TextIO | None) -> None:
        self._streams = [stream for stream in streams if stream is not None]
        self._lock = threading.Lock()

    @property
    def encoding(self) -> str:
        for stream in self._streams:
            encoding = getattr(stream, "encoding", None)
            if encoding:
                return encoding
        return "utf-8"

    def write(self, text: str) -> int:
        with self._lock:
            for stream in self._streams:
                try:
                    stream.write(text)
                    stream.flush()
                except (OSError, ValueError):
                    continue
        return len(text)

    def flush(self) -> None:
        with self._lock:
            for stream in self._streams:
                try:
                    stream.flush()
                except (OSError, ValueError):
                    continue

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        for stream in self._streams:
            try:
                return stream.fileno()
            except (AttributeError, OSError, ValueError):
                continue
        raise OSError("fileno 不可用")


def log_path(name: str) -> Path:
    return logs_dir() / f"{name}.log"


def _rotate(path: Path) -> None:
    try:
        if path.stat().st_size <= MAX_LOG_BYTES:
            return
    except OSError:
        return
    backup = path.with_name(path.name + ".1")
    try:
        backup.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return
    try:
        path.replace(backup)
    except OSError:
        pass


def setup_service_log(name: str, *, mirror_stdout: bool = True) -> Path:
    """把服务输出同时写到终端和 logs/<name>.log。

    stdio 模式的 MCP 不能碰 stdout，所以那里只镜像 stderr。
    """
    ensure_dirs()
    path = log_path(name)
    _rotate(path)
    try:
        stream = path.open("a", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        return path
    stream.write(
        f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 {name} "
        f"pid={os.getpid()} cwd={Path.cwd()} =====\n"
    )
    stream.flush()
    if mirror_stdout:
        sys.stdout = _Tee(sys.stdout, stream)
    sys.stderr = _Tee(sys.stderr, stream)
    return path


def _human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{value}B"


def list_logs() -> list[dict[str, object]]:
    ensure_dirs()
    entries: list[dict[str, object]] = []
    for path in sorted(logs_dir().glob("*.log")):
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append({
            "name": path.stem,
            "path": str(path),
            "bytes": stat.st_size,
            "size_text": _human_size(stat.st_size),
            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
        })
    return entries


def tail_lines(path: Path, lines: int) -> list[str]:
    from collections import deque

    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return list(deque(handle, maxlen=max(1, lines)))
    except OSError:
        return []


def follow(path: Path) -> None:
    """持续输出新增内容，文件被轮转或清空后自动重开。"""
    handle = None
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
        handle.seek(0, os.SEEK_END)
        while True:
            line = handle.readline()
            if line:
                sys.stdout.write(line)
                sys.stdout.flush()
                continue
            time.sleep(0.5)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size < handle.tell():
                handle.close()
                handle = path.open("r", encoding="utf-8", errors="replace")
                handle.seek(0, os.SEEK_END)
    except KeyboardInterrupt:
        sys.stdout.write("\n")
    finally:
        if handle is not None:
            handle.close()
