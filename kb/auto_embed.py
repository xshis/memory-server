"""自动向量补算：在索引/服务生命周期里静默拉起 `kb embed` 补齐缺失嵌入。

- 真正干活的仍是 `kb embed`（幂等、断点续跑、持有 data/embed.lock），本模块只负责时机。
- 每次触发前依次检查：是否已有嵌入在跑（flock）→ 缺口是否够大 → 开关是否打开 → 嵌入后端是否可用。
- 开关：环境变量 KB_AUTO_EMBED=0/1 优先，其次 config.json 的 embed.auto（默认开）。
- 触发点：kb index 完成、watch 一轮扫描/事件增量完成、map/mcp 服务启动兜底。
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

EMBED_LOCK_NAME = "embed.lock"
EMBED_LOG_NAME = "auto-embed.log"
MIN_START_GAP = 1000  # 服务启动兜底：缺口超过这个数才自动补，避免频繁小跑

_STATS_QUERY = (
    "SELECT (SELECT COUNT(*) FROM files WHERE content_chars > 0) AS embeddable, "
    "(SELECT COUNT(*) FROM file_embeddings) AS embedded"
)


def embed_running(kb_home: Path) -> bool:
    """用 data/embed.lock 判断是否有嵌入进程在跑。flock 随进程退出自动释放，无残留问题。"""
    from .index_lock import IndexLock

    lock = IndexLock(Path(kb_home) / "data" / EMBED_LOCK_NAME)
    if lock.acquire():
        lock.release()
        return False
    return True


def embedding_brief(db_path: Path) -> dict[str, Any]:
    """{embeddable, embedded, gap, running}，供状态接口/触发判断；库不可读时返回全 0。"""
    empty: dict[str, Any] = {"embeddable": 0, "embedded": 0, "gap": 0, "running": False}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(_STATS_QUERY).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return empty
    if row is None:
        return empty
    embeddable = max(0, int(row[0] or 0))
    embedded = max(0, int(row[1] or 0))
    return {
        "embeddable": embeddable,
        "embedded": min(embedded, embeddable) if embeddable else embedded,
        "gap": max(0, embeddable - embedded),
        "running": embed_running(db_path.parent.parent),
    }


def _auto_enabled() -> bool:
    """总开关：环境变量 KB_AUTO_EMBED 优先，其次 config.json 的 embed.auto（默认开）。"""
    env_value = os.environ.get("KB_AUTO_EMBED", "").strip().lower()
    if env_value in {"0", "false", "off", "no"}:
        return False
    if env_value in {"1", "true", "on", "yes"}:
        return True
    try:
        from .config import load_config

        embed_config = load_config().get("embed") or {}
        return bool(embed_config.get("auto", True))
    except Exception:  # noqa: BLE001
        return True


def _backend_available() -> bool:
    try:
        from .embedder import fastembed_available

        if fastembed_available():
            return True
        from . import llm

        return bool(llm.status().get("running"))
    except Exception:  # noqa: BLE001
        return False


def maybe_auto_embed(kb_home: Path, *, reason: str, gap_threshold: int = 1) -> bool:
    """缺口 ≥ gap_threshold 就在后台拉起 `kb embed`（输出写 logs/auto-embed.log）。返回是否拉起。"""
    kb_home = Path(kb_home)
    db_path = kb_home / "data" / "kb.db"
    if not db_path.is_file() or embed_running(kb_home):
        return False
    if embedding_brief(db_path)["gap"] < max(1, int(gap_threshold)):
        return False
    if not _auto_enabled() or not _backend_available():
        return False
    logs_dir = kb_home / "logs"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        handle = (logs_dir / EMBED_LOG_NAME).open("a", encoding="utf-8")
    except OSError:
        return False
    handle.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} 自动补算（{reason}）====\n")
    handle.flush()
    popen_kwargs: dict[str, Any] = {
        "stdout": handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "cwd": str(kb_home),
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    try:
        subprocess.Popen([sys.executable, "-m", "kb", "embed"], **popen_kwargs)
    except OSError:
        handle.close()
        return False
    handle.close()  # 子进程持有副本；父进程不泄露 fd
    return True
