"""自动向量补算：在索引/服务生命周期里静默拉起 `kb embed` 补齐缺失嵌入。

- 真正干活的仍是 `kb embed`（幂等、断点续跑、持有 data/embed.lock），本模块只负责时机。
- 每次触发前依次检查：是否已有嵌入在跑（flock）→ 缺口是否够大 → 距上次触发是否满 15 分钟（缺口≥3000 豁免）→ 开关是否打开 → 嵌入后端是否可用。
- 触发节奏：data/embed-stamp.json 记录上次拉起时间；嵌入子进程限 OMP_NUM_THREADS=2（默认吃满全核会发烫）。
- 开关：环境变量 KB_AUTO_EMBED=0/1 优先，其次 config.json 的 embed.auto（默认开）。
- 触发点：kb index 完成、watch 一轮扫描/事件增量完成、map/mcp 服务启动兜底。
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

EMBED_LOCK_NAME = "embed.lock"
EMBED_LOG_NAME = "auto-embed.log"
EMBED_STAMP_NAME = "embed-stamp.json"
MIN_START_GAP = 1000  # 服务启动兜底：缺口超过这个数才自动补，避免频繁小跑
MIN_SPAWN_INTERVAL = 900  # 两次自动补算至少间隔 15 分钟：文件持续变动的机器上让缺口累积一起补
BIG_GAP_BYPASS = 3000     # 缺口超过 3000 立即补，不受节流限制

# kb.db 已达 15GB+（files.content 存正文），COUNT(*) 全表扫描在冷缓存/写入期要数十秒，
# 而前端每 5s 轮询一次 status。files.id 是 INTEGER PRIMARY KEY，MAX(id) 走索引 O(log n)，
# 作为 embeddable 估算值足够（顶栏进度徽章用；精确统计用 `kb embed --stats`）。
_STATS_QUERY = (
    "SELECT (SELECT COALESCE(MAX(id),0) FROM files) AS embeddable, "
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


def _read_stamp(kb_home: Path) -> dict[str, Any]:
    try:
        raw = (Path(kb_home) / "data" / EMBED_STAMP_NAME).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_stamp(kb_home: Path, **fields: Any) -> None:
    path = Path(kb_home) / "data" / EMBED_STAMP_NAME
    try:
        data = _read_stamp(kb_home)
        data.update(fields)
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


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
    brief = embedding_brief(db_path)
    if brief["gap"] < max(1, int(gap_threshold)):
        return False
    # 触发节流：文件持续变动的机器上，让缺口累积到下次一起补（曾因此每 1~2 分钟拉起一次
    # 300% CPU 的嵌入进程导致整机发热）。大缺口不受节流限制。
    now = time.time()
    try:
        last_spawn = float(_read_stamp(kb_home).get("last_spawn") or 0)
    except (TypeError, ValueError):
        last_spawn = 0.0
    if now - last_spawn < MIN_SPAWN_INTERVAL and brief["gap"] < BIG_GAP_BYPASS:
        return False
    if not _auto_enabled() or not _backend_available():
        return False
    logs_dir = kb_home / "logs"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        handle = (logs_dir / EMBED_LOG_NAME).open("a", encoding="utf-8")
    except OSError:
        return False
    handle.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} 自动补算（{reason}，缺口 {brief['gap']}）====\n")
    handle.flush()
    popen_kwargs: dict[str, Any] = {
        "stdout": handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "cwd": str(kb_home),
    }
    # 限线程：fastembed/onnxruntime 默认吃满所有核（实测 315%），自动补算限 2 核防发烫
    env = dict(os.environ)
    env.setdefault("OMP_NUM_THREADS", "2")
    env.setdefault("OMP_THREAD_LIMIT", "2")
    popen_kwargs["env"] = env
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
    _write_stamp(kb_home, last_spawn=now, last_reason=reason)
    return True
