from __future__ import annotations

import hashlib
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import extract
from .config import load_config
from .db import (
    delete_file_by_path,
    delete_missing,
    get_file_by_path,
    list_roots,
    root_for_path,
    upsert_file,
)


@dataclass
class IndexStats:
    scanned: int = 0
    indexed: int = 0
    changed: int = 0
    skipped: int = 0
    removed: int = 0
    too_large: int = 0
    unsupported: int = 0
    errors: int = 0
    started_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "indexed": self.indexed,
            "changed": self.changed,
            "skipped": self.skipped,
            "removed": self.removed,
            "too_large": self.too_large,
            "unsupported": self.unsupported,
            "errors": self.errors,
            "seconds": round(time.time() - self.started_at, 2),
        }


Progress = Callable[[str], None]


def _default_progress(message: str) -> None:
    print(message, flush=True)


def _is_excluded_dir(name: str, exclude_dirs: set[str]) -> bool:
    return name.lower() in exclude_dirs


def _safe_is_symlink(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        return True


def iter_files(root: Path, config: dict) -> tuple[list[Path], int]:
    exclude_dirs = {str(item).lower() for item in config["index"].get("exclude_dirs", [])}
    exclude_globs = list(config["index"].get("exclude_globs", []))
    files: list[Path] = []
    walk_errors = 0

    def on_error(_error: OSError) -> None:
        nonlocal walk_errors
        walk_errors += 1

    for current, dirnames, filenames in os.walk(root, followlinks=False, onerror=on_error):
        current_path = Path(current)
        kept_dirs = []
        for dirname in dirnames:
            if _is_excluded_dir(dirname, exclude_dirs):
                continue
            if _safe_is_symlink(current_path / dirname):
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in filenames:
            path = current_path / filename
            if _safe_is_symlink(path):
                continue
            if extract.is_excluded(path, exclude_globs):
                continue
            if extract.is_sensitive(path):
                continue
            files.append(path)

    return files, walk_errors


def _read_stable(path: Path, retries: int = 2, delay: float = 0.25) -> os.stat_result | None:
    for attempt in range(retries + 1):
        try:
            first = path.stat()
            if attempt < retries:
                time.sleep(delay)
                second = path.stat()
                if first.st_size == second.st_size and first.st_mtime == second.st_mtime:
                    return second
            else:
                return first
        except OSError:
            if attempt >= retries:
                return None
            time.sleep(delay)
    return None


def _process_file(
    conn: sqlite3.Connection,
    root: sqlite3.Row,
    path: Path,
    config: dict,
    run_id: str,
    *,
    full: bool,
    stats: IndexStats,
) -> None:
    existing = get_file_by_path(conn, path)
    try:
        stat = path.stat()
    except OSError:
        stats.errors += 1
        return

    stats.scanned += 1
    if (
        not full
        and existing is not None
        and int(existing["size"]) == stat.st_size
        and abs(float(existing["mtime"]) - stat.st_mtime) < 0.001
        and existing["content_status"] not in ("error", "unknown")
    ):
        conn.execute(
            "UPDATE files SET last_seen_run = ? WHERE id = ?",
            (run_id, int(existing["id"])),
        )
        stats.skipped += 1
        return

    try:
        stable = _read_stable(path)
        if stable is None:
            if existing is not None:
                conn.execute(
                    "UPDATE files SET last_seen_run = ? WHERE id = ?",
                    (run_id, int(existing["id"])),
                )
                conn.commit()
            stats.errors += 1
            return
        stat = stable
    except OSError:
        stats.errors += 1
        return

    root_path = Path(root["path"])
    try:
        rel_path = str(path.relative_to(root_path))
    except ValueError:
        rel_path = path.name
    if (
        not full
        and existing is not None
        and int(existing["size"]) == stat.st_size
        and abs(float(existing["mtime"]) - stat.st_mtime) < 0.001
        and existing["content_status"] not in ("error", "unknown")
    ):
        conn.execute(
            "UPDATE files SET last_seen_run = ? WHERE id = ?",
            (run_id, int(existing["id"])),
        )
        stats.skipped += 1
        return

    max_bytes = int(config["index"].get("max_file_size_mb", 20)) * 1024 * 1024
    max_chars = int(config["index"].get("max_content_chars", 2_000_000))

    content = ""
    status = "indexed"
    error = ""
    if stat.st_size > max_bytes:
        status = "too_large"
        error = f"文件超过 {max_bytes // (1024 * 1024)}MB，只索引文件名"
        stats.too_large += 1
    else:
        text, status, error = extract.extract_text(path)
        if status == "indexed":
            content = extract.normalize_text(text)[:max_chars]
        elif status == "unsupported":
            stats.unsupported += 1
        else:
            stats.errors += 1

    chunks = extract.chunk_text(content) if content else []

    sha = ""
    if stat.st_size <= max_bytes and status == "indexed":
        try:
            with path.open("rb") as handle:
                sha = hashlib.sha256(handle.read()).hexdigest()
        except OSError:
            sha = ""

    upsert_file(
        conn,
        root_id=int(root["id"]),
        path=path,
        rel_path=rel_path,
        name=path.name,
        ext=path.suffix.lower(),
        size=stat.st_size,
        mtime=stat.st_mtime,
        ctime=stat.st_ctime,
        content=content,
        content_status=status,
        error=error,
        sha256=sha,
        chunks=chunks,
        last_seen_run=run_id,
    )
    stats.indexed += 1
    if existing is not None:
        stats.changed += 1


# Kept for readability; the real exclusion happens in iter_files.
get_file_by_path_cache = None


def index_roots(
    conn: sqlite3.Connection,
    roots: list[sqlite3.Row] | None = None,
    *,
    full: bool = False,
    progress: Progress | None = None,
) -> IndexStats:
    config = load_config()
    if roots is None:
        roots = list_roots(conn)
    if not roots:
        raise RuntimeError("没有配置索引范围。先运行 `kb scope`。")

    progress = progress or _default_progress
    stats = IndexStats()
    for root in roots:
        root_path = Path(root["path"])
        if not root_path.exists():
            progress(f"[跳过] 路径不存在：{root_path}")
            continue
        if not root_path.is_dir():
            progress(f"[跳过] 不是目录：{root_path}")
            continue

        run_id = uuid.uuid4().hex
        progress(f"[索引] {root_path}")
        try:
            files, walk_errors = iter_files(root_path, config)
        except OSError as exc:
            progress(f"[跳过] 无法读取 {root_path}：{exc}")
            stats.errors += 1
            continue
        for index, path in enumerate(files, 1):
            try:
                _process_file(conn, root, path, config, run_id, full=full, stats=stats)
            except Exception as exc:  # noqa: BLE001
                stats.errors += 1
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                if progress:
                    progress(f"[错误] {path}: {exc}")
            if index % 50 == 0:
                conn.commit()
                progress(
                    f"  已扫描 {index}/{len(files)}，入库 {stats.indexed}，"
                    f"跳过 {stats.skipped}，错误 {stats.errors}"
                )

        conn.commit()
        if walk_errors == 0:
            removed = delete_missing(conn, int(root["id"]), run_id)
            stats.removed += removed
        else:
            progress(f"[注意] {root_path} 有 {walk_errors} 个目录读取失败，本次不清理旧记录")

    progress(
        f"[完成] 扫描 {stats.scanned}，入库 {stats.indexed}，跳过 {stats.skipped}，"
        f"删除 {stats.removed}，过大 {stats.too_large}，不支持 {stats.unsupported}，"
        f"错误 {stats.errors}，耗时 {time.time() - stats.started_at:.1f}s"
    )
    return stats


def index_single_file(conn: sqlite3.Connection, path: str | Path) -> bool:
    config = load_config()
    resolved = Path(path).expanduser().resolve()
    root = root_for_path(conn, resolved)
    if root is None:
        return False
    if not resolved.is_file():
        return delete_file_by_path(conn, resolved)
    stats = IndexStats()
    _process_file(
        conn, root, resolved, config, uuid.uuid4().hex,
        full=False, stats=stats,
    )
    return stats.indexed > 0


def index_tree(conn: sqlite3.Connection, path: str | Path,
               extra_exclude_dirs: set[str] | None = None) -> int:
    """只索引指定目录子树，用于文件事件里的目录新建/移动。"""
    config = load_config()
    if extra_exclude_dirs:
        config = dict(config)
        config["index"] = dict(config["index"])
        current = {str(item).lower() for item in config["index"].get("exclude_dirs", [])}
        config["index"]["exclude_dirs"] = sorted(current | {item.lower() for item in extra_exclude_dirs})
    resolved = Path(path).expanduser().resolve()
    root = root_for_path(conn, resolved)
    if root is None or not resolved.is_dir():
        return 0
    try:
        files, _errors = iter_files(resolved, config)
    except OSError:
        return 0
    stats = IndexStats()
    run_id = uuid.uuid4().hex
    for item in files:
        try:
            _process_file(conn, root, item, config, run_id, full=False, stats=stats)
        except Exception:  # noqa: BLE001
            stats.errors += 1
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
    conn.commit()
    return stats.indexed


def remove_path(conn: sqlite3.Connection, path: str | Path) -> bool:
    return delete_file_by_path(conn, path)
